#!/usr/bin/env python3
"""Tests for scripts/linkedin_alerts.py — Phase 0b, the LinkedIn alert reader.

Phase 0b reads Salman's own LinkedIn job-alert emails and feeds them into the
pipeline as a portal. It is a job *source*, not just a marker file, and that is the
premise these tests defend: the alerts use vocabulary the 13 track queries do not
("business excellence manager", "PMO & Automation Analyst"), so most alerted
postings were never fetched by a search at all. A design that only flagged
already-fetched jobs would have almost nothing to flag.

Three properties are pinned hardest, because breaking any of them fails silently —
the pipeline keeps running and simply stops giving alerts priority:

  1. An alert URL must key to the same dedup key a search page produces for the same
     posting. Alert emails link `/comm/jobs/view/<id>?midToken=…`; the corpus keys on
     `url:linkedin:<id>`. Get this wrong and every alert key forks away from the
     corpus, so gate_jobs.py's alert-matched 60 tier never fires and nothing anywhere
     reports a problem.
  2. Attribution follows the heading above each card, never the subject line. One
     digest carries several alerts ("New jobs from your other alerts"), so keying off
     the subject files every job in the email under one alert.
  3. Messages found but no cards parsed is an error, not an empty result. A LinkedIn
     markup change must not be indistinguishable from a quiet week.

Also pinned: no `midToken`/`eid` — which identify Salman's LinkedIn account — may
reach any output file, `first_alerted` must never move forward (re-reading the same
digest daily cannot renew a job's 30-day window forever), and the mailbox is only
ever opened read-only.

No test here touches the network. `--from-file` parses saved messages, so
fetch_messages() is never entered and no credential is read.
"""

import html
import importlib.util
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import date
from email.message import EmailMessage
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "linkedin_alerts.py"

_spec = importlib.util.spec_from_file_location("linkedin_alerts", SCRIPT)
la = importlib.util.module_from_spec(_spec)
sys.modules["linkedin_alerts"] = la
_spec.loader.exec_module(la)

# The corpus keying this module has to agree with. Imported rather than
# reimplemented: a second copy of the key logic would drift, and the join would
# break without any test noticing.
_agg_spec = importlib.util.spec_from_file_location(
    "aggregate_jobs", REPO / "scripts" / "aggregate_jobs.py")
agg = importlib.util.module_from_spec(_agg_spec)
sys.modules["aggregate_jobs"] = agg
_agg_spec.loader.exec_module(agg)

RUNNER = (REPO / "scripts" / "run_daily.sh").read_text()
MATRIX = json.loads((REPO / "config" / "search_matrix.json").read_text())

# Salman's actual configured alert names, copied from config/search_matrix.json.
# Copied rather than loaded so a config edit cannot quietly weaken the parser tests;
# PipelineWiring separately asserts the real config still parses the real digest.
ALERT_NAMES = [
    "supply chain and ai",
    "PMO & Automation Analyst - Technology",
    "Manager Business Artificial Intelligence",
    "Business Insights & Performance Manager",
    "AI Business Analyst",
    "business excellence manager",
]

# Stands in for the real account-identifying token in the alert links.
MIDTOKEN = "AQEFAKEtokenFAKE"


def card_html(job_id, title, company, caption):
    """One job card in the shape LinkedIn mails them: logo anchor, title anchor, caption.

    Both anchors point at the same `/comm/` URL carrying the tracking parameters, and
    the company name arrives only as the logo's `alt` — which is why the parser reads
    img alt attributes at all.
    """
    href = (f"https://www.linkedin.com/comm/jobs/view/{job_id}"
            f"?midToken={MIDTOKEN}&amp;eid=fake-eid&amp;trackingId=fakeTrack")
    return f"""
      <table><tr>
        <td><a href="{href}"><img src="logo.png" alt="{html.escape(company)}"/></a></td>
        <td>
          <a href="{href}">{html.escape(title)}</a>
          <p>{html.escape(caption)}</p>
        </td>
      </tr></table>"""


def digest_html(sections):
    """A multi-alert digest: [(heading, [(job_id, title, company, caption), ...]), ...].

    Mirrors the layout of the email Salman forwarded: the first alert is introduced
    with "Your job alert for X", the rest roll up under "New jobs from your other
    alerts", and each block ends with a non-job link.
    """
    blocks = []
    for index, (heading, cards) in enumerate(sections):
        if index == 0:
            blocks.append(f"<p>Your job alert for {html.escape(heading)}</p>"
                          f"<p>{len(cards)} new jobs match your alert. "
                          f"<a href='https://www.linkedin.com/comm/jobs/alerts/'>"
                          f"Manage alerts</a></p>")
        else:
            if index == 1:
                blocks.append("<p>New jobs from your other alerts</p>")
            blocks.append(f"<p>{html.escape(heading)}</p>")
        blocks.extend(card_html(*card) for card in cards)
        blocks.append("<p><a href='https://www.linkedin.com/comm/jobs/search/'>"
                      "See all jobs</a></p>")
    return "<html><body>" + "".join(blocks) + "</body></html>"


# The digest Salman actually received on 2026-08-18, with synthetic job IDs. Three
# alerts in one email, 3/2/2 — the case that makes subject-line attribution wrong.
REAL_DIGEST = [
    ("Business Insights & Performance Manager", [
        ("4123456781", "Process Manager", "RED Global",
         "RED Global · Budapest, Hungary (Hybrid)"),
        ("4123456782", "Continuous Improvement Manager", "Xylem",
         "Xylem · Budapest (Hybrid)"),
        ("4123456783", "Associate Director, Finance and Operations, fixed term contract",
         "IQVIA", "IQVIA · Budapest (Hybrid)"),
    ]),
    ("business excellence manager jobs in Ireland", [
        ("4123456784", "Operations Manager , FC Operations, FC Operations", "Amazon",
         "Amazon · Dublin"),
        ("4123456785", "Budget & Supply Governance Partner, Business & Partnership "
         "Management - T&S", "TikTok", "TikTok · Dublin (On-site)"),
    ]),
    ("Manager Business Artificial Intelligence", [
        ("4123456786", "Manager, Technical Program Management", "Mastercard",
         "Mastercard · Budapest (Hybrid)"),
        ("4123456787", "Machine Learning Engineering Manager", "Signifyd",
         "Signifyd · Budapest (Hybrid)"),
    ]),
]


def eml(body_html, subject="Your job alert", when="Tue, 18 Aug 2026 06:12:00 +0000"):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "LinkedIn <jobalerts-noreply@linkedin.com>"
    msg["Date"] = when
    msg.set_content("plain text fallback")
    msg.add_alternative(body_html, subtype="html")
    return msg


def run_cli(messages, store=None, today="2026-08-19", track_map=None, extra=()):
    """Run the script over saved messages in a scratch dir. Returns (proc, jobs, store).

    Never connects: --from-file short-circuits fetch_messages, so no credential is
    read and no mailbox is opened.
    """
    tmp = Path(tempfile.mkdtemp(prefix="alerts_test_"))
    for index, msg in enumerate(messages):
        (tmp / f"m{index}.eml").write_bytes(msg.as_bytes())

    store_path = tmp / "alert_matched.json"
    if store is not None:
        store_path.write_text(json.dumps(store))
    jobs_out = tmp / "portal.json"

    if track_map is None:
        track_map = {name: "T5_process_perf" for name in ALERT_NAMES}
    matrix = tmp / "matrix.json"
    matrix.write_text(json.dumps({"alerts": {"enabled": True, "track_map": track_map}}))

    cmd = [sys.executable, str(SCRIPT), "--matrix", str(matrix),
           "--jobs-out", str(jobs_out), "--store", str(store_path),
           "--today", today, *extra]
    for index in range(len(messages)):
        cmd += ["--from-file", str(tmp / f"m{index}.eml")]
    # main() only skips its IMAP branch when --from-file is present, so a call with no
    # messages and no explicit --from-file would connect to Salman's real mailbox. It
    # cost one live read before this guard existed; no test may reach the account.
    if "--from-file" not in cmd:
        raise AssertionError(
            "run_cli would connect to the live mailbox: pass at least one message, or "
            "an explicit --from-file in `extra`")
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO))
    return proc, jobs_out, store_path


def results_of(jobs_out):
    return json.loads(jobs_out.read_text())["results"]


class CanonicalUrl(unittest.TestCase):
    """Property 1: the key join. Silent failure if broken."""

    def test_the_alert_url_shape_yields_the_corpus_key(self):
        # The bug this closes: alert emails link through /comm/, and a regex requiring
        # a bare /jobs/view/ keys them as an opaque URL instead of by job ID.
        job_id, url = la.canonical_job_url(
            f"https://www.linkedin.com/comm/jobs/view/4123456789"
            f"?midToken={MIDTOKEN}&eid=x&trackingId=y")
        self.assertEqual(job_id, "4123456789")
        self.assertEqual(url, "https://www.linkedin.com/jobs/view/4123456789")

    def test_every_url_shape_collapses_to_one_corpus_key(self):
        """A posting reached four ways must be one job, not four."""
        shapes = [
            f"https://www.linkedin.com/comm/jobs/view/4123456789?midToken={MIDTOKEN}",
            "https://www.linkedin.com/jobs/view/4123456789",
            "https://hu.linkedin.com/jobs/view/process-manager-at-red-global-4123456789",
            "https://www.linkedin.com/jobs/view/4123456789/?refId=abc",
        ]
        keys = set()
        for href in shapes:
            _, canonical = la.canonical_job_url(href)
            self.assertIsNotNone(canonical, href)
            keys.add(agg.make_dedup_key({"url": canonical}))
        self.assertEqual(keys, {"url:linkedin:4123456789"})

    def test_a_click_wrapper_is_unwrapped(self):
        inner = "https%3A%2F%2Fwww.linkedin.com%2Fcomm%2Fjobs%2Fview%2F4123456789"
        job_id, url = la.canonical_job_url(
            f"https://www.linkedin.com/comm/l/?url={inner}&midToken={MIDTOKEN}")
        self.assertEqual(job_id, "4123456789")
        self.assertEqual(url, "https://www.linkedin.com/jobs/view/4123456789")

    def test_non_job_links_are_rejected(self):
        for href in ["https://www.linkedin.com/comm/jobs/search/?keywords=x",
                     "https://www.linkedin.com/comm/jobs/alerts/",
                     "https://www.linkedin.com/feed/",
                     "https://example.com/jobs/view/4123456789",
                     "mailto:someone@example.com", "", None]:
            self.assertEqual(la.canonical_job_url(href), (None, None), repr(href))

    def test_a_short_number_is_not_a_job_id(self):
        # Guards against keying off a tracking counter or a truncated ID.
        self.assertEqual(
            la.canonical_job_url("https://www.linkedin.com/jobs/view/1234"),
            (None, None))

    def test_the_regex_stays_in_step_with_the_aggregator(self):
        """Two copies of this pattern exist; drift between them breaks the join."""
        self.assertEqual(la.JOB_URL.pattern, agg.LINKEDIN_JOB_ID.pattern)


class Caption(unittest.TestCase):
    """The `·` line is the only authority for which anchor is the company."""

    def test_company_location_and_work_mode(self):
        self.assertEqual(
            la.split_caption("RED Global · Budapest, Hungary (Hybrid)"),
            ("RED Global", "Budapest, Hungary", "Hybrid"))

    def test_on_site_spellings_normalize(self):
        self.assertEqual(la.split_caption("TikTok · Dublin (On-site)")[2], "On-site")
        self.assertEqual(la.split_caption("TikTok · Dublin (Onsite)")[2], "On-site")

    def test_no_work_mode_leaves_the_location_whole(self):
        self.assertEqual(la.split_caption("Amazon · Dublin"), ("Amazon", "Dublin", None))

    def test_a_company_name_containing_a_comma_survives(self):
        company, location, _ = la.split_caption("Smith, Jones & Co · Vienna, Austria")
        self.assertEqual(company, "Smith, Jones & Co")
        self.assertEqual(location, "Vienna, Austria")

    def test_a_line_with_no_separator_yields_no_location(self):
        self.assertEqual(la.split_caption("Just a company"),
                         ("Just a company", None, None))


class AlertNameMatching(unittest.TestCase):
    """Attribution is anchored to Salman's config, not to LinkedIn's phrasing."""

    def test_the_three_heading_phrasings_all_match(self):
        for heading in ["Your job alert for Business Insights & Performance Manager",
                        "Business Insights & Performance Manager",
                        "Business Insights & Performance Manager jobs in Hungary"]:
            self.assertEqual(la.find_alert_name(heading, ALERT_NAMES),
                             "Business Insights & Performance Manager", heading)

    def test_the_longest_name_wins_so_a_substring_cannot_shadow_it(self):
        # None of the six configured names currently sits inside another, but a short
        # one added later would. Sorting by length is what keeps the more specific
        # alert from being stolen by the vaguer one.
        self.assertEqual(
            la.find_alert_name("business excellence manager jobs in Ireland",
                               ALERT_NAMES + ["manager"]),
            "business excellence manager")

    def test_punctuation_and_case_are_ignored_but_words_are_not(self):
        self.assertEqual(
            la.find_alert_name("pmo & automation analyst - technology", ALERT_NAMES),
            "PMO & Automation Analyst - Technology")
        self.assertIsNone(
            la.find_alert_name("PMO and Automation Analyst  Technology", ALERT_NAMES),
            "'and' is a word, not punctuation — it must not match '&'")

    def test_boilerplate_matches_nothing(self):
        for text in ["See all jobs", "Manage alerts", "Unsubscribe", "", None]:
            self.assertIsNone(la.find_alert_name(text, ALERT_NAMES), repr(text))


class ParseCards(unittest.TestCase):
    """Property 2: attribution follows headings, not the subject."""

    def setUp(self):
        self.cards, self.unparsed = la.parse_cards(digest_html(REAL_DIGEST), ALERT_NAMES)

    def test_every_card_in_the_digest_is_found(self):
        self.assertEqual(len(self.cards), 7)
        self.assertEqual(self.unparsed, [])

    def test_cards_are_attributed_to_the_heading_above_them(self):
        by_alert = {}
        for card in self.cards:
            by_alert.setdefault(card["alert_name"], []).append(card["company"])
        self.assertEqual(by_alert, {
            "Business Insights & Performance Manager": ["RED Global", "Xylem", "IQVIA"],
            "business excellence manager": ["Amazon", "TikTok"],
            "Manager Business Artificial Intelligence": ["Mastercard", "Signifyd"],
        })

    def test_the_subject_line_does_not_decide_attribution(self):
        """The failure mode this guards: all seven filed under one alert."""
        self.assertEqual(len({c["alert_name"] for c in self.cards}), 3)

    def test_titles_survive_commas_ampersands_and_dashes(self):
        titles = {c["title"] for c in self.cards}
        self.assertIn("Associate Director, Finance and Operations, fixed term contract",
                      titles)
        self.assertIn("Budget & Supply Governance Partner, Business & Partnership "
                      "Management - T&S", titles)

    def test_the_title_is_never_the_company_logo(self):
        # Both anchors in a card point at the same URL; only the caption distinguishes
        # the logo's alt text from the job title.
        for card in self.cards:
            self.assertNotEqual(la._norm(card["title"]), la._norm(card["company"]),
                                f"logo alt mistaken for the title: {card}")

    def test_location_and_work_mode_come_off_the_caption(self):
        red = next(c for c in self.cards if c["company"] == "RED Global")
        self.assertEqual((red["location"], red["work_mode"]),
                         ("Budapest, Hungary", "Hybrid"))
        amazon = next(c for c in self.cards if c["company"] == "Amazon")
        self.assertEqual((amazon["location"], amazon["work_mode"]), ("Dublin", None))

    def test_the_url_is_canonicalized_at_parse_time(self):
        for card in self.cards:
            self.assertEqual(card["url"],
                             f"https://www.linkedin.com/jobs/view/{card['job_id']}")

    def test_a_card_under_an_unconfigured_heading_is_not_guessed(self):
        cards, _ = la.parse_cards(
            digest_html([("some alert nobody configured",
                          [("4123456790", "Head of Data", "NewCo", "NewCo · Vienna")])]),
            ALERT_NAMES)
        self.assertEqual(len(cards), 1)
        self.assertIsNone(cards[0]["alert_name"],
                          "an unconfigured alert must leave alert_name empty rather "
                          "than inherit whatever heading came before it")

    def test_a_card_with_no_usable_title_is_reported_not_dropped(self):
        body = ("<html><body><p>Your job alert for AI Business Analyst</p>"
                "<a href='https://www.linkedin.com/comm/jobs/view/4123456791'></a>"
                "<p>SomeCo · Berlin</p></body></html>")
        cards, unparsed = la.parse_cards(body, ALERT_NAMES)
        self.assertEqual(cards, [])
        self.assertEqual(len(unparsed), 1)
        self.assertEqual(unparsed[0]["job_id"], "4123456791")
        self.assertIn("title", unparsed[0]["reason"])

    def test_one_posting_listed_under_two_alerts_carries_one_id(self):
        """parse_cards reports what it sees; main() collapses by ID."""
        dup = ("4123456781", "Process Manager", "RED Global",
               "RED Global · Budapest, Hungary (Hybrid)")
        cards, _ = la.parse_cards(digest_html([
            ("Business Insights & Performance Manager", [dup]),
            ("business excellence manager jobs in Ireland", [dup]),
        ]), ALERT_NAMES)
        self.assertEqual({c["job_id"] for c in cards}, {"4123456781"})

    def test_an_empty_body_is_not_an_error(self):
        self.assertEqual(la.parse_cards("", ALERT_NAMES), ([], []))


class OutputAndStore(unittest.TestCase):
    """What reaches disk: the corpus join, the store, and no account tokens."""

    def test_store_keys_join_the_corpus(self):
        """Property 1, end to end: every stored key matches a corpus dedup key."""
        proc, jobs_out, store_path = run_cli([eml(digest_html(REAL_DIGEST))])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        corpus_keys = {agg.make_dedup_key(r) for r in results_of(jobs_out)}
        store_keys = set(json.loads(store_path.read_text()))
        self.assertEqual(len(store_keys), 7)
        self.assertEqual(store_keys - corpus_keys, set(),
                         "a stored key that no corpus job carries can never mark "
                         "anything, so the 60-point gate would never fire")

    def test_no_account_identifying_token_reaches_disk(self):
        proc, jobs_out, store_path = run_cli([eml(digest_html(REAL_DIGEST))])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for path in (jobs_out, store_path):
            text = path.read_text()
            for token in (MIDTOKEN, "midToken", "eid=", "trackingId", "/comm/"):
                self.assertNotIn(token, text, f"{token} leaked into {path.name}")

    def test_cards_carry_no_description(self):
        """enrich_linkedin.py fetches these first precisely because this is empty."""
        _, jobs_out, _ = run_cli([eml(digest_html(REAL_DIGEST))])
        for result in results_of(jobs_out):
            self.assertIsNone(result["description"])

    def test_the_track_map_is_applied(self):
        _, jobs_out, _ = run_cli([eml(digest_html(REAL_DIGEST))])
        for result in results_of(jobs_out):
            self.assertEqual(result["alert_track"], "T5_process_perf")

    def test_the_alert_name_is_recorded_on_each_result(self):
        # Only this file carries alert_name/alert_track — aggregate_jobs.normalize_job
        # drops them — so the report's per-alert breakdown reads it here or nowhere.
        _, jobs_out, _ = run_cli([eml(digest_html(REAL_DIGEST))])
        counts = {}
        for result in results_of(jobs_out):
            counts[result["alert_name"]] = counts.get(result["alert_name"], 0) + 1
        self.assertEqual(sorted(counts.values()), [2, 2, 3])

    def test_first_alerted_never_moves_forward(self):
        """Re-reading a digest must not renew the 30-day window indefinitely."""
        existing = {"url:linkedin:4123456781": {
            "first_alerted": "2026-08-01", "last_seen": "2026-08-01",
            "alert_name": "Business Insights & Performance Manager",
            "track": "T5_process_perf", "title": "Process Manager",
            "company": "RED Global", "source": "linkedin-alert"}}
        _, _, store_path = run_cli([eml(digest_html(REAL_DIGEST))],
                                   store=existing, today="2026-08-19")
        entry = json.loads(store_path.read_text())["url:linkedin:4123456781"]
        self.assertEqual(entry["first_alerted"], "2026-08-01",
                         "the earlier sighting must win, or a job alerted once could "
                         "stay live forever by being re-read every day")
        self.assertEqual(entry["last_seen"], "2026-08-19")

    def test_a_new_key_is_dated_from_the_email_not_from_today(self):
        _, _, store_path = run_cli(
            [eml(digest_html(REAL_DIGEST), when="Sun, 16 Aug 2026 05:00:00 +0000")],
            today="2026-08-19")
        entry = json.loads(store_path.read_text())["url:linkedin:4123456781"]
        self.assertEqual(entry["first_alerted"], "2026-08-16")
        self.assertEqual(entry["last_seen"], "2026-08-19")

    def test_existing_unrelated_keys_are_preserved(self):
        existing = {"url:linkedin:999999999": {
            "first_alerted": "2026-08-10", "last_seen": "2026-08-10",
            "source": "linkedin-alert"}}
        _, _, store_path = run_cli([eml(digest_html(REAL_DIGEST))], store=existing)
        store = json.loads(store_path.read_text())
        self.assertIn("url:linkedin:999999999", store,
                      "the store is merged, never replaced")
        self.assertEqual(len(store), 8)

    def test_the_message_date_becomes_the_job_date(self):
        # An alert card carries no posting date, so the email's Date is the best upper
        # bound available: the posting existed on or before the day LinkedIn mailed it.
        _, jobs_out, _ = run_cli(
            [eml(digest_html(REAL_DIGEST), when="Sun, 16 Aug 2026 05:00:00 +0000")])
        for result in results_of(jobs_out):
            self.assertEqual(result["date"], "2026-08-16")

    def test_two_messages_repeating_a_job_produce_one_entry(self):
        proc, jobs_out, _ = run_cli([eml(digest_html(REAL_DIGEST)),
                                     eml(digest_html(REAL_DIGEST))])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        results = results_of(jobs_out)
        self.assertEqual(len(results), 7)
        self.assertEqual(len({r["url"] for r in results}), 7)

    def test_dry_run_writes_nothing(self):
        proc, jobs_out, store_path = run_cli([eml(digest_html(REAL_DIGEST))],
                                             extra=["--dry-run"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(jobs_out.exists())
        self.assertFalse(store_path.exists())
        self.assertEqual(json.loads(proc.stdout)["cards"], 7)

    def test_the_summary_reports_the_per_alert_breakdown(self):
        proc, _, _ = run_cli([eml(digest_html(REAL_DIGEST))])
        summary = json.loads(proc.stdout)
        self.assertEqual(summary["messages"], 1)
        self.assertEqual(summary["cards"], 7)
        self.assertEqual(summary["new_store_keys"], 7)
        self.assertEqual(summary["unattributed"], 0)
        self.assertEqual(summary["unparsed_cards"], 0)
        self.assertEqual(sorted(summary["per_alert"].values()), [2, 2, 3])


class FailsLoudly(unittest.TestCase):
    """Property 3: a markup change must not look like a quiet week."""

    def test_messages_but_no_cards_is_an_error(self):
        broken = ("<html><body><p>Your job alert for AI Business Analyst</p>"
                  "<p>3 new jobs match your alert.</p>"
                  "<div>markup LinkedIn changed to something unrecognizable</div>"
                  "</body></html>")
        proc, jobs_out, store_path = run_cli([eml(broken)])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("parsed", proc.stderr.lower())
        self.assertIn("markup", proc.stderr.lower())
        self.assertFalse(jobs_out.exists(),
                         "a failed parse must not leave a partial portal file for the "
                         "aggregator to treat as a complete result")
        self.assertFalse(store_path.exists())

    def test_no_messages_at_all_is_not_an_error(self):
        """A genuinely quiet mailbox: exit 0, zero jobs, no false alarm.

        `--from-file` is given a path that does not exist rather than omitted. Omitting
        it entirely is not the same test: an empty `--from-file` list sends main() down
        its IMAP branch, so the assertion would only hold by connecting to the real
        mailbox — which a test must never do.
        """
        proc, jobs_out, _ = run_cli([], extra=["--from-file", "/nonexistent.eml"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(results_of(jobs_out), [])
        self.assertIn("could not read", proc.stderr)

    def test_an_empty_track_map_warns(self):
        proc, _, _ = run_cli([eml(digest_html(REAL_DIGEST))], track_map={})
        # No configured names means nothing can be attributed at all, which is a
        # config error rather than a quiet day — it must be said out loud.
        self.assertIn("track_map", proc.stderr)

    def test_an_unmapped_alert_still_ingests_its_jobs(self):
        """Promised in search_matrix.json: a config gap is visible, never a silent drop."""
        proc, jobs_out, _ = run_cli(
            [eml(digest_html(REAL_DIGEST))],
            track_map={"Business Insights & Performance Manager": None,
                       "business excellence manager": "T5_process_perf",
                       "Manager Business Artificial Intelligence": "T1_ai_ml"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        results = results_of(jobs_out)
        self.assertEqual(len(results), 7, "every card is ingested regardless of track")
        untracked = [r for r in results if r["alert_track"] is None]
        self.assertEqual(len(untracked), 3,
                         "cards from an alert with no track are kept, not dropped")
        self.assertTrue(all(r["alert_name"] for r in untracked),
                        "and they keep their attribution, so the report can name the "
                        "alert whose track_map entry is missing")

    def test_a_card_matching_no_configured_alert_warns_and_is_kept(self):
        proc, jobs_out, _ = run_cli(
            [eml(digest_html([("a brand new alert not yet in the config", [
                ("4123456792", "Head of AI", "NewCo", "NewCo · Vienna")])]))])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(results_of(jobs_out)), 1)
        self.assertEqual(json.loads(proc.stdout)["unattributed"], 1)
        self.assertIn("could not be attributed", proc.stderr)

    def test_a_bad_today_is_rejected_before_anything_is_written(self):
        proc, jobs_out, store_path = run_cli([eml(digest_html(REAL_DIGEST))],
                                             today="18-08-2026")
        self.assertEqual(proc.returncode, 2)
        self.assertFalse(jobs_out.exists())
        self.assertFalse(store_path.exists())


class PipelineWiring(unittest.TestCase):
    """The runner and config seams Phase 0b depends on. Cheap, and catches renames."""

    def test_phase_0b_runs_before_phase_1_fetches(self):
        self.assertLess(RUNNER.index("scripts/linkedin_alerts.py"),
                        RUNNER.index("Phase 1: Fetching jobs from portals"),
                        "Phase 0b must write its portal file before Phase 1 globs for "
                        "portal files, or the alerts miss aggregation entirely")

    def test_the_portal_filename_lands_in_the_aggregation_glob(self):
        self.assertIn(
            'ALERT_JOBS_FILE="/tmp/jobsearch_portal_linkedin-alert_${TODAY}.json"',
            RUNNER)
        self.assertIn("PORTAL_FILES=(/tmp/jobsearch_portal_*_${TODAY}.json)", RUNNER)

    def test_the_filename_is_what_detect_portal_recognizes(self):
        """The name is the whole wiring: rename it and alerts become linkedin-search."""
        self.assertEqual(
            agg.detect_portal(
                Path("/tmp/jobsearch_portal_linkedin-alert_2026-08-19.json"), {}),
            "linkedin-alert")

    def test_the_alert_file_sorts_ahead_of_the_search_files(self):
        # Dedup keeps the first occurrence of a key, so glob order is what decides
        # whether a posting found by both an alert and a search keeps its alert
        # attribution. Asserted on real sorting rather than reasoned from ASCII.
        names = sorted([
            "jobsearch_portal_linkedin-alert_2026-08-19.json",
            "jobsearch_portal_linkedin_t1_ai_engineer_hungary_2026-08-19.json",
            "jobsearch_portal_freehire_ai_2026-08-19.json",
        ])
        self.assertLess(
            names.index("jobsearch_portal_linkedin-alert_2026-08-19.json"),
            names.index("jobsearch_portal_linkedin_t1_ai_engineer_hungary_2026-08-19.json"))

    def phase_0b_block(self):
        block = RUNNER[RUNNER.index("=== Phase 0b"):]
        return block[:block.index("=== Phase 1:")]

    def test_phase_0b_failure_is_non_fatal_and_reaches_the_report(self):
        block = self.phase_0b_block()
        self.assertIn("$WARN_FILE", block,
                      "a mail outage must reach the report, not just the log")
        self.assertNotIn("exit 1", block,
                         "alerts are an enhancement; losing them must not cost the run "
                         "its ranking")

    def test_a_failed_run_removes_its_partial_output(self):
        self.assertIn('rm -f "$ALERT_JOBS_FILE"', self.phase_0b_block())

    def test_skip_alerts_and_resume_both_skip_the_mailbox(self):
        self.assertIn('SKIP_ALERTS="${SKIP_ALERTS:-0}"', RUNNER)
        block = self.phase_0b_block()
        self.assertIn('"$RESUME" == "1"', block)
        self.assertIn('"$SKIP_ALERTS" == "1"', block)

    def test_the_store_path_is_the_one_the_gate_reads(self):
        self.assertIn('ALERT_STORE="$PROJECT_DIR/job_scraper/alert_matched.json"', RUNNER)
        self.assertIn("alert_matched.json",
                      (REPO / "scripts" / "gate_jobs.py").read_text())

    def test_every_configured_alert_maps_to_a_real_track(self):
        tracks = set(MATRIX["linkedin"]["tracks"])
        for name, track in MATRIX["alerts"]["track_map"].items():
            self.assertIn(track, tracks,
                          f"alert {name!r} maps to {track!r}, which is not a track")

    def test_the_configured_names_parse_the_real_digest(self):
        """The config is only useful if it attributes Salman's actual emails."""
        names = list(MATRIX["alerts"]["track_map"])
        cards, unparsed = la.parse_cards(digest_html(REAL_DIGEST), names)
        self.assertEqual(len(cards), 7)
        self.assertEqual(unparsed, [])
        self.assertEqual([c for c in cards if c["alert_name"] is None], [])

    def test_the_three_geo_variants_share_one_configured_name(self):
        """Spain, Ireland and Budapest are three alerts with one name — one entry."""
        names = list(MATRIX["alerts"]["track_map"])
        self.assertIn("business excellence manager", names)
        for heading in ["business excellence manager jobs in Ireland",
                        "business excellence manager jobs in Spain",
                        "business excellence manager jobs in Budapest, Hungary"]:
            self.assertEqual(la.find_alert_name(heading, names),
                             "business excellence manager", heading)


class MailboxSafety(unittest.TestCase):
    """Read-only, and no LinkedIn credential anywhere. Both are design commitments."""

    SOURCE = SCRIPT.read_text()

    def test_the_mailbox_is_opened_read_only(self):
        self.assertIn("readonly=True", self.SOURCE)
        for mutation in (".store(", ".copy(", ".move(", ".expunge("):
            self.assertNotIn(mutation, self.SOURCE,
                             f"{mutation} would modify Salman's mailbox")

    def test_no_linkedin_session_credential_is_read(self):
        # The design stores no LinkedIn password, token, cookie or session — the only
        # credential involved is the Gmail app password already in automation.json.
        lowered = self.SOURCE.lower()
        for forbidden in ("li_at", "jsessionid", "linkedin_password", "csrf-token"):
            self.assertNotIn(forbidden, lowered)

    def test_the_login_failure_message_cannot_echo_the_password(self):
        # The message names the user and points at the config; interpolating the
        # credential would put it in the log file the report links to.
        block = self.SOURCE[self.SOURCE.index("IMAP login failed"):]
        block = block[:block.index("conn.select")]
        self.assertNotIn("{password", block)

    def test_the_socket_carries_a_deadline(self):
        # Guards the exact regression: a bare IMAP4_SSL(host, port) blocks forever.
        self.assertIn("timeout=IMAP_TIMEOUT_SECONDS", self.SOURCE)
        self.assertNotIn("imaplib.IMAP4_SSL(host, port)", self.SOURCE)


class MailboxTimeouts(unittest.TestCase):
    """A mailbox that stops answering must not stop the pipeline.

    On 2026-08-22 the 08:00 run reached `conn.fetch` and stayed there for six hours.
    The connection was still ESTABLISHED locally while the peer had gone silent, and
    `imaplib.IMAP4_SSL(host, port)` with no timeout waits on that forever. The run
    held its lock, fetched no corpus, ranked nothing and sent no digest — and nothing
    reported a problem, because a blocked process looks exactly like a busy one.

    These use a fake IMAP server on loopback rather than a mock, because the bug was
    in socket behaviour: a mock answers instantly and passes against the unfixed code.
    """

    @staticmethod
    def _silent_at(stop_at):
        """A server that plays a normal IMAP dialogue, then goes silent at `stop_at`.

        `stop_at` names the command to stall on, because "never answers at all" and
        "answers until the FETCH" are different code paths: the first raises from the
        constructor, the second from deep inside the session, where a connect-only
        timeout would have stopped watching. Returns (host, port).
        """
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        seen = []

        def serve():
            try:
                conn, _ = srv.accept()
            except OSError:                       # pragma: no cover - test teardown
                return
            with conn, conn.makefile("rwb") as io:
                if stop_at == "GREETING":
                    time.sleep(120)               # accept, then never even greet
                    return
                io.write(b"* OK [CAPABILITY IMAP4rev1 AUTH=PLAIN] fake ready\r\n")
                io.flush()
                while True:
                    line = io.readline()
                    if not line:
                        return
                    tag = line.split(b" ", 1)[0].decode()
                    command = line.decode(errors="replace").upper()
                    seen.append(command.split()[1] if len(command.split()) > 1 else "?")
                    if stop_at in command:
                        time.sleep(120)           # the hang: alive, unanswering
                        return
                    if "CAPABILITY" in command:
                        io.write(b"* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\n"
                                 + f"{tag} OK done\r\n".encode())
                    elif "LOGIN" in command:
                        io.write(f"{tag} OK LOGIN completed\r\n".encode())
                    elif "EXAMINE" in command or "SELECT" in command:
                        io.write(b"* 1 EXISTS\r\n"
                                 + f"{tag} OK [READ-ONLY] done\r\n".encode())
                    elif "SEARCH" in command:
                        io.write(b"* SEARCH 1\r\n" + f"{tag} OK done\r\n".encode())
                    else:
                        io.write(f"{tag} OK done\r\n".encode())
                    io.flush()

        threading.Thread(target=serve, daemon=True).start()
        host, port = srv.getsockname()
        return host, port, seen

    def setUp(self):
        # Three seconds keeps the suite fast. The number under test is not 60 — it is
        # "a deadline exists, fires, and arrives as the exception main() handles".
        self._real_timeout = la.IMAP_TIMEOUT_SECONDS
        self._real_ssl = la.imaplib.IMAP4_SSL
        la.IMAP_TIMEOUT_SECONDS = 3
        # Plain IMAP4 stands in for IMAP4_SSL: the fake server speaks no TLS, and the
        # deadline being tested rides on the socket underneath either class.
        la.imaplib.IMAP4_SSL = (
            lambda host, port, timeout=None: la.imaplib.IMAP4(host, port, timeout=timeout))

    def tearDown(self):
        la.IMAP_TIMEOUT_SECONDS = self._real_timeout
        la.imaplib.IMAP4_SSL = self._real_ssl

    def _fetch(self, host, port):
        return la.fetch_messages(host, port, "someone@example.com", "app-password",
                                 "JobSearch/LinkedIn-Alerts", date(2026, 8, 20),
                                 lambda message: None)

    def test_the_configured_timeout_is_set_and_generous(self):
        # Generous on purpose: Phase 0b read 81 jobs in ~11s on 2026-08-22, so a
        # deadline anywhere near that would fire on a merely slow mailbox and lose the
        # alerts it exists to protect.
        self.assertGreaterEqual(self._real_timeout, 30)
        self.assertLessEqual(self._real_timeout, 300)

    def test_a_server_that_never_greets_is_given_up_on(self):
        host, port, _ = self._silent_at("GREETING")
        started = time.monotonic()
        with self.assertRaises(RuntimeError):
            self._fetch(host, port)
        self.assertLess(time.monotonic() - started, 30)

    def test_a_hang_mid_session_is_given_up_on(self):
        """The 2026-08-22 failure exactly: login, select and search all succeed.

        This is the case a connect-only timeout misses. `timeout=` on the constructor
        covers it because CPython builds the socket with
        `socket.create_connection(address, timeout)` and never clears the deadline, so
        the same limit applies to every later read.
        """
        host, port, seen = self._silent_at("FETCH")
        started = time.monotonic()
        with self.assertRaises(RuntimeError) as caught:
            self._fetch(host, port)
        self.assertLess(time.monotonic() - started, 30)
        self.assertIn("stopped responding", str(caught.exception))
        # Proves the stall was mid-session and not a failed handshake, which an earlier
        # version of this test mistook for the same thing.
        self.assertIn("FETCH", seen)
        self.assertIn("LOGIN", seen)

    def test_a_timeout_arrives_as_the_error_main_already_handles(self):
        """RuntimeError, not TimeoutError, and the difference decides the run.

        `main()` turns RuntimeError into one stderr line and exit 1, which the runner
        reads as "degrade to search-only". A bare TimeoutError is an OSError, escapes
        that handler, and turns a recoverable mailbox stall into a traceback.
        """
        host, port, _ = self._silent_at("FETCH")
        try:
            self._fetch(host, port)
        except RuntimeError:
            pass
        except TimeoutError:                      # pragma: no cover - the regression
            self.fail("a socket timeout escaped as TimeoutError; main() will not catch "
                      "it, so the log gets a traceback instead of a warning and the "
                      "runner cannot tell a stalled mailbox from a crash")

    def test_the_message_says_how_much_was_read(self):
        # The report has to distinguish "stalled before reading anything" from
        # "stalled halfway", because the second means today's alerts are partial.
        host, port, _ = self._silent_at("FETCH")
        with self.assertRaises(RuntimeError) as caught:
            self._fetch(host, port)
        self.assertIn("message(s) had been read", str(caught.exception))
        self.assertNotIn("app-password", str(caught.exception))

    def test_the_runner_also_bounds_the_phase(self):
        """Second line of defense, for a blocking call that is not a socket read."""
        self.assertIn("ALERT_TIMEOUT", RUNNER)
        self.assertIn("Phase 0b TIMEOUT", RUNNER)
        # 124 distinguishes "we stopped it" from "it failed", so the report can avoid
        # blaming the mailbox's contents for a phase that was killed mid-read.
        self.assertIn("ALERT_EXIT=124", RUNNER)

    def test_the_phase_watchdog_does_not_abort_the_run(self):
        """The `set -e` hazard that cost the 2026-08-18 run every phase after 2.

        `wait` on an already-reaped PID returns non-zero, and under `set -euo pipefail`
        that aborts the script. So the timeout branch must break rather than fall
        through to `wait`, and the normal path's `wait` must be guarded.
        """
        block = RUNNER[RUNNER.index("Phase 0b: reading LinkedIn job alerts"):]
        block = block[:block.index("Phase 1:")]
        self.assertIn("wait $ALERT_PID || ALERT_EXIT=$?", block)
        self.assertIn("ALERT_TIMED_OUT=1", block)
        self.assertIn("break", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
