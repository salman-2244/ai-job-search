#!/usr/bin/env python3
"""Pull the full job description off a live LinkedIn posting via Kimi WebBridge.

Why this exists rather than an HTTP fetch: LinkedIn serves the job description only
to an authenticated, *foregrounded* browser tab. `enrich_linkedin.py` fetches over
HTTP and gets the ~500-character card snippet at best, which is what left 10 of 14
LinkedIn rows in the 2026-08-23 rankset sitting at `evidence_chars: 0`.

Two findings from `docs/LINKEDIN_SELECTOR_FINDINGS.md` drive the whole design, and
both are counter-intuitive enough to restate here:

1. **A hidden tab never renders the description at all.** LinkedIn defers mounting the
   job-detail route while `document.visibilityState == "hidden"`, and it never fires the
   job fetch. `readyState` still reaches `"complete"`, so the page reports itself
   finished while permanently missing its main content. Waiting longer cannot fix it —
   the extractor must foreground the tab (`Page.bringToFront`) and then assert on
   *content*, never on load state.

2. **Every CSS class is a rotating hash** (`_823a9014 e6f094c7 …`). Class-based
   selectors are worthless. The description container carries a stable semantic id
   instead — `JobDetails_AboutTheJob_<jobId>` — one of a family of `JobDetails_*_<id>`
   module ids. Prefix-matching that id is the primary strategy; keying on the job id
   parsed from the URL would break on the locale hosts (`hu.`, `se.`) where the URL
   slug and the canonical posting id need not agree.

Scoping to that container also drops LinkedIn's footer locale picker
("Deutsch (German)", "English (English)", …), which is inside `document.body` and would
otherwise read as a language requirement to any downstream language gate.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict

DAEMON = "http://127.0.0.1:10086/command"
SESSION = "Job posting verification"

MAX_ATTEMPTS = 3
BACKOFF_BASE = 2.0          # seconds; attempt N waits BACKOFF_BASE ** N
NAV_TIMEOUT = 120

# Politeness between consecutive postings. Randomised because a fixed cadence is
# itself a bot signature, and jittered around a few seconds because a human reading
# job ads does not open them 200ms apart. This is the only rate limiting there is —
# the daemon imposes none, so pacing is entirely this module's responsibility.
POLITE_DELAY = (3.5, 8.0)

# A description shorter than this is not a description. The observed floor across the
# rankset's real bodies is ~1,900 chars; 400 is well under any of them but far above
# the ~200-char stub a half-mounted module can leave behind.
MIN_CHARS = 400

# Substrings that mean we captured page furniture instead of the posting. If any of
# these appear the extraction is rejected outright rather than passed downstream to be
# gated as if it were job text.
CHROME_MARKERS = (
    "Skip to main content",
    "Skip to primary content",
    "0 notifications",
    "LinkedIn Corporation ©",
)


class ExtractionError(RuntimeError):
    """Raised when no strategy produced usable text.

    Carries the diagnostic payload rather than just a message: which strategies ran,
    what each returned, and a snippet of what was actually on the page. A bare
    "extraction failed" would send the next reader back to square one of the
    investigation this module exists to close out.
    """

    def __init__(self, message, *, url, attempts, diagnostics):
        super().__init__(message)
        self.url = url
        self.attempts = attempts
        self.diagnostics = diagnostics

    def report(self) -> str:
        d = self.diagnostics or {}
        lines = [f"{self}", f"  url:      {self.url}", f"  attempts: {self.attempts}"]
        for k in ("visibility", "hasFocus", "readyState", "bodyTextLen", "htmlLen",
                  "authWall", "captcha", "expanded"):
            if k in d:
                lines.append(f"  {k+':':10s}{d[k]}")
        for s in d.get("strategies", []):
            lines.append(f"  tried {s.get('name')!r} -> {s.get('selector')!r} "
                         f"len={s.get('len', 0)} {s.get('note', '')}".rstrip())
        if d.get("bodySnippet"):
            lines.append("  page snippet:")
            lines.append("    " + d["bodySnippet"][:400].replace("\n", "\n    "))
        return "\n".join(lines)


@dataclass
class Extraction:
    """One posting's worth of extracted text, plus how it was obtained."""
    url: str
    job_id: str | None
    text: str
    strategy: str
    selector: str
    elapsed_s: float
    attempts: int
    expanded: bool
    validation: dict = field(default_factory=dict)

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    def summary(self) -> str:
        v = "PASS" if self.validation.get("ok") else "FAIL"
        return (f"{self.url}\n"
                f"  strategy:   {self.strategy} ({self.selector})\n"
                f"  time:       {self.elapsed_s:.1f}s over {self.attempts} attempt(s)\n"
                f"  extracted:  {len(self.text)} chars / {self.word_count} words"
                f"{' (expanded)' if self.expanded else ''}\n"
                f"  validation: {v}"
                + ("" if self.validation.get("ok")
                   else f" - {'; '.join(self.validation.get('failures', []))}"))


# --- daemon plumbing -------------------------------------------------------------

def _call(action, args, session=SESSION, timeout=NAV_TIMEOUT):
    """POST one command to the WebBridge daemon and return its `data` payload.

    Built with urllib rather than a curl subprocess on purpose: shell quoting mangles
    the JSON body, and the corrupted requests that produced were how the previous
    investigation convinced itself the selectors were at fault.
    """
    body = json.dumps({"action": action, "args": args, "session": session})
    req = urllib.request.Request(
        DAEMON, data=body.encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise ExtractionError(
            f"cannot reach the WebBridge daemon at {DAEMON} ({exc}). "
            "Start it with `~/.kimi-webbridge/bin/kimi-webbridge start`.",
            url="", attempts=0, diagnostics={},
        ) from exc
    if not payload.get("ok"):
        raise RuntimeError(f"{action} failed: {str(payload)[:300]}")
    return payload.get("data")


def _evaluate(code):
    """Run JS in the page and return the decoded result.

    `evaluate` hands back `{"type": ..., "value": ...}` and our page code always
    returns a JSON string, so unwrap both layers here instead of at every call site.
    """
    data = _call("evaluate", {"code": code})
    value = data.get("value") if isinstance(data, dict) else data
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {"raw": value}
    return value


def job_id_from_url(url: str) -> str | None:
    """Best-effort posting id, for logging and cross-checks only.

    Deliberately *not* used to build the selector. On `hu.`/`se.` locale hosts the URL
    carries a slug (`.../view/quality-performance-manager-at-bat-4452361628`) and the
    trailing digits are not guaranteed to be the id the DOM uses.
    """
    m = re.search(r"(\d{8,})", url)
    return m.group(1) if m else None


# --- the page-side extraction ----------------------------------------------------

# Runs inside the page. Polls for the anchor, expands any "… more" collapse, then
# walks a fallback chain. Returns diagnostics on failure as well as success, so a
# miss is reportable without a second round trip.
_EXTRACT_JS = r"""
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const MIN = %(min_chars)d;
  const strategies = [];

  // Strategy 1 — the stable semantic id. Prefix match, never the URL's job id.
  const byId = () => document.querySelector('[id^="JobDetails_AboutTheJob"]');

  // Strategy 2 — pre-hash builds, in case an older layout is ever served.
  const LEGACY = ['.jobs-description__content', '.jobs-box__html-content',
                  '.show-more-less-html__markup', '#job-details',
                  '.description__text', '[class*="jobs-description"]'];
  const byLegacy = () => {
    for (const sel of LEGACY) {
      const el = document.querySelector(sel);
      if (el && (el.innerText || '').trim().length >= MIN) return [el, sel];
    }
    return [null, null];
  };

  // Strategy 3 — the technique that located the anchor in the first place: find a
  // text node that reads like job copy, then climb until the block is substantial.
  // Survives a total id rename, which is the one thing strategies 1 and 2 cannot.
  const PHRASES = ['Responsibilities', 'Qualifications', 'About the job',
                   'What you', 'Your tasks', 'We offer', 'years of experience',
                   'Requirements', 'About the role'];
  const byPhraseTrace = () => {
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let n;
    while (n = w.nextNode()) {
      const t = n.textContent || '';
      if (!PHRASES.some(p => t.includes(p))) continue;
      let el = n.parentElement;
      for (let i = 0; i < 16 && el; i++) {
        const len = (el.innerText || '').length;
        // Stop at the first ancestor big enough to be the description but still
        // inside the page — `main`/`body` would drag in nav and footer chrome.
        if (len >= MIN && el.tagName !== 'MAIN' && el.tagName !== 'BODY') {
          return [el, 'phrase-trace:' + t.trim().slice(0, 24)];
        }
        el = el.parentElement;
      }
    }
    return [null, null];
  };

  // Wait on content, never on readyState — readyState was "complete" throughout the
  // hidden-tab failure this module was written to defeat.
  let el = null, selector = null, name = null;
  for (let i = 0; i < 32; i++) {
    el = byId();
    if (el && (el.innerText || '').trim().length >= MIN) {
      selector = '[id^="JobDetails_AboutTheJob"]'; name = 'id-anchor'; break;
    }
    await sleep(250);
  }
  if (!el || !name) {
    const [le, ls] = byLegacy();
    if (le) { el = le; selector = ls; name = 'legacy-selector'; }
  }
  if (!el || !name) {
    const [pe, ps] = byPhraseTrace();
    if (pe) { el = pe; selector = ps; name = 'phrase-trace'; }
  }
  strategies.push({name: 'id-anchor', selector: '[id^="JobDetails_AboutTheJob"]',
                   len: (byId() ? (byId().innerText || '').length : 0)});

  // Expand the "… more" collapse. Synthetic click() is enough here (verified), but a
  // failure is non-fatal: the clamp is CSS, so most text is already in innerText.
  let expanded = false;
  if (el && name) {
    const btn = [...document.querySelectorAll('button')]
      .find(b => /^[…\.\s]*more$/i.test((b.innerText || '').trim()));
    if (btn) {
      const before = (el.innerText || '').length;
      try { btn.click(); await sleep(1000); } catch (e) {}
      expanded = (el.innerText || '').length > before;
    }
  }

  const bodyText = document.body.innerText || '';
  return JSON.stringify({
    ok: !!(el && name),
    text: (el && name) ? (el.innerText || '').trim() : '',
    strategy: name, selector: selector, expanded: expanded,
    strategies: strategies,
    visibility: document.visibilityState, hasFocus: document.hasFocus(),
    readyState: document.readyState,
    bodyTextLen: bodyText.length,
    htmlLen: document.documentElement.innerHTML.length,
    authWall: /authwall|sign in to see|join now to see/i.test(bodyText),
    captcha: !!document.querySelector(
      '[id*=captcha],[class*=captcha],iframe[src*=recaptcha],iframe[src*=hcaptcha]'),
    bodySnippet: bodyText.slice(0, 600)
  });
})()
"""


def validate(text: str, keywords=(), min_chars=MIN_CHARS) -> dict:
    """Judge whether `text` is really a job description.

    Keyword checking is case-insensitive and reported as a *list of misses* rather
    than a pass/fail count, because a partial match is the interesting case: it
    usually means the right element was found on the wrong posting.
    """
    failures = []
    if len(text) < min_chars:
        failures.append(f"only {len(text)} chars, under the {min_chars} floor")
    hit_chrome = [m for m in CHROME_MARKERS if m in text]
    if hit_chrome:
        failures.append(f"contains page chrome: {hit_chrome}")
    low = text.lower()
    missing = [k for k in keywords if k.lower() not in low]
    if missing:
        failures.append(f"missing expected keyword(s): {missing}")
    return {"ok": not failures, "failures": failures, "chars": len(text),
            "words": len(text.split()), "missing_keywords": missing,
            "checked_keywords": list(keywords)}


def extract(url: str, *, keywords=(), min_chars=MIN_CHARS,
            session=SESSION, verbose=False, new_tab=True) -> Extraction:
    """Open `url` in the real browser and return its description with provenance.

    Retries up to MAX_ATTEMPTS with exponential backoff. A retry re-navigates and
    re-foregrounds rather than merely re-reading, since the failure mode being
    guarded against is a tab that was never mounted in the first place.

    `new_tab=False` navigates the session's existing tab instead of opening another.
    A batch of a dozen postings otherwise leaves a dozen live LinkedIn tabs, each
    running the full SPA, and that resource pressure is itself a source of timeouts
    late in a run. Reuse never *closes* anything — it only stops the pile growing.

    Raises ExtractionError with a populated `.diagnostics` if every attempt fails, or
    immediately — without burning retries — on a login wall or CAPTCHA, which are
    conditions for a human to look at, not for a machine to hammer.
    """
    started = time.monotonic()
    last_diag: dict = {}

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if attempt > 1:
            delay = BACKOFF_BASE ** attempt
            if verbose:
                print(f"    retry {attempt}/{MAX_ATTEMPTS} in {delay:.0f}s",
                      file=sys.stderr)
            time.sleep(delay)

        _call("navigate", {"url": url, "newTab": new_tab and attempt == 1,
                           "group_title": session}, session=session)
        # The single most important line in this module. Without it the job-detail
        # route never mounts and every selector below matches nothing.
        _call("cdp", {"method": "Page.bringToFront", "params": {}}, session=session)

        result = _evaluate(_EXTRACT_JS % {"min_chars": min_chars})
        last_diag = {k: result.get(k) for k in
                     ("visibility", "hasFocus", "readyState", "bodyTextLen", "htmlLen",
                      "authWall", "captcha", "expanded", "strategies", "bodySnippet")}

        if result.get("captcha"):
            raise ExtractionError(
                "CAPTCHA present - pausing rather than attempting to get past it. "
                "Open the URL yourself, clear it, then re-run.",
                url=url, attempts=attempt, diagnostics=last_diag)
        if result.get("authWall"):
            raise ExtractionError(
                "the page is behind a login wall in this browser session - pausing "
                "rather than guessing at the content.",
                url=url, attempts=attempt, diagnostics=last_diag)

        if result.get("ok"):
            text = result["text"]
            v = validate(text, keywords=keywords, min_chars=min_chars)
            # A text-too-short failure is worth another attempt (a partial mount);
            # a keyword mismatch is not, because retrying cannot change the posting.
            too_short = any("under the" in f for f in v["failures"])
            if v["ok"] or not too_short or attempt == MAX_ATTEMPTS:
                return Extraction(
                    url=url, job_id=job_id_from_url(url), text=text,
                    strategy=result.get("strategy") or "unknown",
                    selector=result.get("selector") or "unknown",
                    elapsed_s=time.monotonic() - started, attempts=attempt,
                    expanded=bool(result.get("expanded")), validation=v)

    raise ExtractionError("no strategy produced a usable description",
                          url=url, attempts=MAX_ATTEMPTS, diagnostics=last_diag)


def extract_linkedin_description(url: str) -> str:
    """Return the full description text for a LinkedIn posting URL.

    The plain signature from the brief, for callers that want the text and nothing
    else. Use `extract()` when provenance, timings, or validation detail matter.
    """
    return extract(url).text


def extract_many(urls, *, keywords_by_url=None, verbose=True, new_tab=True):
    """Extract several postings with polite, jittered spacing between them.

    Returns `(results, failures)` and never raises for a single bad URL: one dead
    posting in a batch of 25 should not discard the other 24.
    """
    keywords_by_url = keywords_by_url or {}
    results, failures = [], []
    for i, url in enumerate(urls):
        if i:
            pause = random.uniform(*POLITE_DELAY)
            if verbose:
                print(f"  ... pausing {pause:.1f}s", file=sys.stderr)
            time.sleep(pause)
        try:
            got = extract(url, keywords=tuple(keywords_by_url.get(url, ())),
                          verbose=verbose, new_tab=new_tab)
            results.append(got)
            if verbose:
                print(got.summary(), file=sys.stderr)
        except ExtractionError as exc:
            failures.append(exc)
            if verbose:
                print(exc.report(), file=sys.stderr)
    return results, failures


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("urls", nargs="+", help="LinkedIn job posting URL(s)")
    ap.add_argument("--keyword", action="append", default=[],
                    help="require this string in the extracted text (repeatable)")
    ap.add_argument("--json", action="store_true", help="emit JSON on stdout")
    ap.add_argument("--text", action="store_true", help="print the description text")
    args = ap.parse_args(argv)

    kw = {u: tuple(args.keyword) for u in args.urls}
    results, failures = extract_many(args.urls, keywords_by_url=kw)

    if args.json:
        print(json.dumps({
            "extracted": [{**asdict(r), "text_len": len(r.text),
                           "text": r.text if args.text else None} for r in results],
            "failed": [{"url": f.url, "error": str(f), "attempts": f.attempts,
                        "diagnostics": f.diagnostics} for f in failures],
        }, indent=2, ensure_ascii=False))
    elif args.text:
        for r in results:
            print(f"===== {r.url} =====\n{r.text}\n")

    return 0 if results and not failures else 1


if __name__ == "__main__":
    sys.exit(main())
