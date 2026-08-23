#!/usr/bin/env python3
"""Phase 0b: turn LinkedIn job-alert emails into pipeline jobs.

Salman's LinkedIn alerts use vocabulary the search matrix does not: "business
excellence manager", "PMO & Automation Analyst", "Business Insights & Performance
Manager". That is the point of having them — they surface roles the 13 track queries
miss. Which means marking alert keys in a store is not enough on its own: if the
posting was never fetched, there is nothing in the corpus for the key to mark, and
the 60-point gate has no job to widen. So this script does two things:

  1. **Emits the alert jobs as a portal**, written where Phase 1's aggregation glob
     picks it up. They then flow through dedup, pre-ranking, enrichment, ranking and
     the gate exactly like any other job.
  2. **Writes `job_scraper/alert_matched.json`**, the store `gate_jobs.py` treats as
     the authority for the 60-point gate and `prerank_jobs.py` reads to reserve
     deep-rank slots.

Priority, precisely
-------------------
An alert changes how much *attention* a job gets, never whether it is approved:

  * `prerank_jobs.py` force-includes alert keys up to `prerank.alert_budget`,
    bypassing the score cut. An alert job reaches the ranker even if the pre-rank
    vocabulary scores it zero — LinkedIn matched it against Salman's own alert, so a
    vocabulary miss is the vocabulary's gap, not the job's.
  * `enrich_linkedin.py` spends its detail budget on alert jobs first. They arrive
    with no description at all, so without enrichment the ranker would judge them on
    a bare job title, the weakest evidence in the pipeline.
  * `gate_jobs.py` drafts documents at 60 instead of 75 for a live alert key.

The ranker still judges the job on merit. Salman's own alerts legitimately return
things like "Associate Director, Finance and Operations" and warehouse ops manager
roles; those should reach the ranker and be scored honestly, not be waved through.

Read-only against the mailbox
-----------------------------
The IMAP session selects the label with `readonly=True` and never issues STORE,
COPY, MOVE or EXPUNGE. Nothing is labelled, marked read, archived or deleted. The
credential is the Gmail app password already in `config/automation.json` for sending
the digest — an app password grants IMAP and SMTP together, so reading adds no
secret that was not already there.

Trust boundary
--------------
Alert email bodies are untrusted input, same as any posting. This script extracts
only company, title, location and the numeric LinkedIn job ID, and rebuilds every
URL as `https://www.linkedin.com/jobs/view/<id>`. It never follows a link found in
an email, and never stores the `midToken`/`eid` tracking parameters, which identify
Salman's account.

Failing loudly
--------------
LinkedIn can change its email markup at any time. If that happens, the honest
outcome is a visible failure, not zero alert jobs that look exactly like a quiet
week. So finding messages but parsing no job cards out of them is an error, and any
card that yields no usable title is reported rather than ingested under a guess.

Usage:
    python3 scripts/linkedin_alerts.py \\
        --jobs-out /tmp/jobsearch_portal_linkedin-alert_2026-08-19.json \\
        --store job_scraper/alert_matched.json --today 2026-08-19

    # Parse a saved message instead of connecting (debugging a markup change):
    python3 scripts/linkedin_alerts.py --from-file alert.eml --dry-run
"""

import argparse
import email
import email.utils
import imaplib
import json
import re
import sys
from datetime import date, datetime, timedelta
from email.header import decode_header, make_header
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

REPO = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO / "config" / "automation.json"
DEFAULT_MATRIX = REPO / "config" / "search_matrix.json"
DEFAULT_STORE = REPO / "job_scraper" / "alert_matched.json"

DEFAULT_LABEL = "JobSearch/LinkedIn-Alerts"
DEFAULT_LOOKBACK_DAYS = 3
DEFAULT_IMAP_HOST = "imap.gmail.com"
DEFAULT_IMAP_PORT = 993

# Seconds any single socket operation may block. Not a budget for the whole session —
# the timeout rides on the socket and is re-armed per read, so a slow-but-alive server
# that keeps sending data is never cut off; only a silent one is.
#
# This exists because it was missing once. On 2026-08-22 the 08:00 run reached
# `conn.fetch` and stopped there for six hours: the TCP connection stayed ESTABLISHED
# locally while the peer had stopped answering, and a blocking socket with no timeout
# waits on that forever. The run held its lock, fetched no corpus, ranked nothing and
# sent no digest, and nothing noticed, because a process that is waiting looks exactly
# like a process that is working.
#
# 60s is around six times the whole phase's normal duration (~11s on 2026-08-22 for 81
# jobs across several digests), so it cannot fire on a merely slow mailbox.
IMAP_TIMEOUT_SECONDS = 60

# The numeric job ID inside any LinkedIn job URL. `/comm/` is the path the alert
# emails use; the search pages omit it. Kept in step with aggregate_jobs.py's
# LINKEDIN_JOB_ID, which the corpus keys on — see canonical_job_url().
JOB_URL = re.compile(
    r"^https?://(?:[a-z0-9-]+\.)*linkedin\.com/(?:comm/)?jobs/view/(?:.*?-)?(\d{6,})(?:[/?#]|$)",
    re.IGNORECASE,
)

# LinkedIn separates company from location with U+00B7 in the card's caption line
# ("RED Global · Budapest, Hungary (Hybrid)"). That line is the authority for both:
# the anchors around it are the company logo and the job title, and which is which
# is only decidable by comparing against this.
CAPTION = "·"

# Trailing work-mode marker on the caption's location half.
WORK_MODE = re.compile(r"\s*\((On-site|Onsite|Remote|Hybrid)\)\s*$", re.IGNORECASE)


def canonical_job_url(href: str) -> tuple:
    """(job_id, canonical_url) for a LinkedIn job link, or (None, None).

    Returns the plain `linkedin.com/jobs/view/<id>` form so that:
      * `aggregate_jobs.make_dedup_key` produces `url:linkedin:<id>`, the same key the
        search pages produce for this posting — without which the alert key could
        never join the job and the 60-point gate would never fire;
      * `midToken` and `eid`, which identify Salman's LinkedIn account, are dropped
        rather than written into a JSON file.
    """
    href = (href or "").strip()
    if not href:
        return None, None

    match = JOB_URL.match(href)
    if not match:
        # Click wrappers carry the real destination in a query parameter. Unwrap once;
        # a wrapper pointing at another wrapper is not a shape LinkedIn uses and is
        # not worth recursing into.
        try:
            params = parse_qs(urlparse(href).query)
        except ValueError:
            return None, None
        for name in ("url", "u", "target"):
            for candidate in params.get(name, []):
                match = JOB_URL.match(unquote(candidate).strip())
                if match:
                    break
            if match:
                break
    if not match:
        return None, None

    job_id = match.group(1)
    return job_id, f"https://www.linkedin.com/jobs/view/{job_id}"


class _Tokenizer(HTMLParser):
    """Flatten an alert email into ('link', href, text) and ('text', None, text) tokens.

    A DOM walk would be tighter but far more brittle: LinkedIn's alert markup is
    nested tables whose structure changes without notice, while the *order* of
    "logo link, title link, caption text" has been stable. Reading a flat token
    stream depends only on that order.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tokens = []
        self._href = None
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._flush_text()
            self._href = dict(attrs).get("href")
            self._buf = []
        elif tag == "img" and self._href is not None:
            # The company-logo anchor has no text, only an image; its alt attribute
            # is the company name and is sometimes the only place it appears.
            alt = (dict(attrs).get("alt") or "").strip()
            if alt:
                self._buf.append(alt)
        elif tag in ("br", "tr", "td", "p", "div", "table"):
            self._flush_text()

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.tokens.append(("link", self._href, " ".join(self._buf).strip()))
            self._href, self._buf = None, []
        elif tag in ("tr", "td", "p", "div", "table"):
            self._flush_text()

    def handle_data(self, data):
        text = re.sub(r"\s+", " ", data)
        if text.strip():
            self._buf.append(text.strip())

    def _flush_text(self):
        if self._href is None and self._buf:
            self.tokens.append(("text", None, " ".join(self._buf).strip()))
            self._buf = []

    def close(self):
        super().close()
        self._flush_text()
        return self.tokens


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def split_caption(text: str) -> tuple:
    """'RED Global · Budapest, Hungary (Hybrid)' -> (company, location, work_mode)."""
    company, _, rest = text.partition(CAPTION)
    location = rest.strip()
    mode_match = WORK_MODE.search(location)
    work_mode = None
    if mode_match:
        work_mode = mode_match.group(1)
        work_mode = "On-site" if work_mode.lower() == "onsite" else work_mode
        location = location[:mode_match.start()].strip()
    return company.strip(), location or None, work_mode


def find_alert_name(text: str, alert_names: list) -> str:
    """The configured alert name this heading refers to, or None.

    Matching against names from `alerts.track_map` rather than parsing LinkedIn's
    heading phrasing ("Your job alert for X", "X jobs in Ireland", or a bare "X")
    keeps attribution stable across markup changes, and puts it under Salman's
    control: the config names mirror the alerts he actually created.
    """
    haystack = _norm(text)
    if not haystack:
        return None
    # Longest first, so "business excellence manager" is not shadowed by a shorter
    # configured name that happens to be a substring of it.
    for name in sorted(alert_names, key=lambda n: -len(_norm(n))):
        needle = _norm(name)
        if needle and needle in haystack:
            return name
    return None


def parse_cards(html: str, alert_names: list) -> tuple:
    """(cards, unparsed) from one alert email's HTML body.

    One email carries several alerts: the subject names one, then "New jobs from your
    other alerts" rolls up the rest. Attribution therefore follows the most recent
    heading in the token stream, never the subject line — keying off the subject would
    file every job in a digest under whichever alert happened to be named there.
    """
    tokenizer = _Tokenizer()
    tokenizer.feed(html)
    tokens = tokenizer.close()

    cards, unparsed = [], []
    state = {"alert": None}
    group = None

    def finish(group):
        """Turn one run of same-job-ID tokens into a card."""
        if group is None:
            return
        company = location = work_mode = None
        for text in group["texts"]:
            if CAPTION in text:
                company, location, work_mode = split_caption(text)
                break

        # The title is the anchor text that is not the company name. Without a caption
        # there is nothing to disambiguate against, so the longest anchor text is the
        # best available guess — the company logo's alt text is typically shorter.
        anchors = [a for a in group["anchors"] if a]
        title = None
        if company:
            for anchor in anchors:
                if _norm(anchor) != _norm(company):
                    title = anchor
                    break
        if not title and anchors:
            title = max(anchors, key=len)

        if not title:
            unparsed.append({"job_id": group["job_id"],
                             "reason": "no anchor text usable as a job title",
                             "texts": group["texts"][:3]})
            return

        cards.append({
            "job_id": group["job_id"],
            "url": group["url"],
            "title": title,
            "company": company or None,
            "location": location,
            "work_mode": work_mode,
            "alert_name": group["alert_name"],
        })

    for kind, href, text in tokens:
        if kind == "text":
            name = find_alert_name(text, alert_names)
            if name:
                state["alert"] = name
            if group is not None:
                group["texts"].append(text)
            continue

        job_id, url = canonical_job_url(href)
        if not job_id:
            # A non-job link ("See all jobs", "Manage alerts", "Edit alert", the
            # LinkedIn logo) ends the current card, and may itself name an alert.
            name = find_alert_name(text, alert_names)
            if name:
                state["alert"] = name
            finish(group)
            group = None
            continue

        if group is None or group["job_id"] != job_id:
            finish(group)
            group = {"job_id": job_id, "url": url, "anchors": [],
                     "texts": [], "alert_name": state["alert"]}
        group["anchors"].append(text)

    finish(group)
    return cards, unparsed


def html_body(msg) -> str:
    """The message's HTML part, or its plain-text part as a fallback."""
    html_parts, text_parts = [], []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            decoded = payload.decode(charset, errors="replace")
        except LookupError:
            decoded = payload.decode("utf-8", errors="replace")
        if part.get_content_type() == "text/html":
            html_parts.append(decoded)
        elif part.get_content_type() == "text/plain":
            text_parts.append(decoded)
    return "\n".join(html_parts or text_parts)


def header(msg, name: str) -> str:
    raw = msg.get(name)
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except (UnicodeDecodeError, LookupError, ValueError):
        return str(raw)


def message_date(msg, fallback: date) -> date:
    """The message's Date header as a date, falling back to `fallback`.

    Used as the job's date because an alert card carries no posting date. It is an
    upper bound: the posting existed on or before the day LinkedIn emailed about it.
    """
    raw = msg.get("Date")
    if raw:
        try:
            parsed = email.utils.parsedate_to_datetime(raw)
            if parsed is not None:
                return parsed.date()
        except (TypeError, ValueError):
            pass
    return fallback


def fetch_messages(host, port, user, password, label, since, warn) -> list:
    """Alert messages newer than `since`. Read-only: no STORE, COPY, MOVE or EXPUNGE."""
    messages = []
    try:
        # `timeout=` here covers the whole session, not just the connect. CPython builds
        # the socket with `socket.create_connection(address, timeout)` and never clears
        # it, so the same deadline applies to login, select, search and every fetch —
        # which matters, because the 2026-08-22 hang was in `fetch`, long after a
        # connect-only timeout would have stopped watching.
        #
        # Scoped to this socket rather than set with socket.setdefaulttimeout(), which
        # is process-global: this script is also imported by its tests, and a global
        # default would silently follow the module into any other caller's sockets.
        conn = imaplib.IMAP4_SSL(host, port, timeout=IMAP_TIMEOUT_SECONDS)
    except (OSError, imaplib.IMAP4.error) as exc:
        raise RuntimeError(f"could not reach {host}:{port} — {exc}") from exc

    try:
        try:
            conn.login(user, password)
        except imaplib.IMAP4.error as exc:
            # Never echo the credential. Gmail rejects a normal account password here;
            # the app password from config/automation.json is what works.
            raise RuntimeError(
                f"IMAP login failed for {user} — check that email.smtp_password in "
                f"config/automation.json is a Gmail app password and that IMAP is "
                f"enabled in Gmail settings ({exc})") from exc

        status, _ = conn.select(f'"{label}"', readonly=True)
        if status != "OK":
            raise RuntimeError(
                f"could not open the label {label!r}. Gmail exposes labels as IMAP "
                f"mailboxes, so a nested label is spelled 'Parent/Child' exactly as it "
                f"appears in Gmail, and IMAP access must be on in Gmail settings")

        status, data = conn.search(None, "SINCE", since.strftime("%d-%b-%Y"))
        if status != "OK":
            raise RuntimeError(f"IMAP search failed: {status}")

        for num in (data[0] or b"").split():
            status, payload = conn.fetch(num, "(RFC822)")
            if status != "OK" or not payload:
                warn(f"could not fetch message {num.decode()} — skipped")
                continue
            raw = next((part[1] for part in payload
                        if isinstance(part, tuple) and isinstance(part[1], bytes)), None)
            if raw is None:
                warn(f"message {num.decode()} returned no body — skipped")
                continue
            # RFC822 returns the whole message, so Gmail's web-view "[Message clipped]"
            # truncation does not apply here: digests listing many alerts arrive whole.
            messages.append(email.message_from_bytes(raw))
    except TimeoutError as exc:
        # Reached only once the timeout above fires mid-session. Converted rather than
        # left to propagate because `main()` handles RuntimeError by printing one line
        # and returning 1, which is what the runner reads to degrade the run to
        # search-only. A bare TimeoutError is an OSError, escapes that handler, and
        # turns a recoverable mailbox stall into a traceback in the log.
        raise RuntimeError(
            f"the mailbox stopped responding mid-session and the "
            f"{IMAP_TIMEOUT_SECONDS}s socket timeout fired ({exc or 'no data'}). "
            f"{len(messages)} message(s) had been read; the run continues without "
            f"today's alerts rather than waiting on a dead connection") from exc
    finally:
        try:
            conn.logout()
        except (imaplib.IMAP4.error, OSError):
            pass
    return messages


def load_json(path: Path, label: str, warn):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        warn(f"{label} could not be read ({exc})")
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="automation.json; supplies the mailbox credential.")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX,
                        help="search_matrix.json; supplies the `alerts` block.")
    parser.add_argument("--jobs-out", type=Path,
                        help="Portal JSON for aggregate_jobs.py to pick up.")
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE,
                        help="alert_matched.json. Merged, never truncated.")
    parser.add_argument("--from-file", type=Path, action="append", default=[],
                        help="Parse a saved .eml instead of connecting. Repeatable.")
    parser.add_argument("--label", default=None, help="Override alerts.label.")
    parser.add_argument("--lookback-days", type=int, default=None,
                        help="Override alerts.lookback_days.")
    parser.add_argument("--today", default=None, help="YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and report; write nothing.")
    args = parser.parse_args()

    warnings = []

    def warn(message):
        warnings.append(message)
        print(f"WARNING: {message}", file=sys.stderr)

    try:
        today = (datetime.strptime(args.today, "%Y-%m-%d").date()
                 if args.today else date.today())
    except ValueError:
        print(f"Error: --today {args.today!r} is not YYYY-MM-DD", file=sys.stderr)
        return 2

    matrix = load_json(args.matrix, "search_matrix.json", warn) or {}
    cfg = matrix.get("alerts") or {}
    if not cfg.get("enabled", True):
        print(json.dumps({"enabled": False, "messages": 0, "cards": 0,
                          "warnings": warnings}, indent=2))
        return 0

    track_map = cfg.get("track_map") or {}
    alert_names = list(track_map)
    if not alert_names:
        warn("alerts.track_map in config/search_matrix.json is empty, so no job can "
             "be attributed to an alert or a Profile Track. Add one entry per alert "
             "created in LinkedIn, spelled as LinkedIn spells it.")

    label = args.label or cfg.get("label") or DEFAULT_LABEL
    lookback = args.lookback_days or cfg.get("lookback_days") or DEFAULT_LOOKBACK_DAYS
    since = today - timedelta(days=lookback)

    # === Collect messages ===
    messages = []
    if args.from_file:
        for path in args.from_file:
            try:
                messages.append(email.message_from_bytes(path.read_bytes()))
            except OSError as exc:
                warn(f"could not read {path} ({exc})")
    else:
        automation = load_json(args.config, "automation.json", warn) or {}
        mail = automation.get("email") or {}
        imap_cfg = automation.get("imap") or {}
        # The Gmail app password already in the email block covers IMAP and SMTP
        # together, so reading the alerts introduces no new credential. An explicit
        # `imap` block overrides it for anyone wanting a separate one.
        user = imap_cfg.get("user") or mail.get("smtp_user")
        password = imap_cfg.get("password") or mail.get("smtp_password")
        if not user or not password:
            print("Error: no mailbox credential. Set email.smtp_user and "
                  "email.smtp_password (a Gmail app password) in "
                  f"{args.config}, or an explicit imap.user / imap.password.",
                  file=sys.stderr)
            return 1
        try:
            messages = fetch_messages(
                imap_cfg.get("host") or cfg.get("imap_host") or DEFAULT_IMAP_HOST,
                int(imap_cfg.get("port") or cfg.get("imap_port") or DEFAULT_IMAP_PORT),
                user, password, label, since, warn)
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    # === Parse ===
    jobs, unparsed_all, per_alert = {}, [], {}
    for msg in messages:
        body = html_body(msg)
        if not body:
            warn(f"message {header(msg, 'Subject')!r} had no readable body")
            continue
        posted = message_date(msg, today)
        cards, unparsed = parse_cards(body, alert_names)
        unparsed_all.extend(unparsed)
        for card in cards:
            bucket = card["alert_name"] or "(unattributed)"
            per_alert[bucket] = per_alert.get(bucket, 0) + 1
            if not card["alert_name"]:
                warn(f"{card['title']!r} at {card['company'] or 'an unnamed company'} "
                     f"could not be attributed to a configured alert — ingesting it "
                     f"with no track rather than dropping it")
            # Keep the first sighting of a job ID: an earlier digest dates the alert
            # correctly, and the 30-day expiry runs from when it first appeared.
            jobs.setdefault(card["job_id"], {**card, "date": posted.isoformat()})

    if messages and not jobs:
        print(f"Error: read {len(messages)} alert message(s) from {label!r} but parsed "
              f"no job cards out of them ({len(unparsed_all)} card(s) had no usable "
              f"title). That is almost certainly a LinkedIn markup change, not an "
              f"empty week — save one message and re-run with --from-file to see what "
              f"it produces. Failing so this cannot look like a quiet day.",
              file=sys.stderr)
        return 1

    # === Portal JSON, in the shape aggregate_jobs.normalize_job reads ===
    results = [{
        "title": card["title"],
        "company": card["company"],
        "url": card["url"],
        "location": card["location"],
        "date": card["date"],
        # No description: the alert card has none. enrich_linkedin.py fills it, and
        # prioritizes these jobs precisely because the field is empty.
        "description": None,
        "work_mode": card["work_mode"],
        "alert_name": card["alert_name"],
        "alert_track": track_map.get(card["alert_name"]),
    } for card in jobs.values()]

    # === Alert store, keyed exactly as aggregate_jobs.make_dedup_key will key it ===
    store = load_json(args.store, "alert_matched.json", warn)
    if not isinstance(store, dict):
        if store is not None:
            warn("alert_matched.json was not an object — starting a fresh store")
        store = {}
    new_keys = 0
    for card in jobs.values():
        key = f"url:linkedin:{card['job_id']}"
        existing = store.get(key)
        if isinstance(existing, dict) and existing.get("first_alerted"):
            # first_alerted is never moved forward. Re-reading the same digest on a
            # later day must not renew a job's 30-day window indefinitely.
            existing["last_seen"] = today.isoformat()
            continue
        store[key] = {
            "first_alerted": card["date"],
            "last_seen": today.isoformat(),
            "alert_name": card["alert_name"],
            "track": track_map.get(card["alert_name"]),
            "title": card["title"],
            "company": card["company"],
            "source": "linkedin-alert",
        }
        new_keys += 1

    summary = {
        "label": label,
        "since": since.isoformat(),
        "messages": len(messages),
        "cards": len(jobs),
        "new_store_keys": new_keys,
        "store_size": len(store),
        "per_alert": per_alert,
        "unattributed": per_alert.get("(unattributed)", 0),
        "unparsed_cards": len(unparsed_all),
        "dry_run": bool(args.dry_run),
        "warnings": warnings,
    }

    if not args.dry_run:
        if args.jobs_out:
            args.jobs_out.parent.mkdir(parents=True, exist_ok=True)
            args.jobs_out.write_text(json.dumps(
                {"meta": {"source": "linkedin-alert", "label": label,
                          "messages": len(messages), "unique": len(results)},
                 "results": results}, indent=2))
        args.store.parent.mkdir(parents=True, exist_ok=True)
        args.store.write_text(json.dumps(store, indent=2, sort_keys=True))

    print(json.dumps(summary, indent=2))
    print(f"Alerts: {len(messages)} message(s) -> {len(jobs)} job(s), "
          f"{new_keys} new store key(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
