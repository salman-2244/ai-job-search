---
tags: [note, browser]
---
# 05 - Playwright and Headless Mode

Playwright is the **optional tier-3** LinkedIn enrichment client — never the primary
browser automation path (that's Kimi WebBridge), and never used for portal searches
(those are plain HTTP CLIs). Parent: [[00 - Project Overview]].

## How Playwright is used

`scripts/linkedin_playwright.py` defines `PlaywrightDetailProvider`, which:

- loads an authenticated session from a **Playwright storage-state file**
  (`~/.jobsearch-linkedin-state.json`) that a human must provision once from a
  terminal via `scripts/linkedin_session.py` — automated credential login is
  deliberately unsupported (a machine-driven login is exactly what LinkedIn's
  anti-bot screens exist to catch);
- navigates to `https://www.linkedin.com/jobs/view/<id>` (job id validated as digits
  before interpolation), waits for the description element to hydrate by polling
  within a bounded timeout, reads the semantic selector
  `.show-more-less-html__markup, .jobs-description__content, [class*='jobs-description']`;
- **never bypasses a wall**: login/authwall, CAPTCHA/checkpoint, and consent screens
  each raise a distinct typed error (`PlaywrightLoginWallError`,
  `PlaywrightChallengeError`, `PlaywrightConsentError`) naming the operator action,
  and the tier pauses (reports, doesn't retry);
- charges the shared `RequestLedger` **before** every navigation, so tier 3 can never
  push the run over `linkedin.max_requests_per_run`;
- is injected into Phase 1c by `enrich_linkedin.py:_load_playwright_provider` /
  `playwright_fetcher`, with graceful degrade to the guest CLI on any failure
  (posting failures tolerated twice — `BROWSER_FAILURE_STREAK = 2` — then the tier
  is condemned for the run).

`playwright` is imported **lazily inside the first fetch**, never at module scope —
the module imports cleanly and tests run on machines with no browser installed.

## Where headless mode is configured

| Layer | Setting | Default |
|---|---|---|
| Env file | `LINKEDIN_PLAYWRIGHT_HEADLESS` (read in `stage_3/config.py:315` and `enrich_linkedin.py:1057`) | **`true`** |
| Env file | `LINKEDIN_PLAYWRIGHT_TIMEOUT` | 30s (clamped 1–300) |
| Provider constructor | `PlaywrightDetailProvider(headless=...)` → `pw.chromium.launch(headless=self._headless)` (`linkedin_playwright.py:330`) | true |

**Current setting: headless = true** everywhere (default unoverridden in env files).

The one **deliberately headed** browser is `scripts/linkedin_session.py provision()`
(`pw.chromium.launch(headless=False)`) — the human must see the window to type their
own login. That helper is terminal-only and never runs from the pipeline.

## Scripts that use Playwright

| Script | Role |
|---|---|
| `scripts/linkedin_playwright.py` | The tier-3 provider (sync Playwright, chromium) |
| `scripts/linkedin_session.py` | Terminal-only session provisioner (headed) / `--show` state describer |
| `scripts/enrich_linkedin.py` | Arms tier 3 only when `linkedin.use_playwright: true` in the matrix AND `LINKEDIN_PLAYWRIGHT_STORAGE_STATE` names a valid file |
| `stage_3/config.py` | Parses the headless/timeout settings and forwards the storage-state path to child environments |
| `requirements-stage3.txt` | Pins `playwright==1.62.0`; browser installed separately with `playwright install chromium` |

## The 0600 rule (a real enforcement, not a convention)

The storage state is an authenticated LinkedIn session, so both the loader
(`enrich_linkedin.py:1052`) and the provider (`linkedin_playwright.py:217`) **reject
it unless its mode is exactly 0600**. `linkedin_session.py` writes it atomically
(temp + `os.replace` + chmod 0600) and `--show` reports (never prints) its shape:
cookie counts and soonest expiry only.

## Documented visible-browser issues

Two distinct, well-documented "browser didn't show the right thing" problems shaped
this design (both in `docs/LINKEDIN_SELECTOR_FINDINGS.md` and inline comments):

1. **Hidden tabs never render the description** — LinkedIn defers mounting the
   job-detail route while `document.visibilityState == "hidden"`, and `readyState`
   still reaches `complete`. This is why the tier-1 WebBridge extractor
   (`scripts/linkedin_extract.py`) must foreground the tab (`Page.bringToFront`) and
   assert on **content**, never on load state. A hidden-tab fetch was the original
   cause of `description_chars: 0` rows and of the old 6,000-char description cap
   looking sufficient when real bodies are 6k–19k.
2. **Rotating hashed CSS classes** — every LinkedIn class is a rotating hash, so
   class-based selectors are worthless; the extractor prefix-matches the stable
   semantic id `JobDetails_AboutTheJob_<jobId>`. The Playwright provider instead
   targets the semantic description classes listed above.
3. **Anti-bot friction as operator actions** — a CAPTCHA/checkpoint is reported and
   paused, never retried; the standing rule is "report and let a human clear it".
   The guest CLI is a different client and is unaffected.

## The tier-3 engagement condition (both, never one)

Tier 3 only engages when **both** hold: the matrix sets
`linkedin.use_playwright: true` **and** the environment points
`LINKEDIN_PLAYWRIGHT_STORAGE_STATE` at a file with exact mode 0600. A session nobody
asked this path to spend must not be spent by it. There is deliberately no mid-run
handoff from the WebBridge browser to Playwright — two authenticated clients on one
host would double session risk for the same body.

Related: [[01 - Pipeline Architecture]] (Phase 1c) · [[02 - Configuration and Settings]] ·
[[07 - Known Issues and Bugs]] (tier-3 & enrichment notes).

## Vault graph

Browser files: [[linkedin-playwright]] · [[linkedin-session]] · [[linkedin-extract]] · [[enrich-linkedin]] — hub: [[_Pipeline Flow]]
