# Phase 1c enrichment — the dual-path design

How the daily pipeline fetches LinkedIn posting bodies, why there are two ways to do
it, and what has to be running at 08:00 for the faster one to be used.

**Status as of 2026-08-24: the browser path is wired in and primary.** `enrich_linkedin.py`
runs a pre-flight chain at Phase 1c entry, uses the authenticated browser when it passes,
and falls back to the guest CLI on every failure. Verified live the same day on three
real postings (see **Measured, and one figure corrected** below).

## Why two paths

Phase 1c exists because non-LinkedIn portals hand the ranker a ~500-char snippet,
while LinkedIn will give up the whole posting body — and the body is what the language
and experience gates need. Two clients can fetch it:

| | **Guest CLI** (fallback) | **Authenticated browser** (primary) |
|---|---|---|
| Entry point | `enrich_linkedin.fetch_detail()` → `bun run .agents/skills/linkedin-search/cli/src/cli.ts detail <id>` | `linkedin_extract.extract()` → WebBridge daemon |
| Sees | `linkedin.com/jobs-guest/...` public HTML, `.description__text` | The real logged-in posting, DOM after mount |
| Auth | none, honest non-spoofed UA | your live LinkedIn session |
| Speed | **~0.5 s/fetch** (3 postings, 2026-08-24) | **~3.9 s/fetch** (same 3 postings, same day) |
| Text volume | 0.5–1% *less* than the browser (−37 to −50 chars on those 3) | baseline |
| Structured fields | `seniority`, `employmentType`, `jobFunction`, `industries`, `applyUrl` | **none — description text only** |
| Needs a running browser | no | yes |

### Measured, and one figure corrected

An earlier version of this file claimed the guest CLI costs **~93 s/job** and that the
browser is therefore ~15× faster. That number came from a wall-clock reading of a whole
phase — 15 jobs in 23 minutes — and attributing all of it to the fetch was wrong. Timed
per fetch on 2026-08-24 the guest CLI returned in **0.4–0.5 s** and the browser in
**3.2–4.9 s**, so on raw fetch time **the guest CLI is the faster of the two.** The
phase-level 23 minutes was `delay_seconds: 4` between requests plus whatever the CLI was
contending with that day, not per-fetch cost.

This matters enough to state plainly because it undercuts the original reason for
switching. What the browser still buys:

- **The real posting**, not the public guest rendering — a stronger guarantee about what
  the gates are reading, and ~1% more text.
- **A second independent client.** `.description__text` is public markup the CLI scrapes
  and LinkedIn can change; the logged-in DOM is the product itself.

What it does not buy: request budget (same host, same IP) and, on this evidence, not
speed either. Both paths are kept because two independent clients for the same data are
worth more than either alone — if the guest markup changes the browser still works, and
if the browser stack is down the CLI still works. **Any future claim that one path is Nx
faster than the other should be re-measured per fetch before it goes in a commit
message.**

The text-volume gap is small and was measured, not assumed — the guest CLI is not
returning snippets. **A widely repeated claim that it returns ~500 chars and left rows at
`evidence_chars: 0` is wrong**; probing all 11 rows through the guest path on 2026-08-24
returned full bodies for every one (2,502–10,132 chars). The zeros were budget
starvation — `detail_enrich_budget` cutting the target list — not fetch failure.

### The accepted regression: no structured fields on the browser path

`DETAIL_FIELDS = ("seniority", "employmentType", "jobFunction", "industries",
"applyUrl")` come only from the guest CLI's JSON. The browser returns description text.
Rows enriched by the browser therefore carry **no `seniority` field**.

This is an accepted loss, decided deliberately rather than discovered. The seniority
gate falls back to reading the body text, which the browser supplies in full and
slightly more of, so the gate keeps working; what is lost is the tidier structured
signal. Worth noting that the guest CLI returned `seniority: "Not Applicable"` for all
three postings probed on 2026-08-24, so the structured field is often empty of signal
anyway. Revisit only if seniority verdicts measurably degrade.

## Traffic budget

`linkedin.max_requests_per_run: 60` is a **hard stop on the whole run's LinkedIn
traffic**, searches plus enrichment, because both hit the same host minutes apart.
`build_search_plan.py` reserves `detail_enrich_budget` out of it and
`run_daily.sh` re-derives the same subtraction as a defence-in-depth check.

**Switching to the browser does not buy request budget.** A logged-in browser and the
guest CLI are different clients but the same host and the same IP. It buys speed and a
little text, nothing more, and enrichment still counts one request per job.

Current setting, raised 2026-08-24: **`detail_enrich_budget: 25`**, so 35 searches +
25 details = exactly 60.

Why 25 and not the 30 originally asked for — the pipeline named the number itself, in
`logs/daily/2026-08-24.log:166`:

> `enrich: 10 of those are inside the top 25 with unverified language/experience gates
> — they will be ranked as UNKNOWN. Raise linkedin.detail_enrich_budget to at least 25
> to verify every reachable job in the cut.`

25 verifies **every reachable job in the cut**. Above that the still-unverified rows
are non-LinkedIn cards or bodies the pre-ranker already read, which no budget can
reach, so 30 would cost 5 more searches and buy nothing. 25 is also the ceiling
`tests/test_search_plan.py::test_enrichment_budget_is_bounded` enforces.

> **Do not split the reserve across two config keys.** Reserving 15 in the planner
> while enrichment spends 30 was considered and rejected: `run_daily.sh` computes
> `LINKEDIN_SEARCH_CAP=$((LINKEDIN_CAP - ENRICH_BUDGET))` from the same key and
> `exit 1`s when the plan exceeds it. A planner emitting 45 searches against a
> shell-computed cap of 30 is a **FATAL abort at Phase 1** — no searches, no jobs, no
> Telegram — and it would also put the day at 75 requests, above the agreed 60 ceiling.
> One key drives both on purpose.

## What must be running at 08:00 for the browser path

The launchd job `com.salman.jobsearch.daily` runs unattended at 08:00 Europe/Budapest.
For the authenticated path to be used, at that moment:

1. **The WebBridge daemon must be listening** on `127.0.0.1:10086`. A SessionStart hook
   starts it when Claude Code launches, which is *not* a guarantee at 08:00 — nothing
   in the launchd job starts it. Check with `~/.kimi-webbridge/bin/kimi-webbridge
   status`; start it with `~/.kimi-webbridge/bin/kimi-webbridge start` (idempotent).
   Never `stop`/`restart`/`uninstall` from automation — those fight the Kimi Desktop App.
2. **The Kimi browser extension must be connected** (`extension_connected: true`).
3. **The LinkedIn session must still be logged in.** Sessions lapse on their own.
4. **The browser must be running.** A daemon with no browser answers `list_tabs` and
   then fails every navigation.

None of these are things the 08:00 run can fix, which is the whole reason for the
fallback. It does not need them to be true — it needs to *detect* that they aren't.

### Pre-flight checks

Two functions in `scripts/linkedin_extract.py`, both of which **never raise** and both
of which return `(ok: bool, reason: str)`:

- **`daemon_reachable(timeout=5.0)`** — one local `list_tabs` call, no LinkedIn
  traffic, sub-second when healthy (measured 0.03 s). Distinguishes *unreachable*
  (daemon down) from *wedged* (`ok: false`, extension stale) because the remedies
  differ. The probe action is `list_tabs` and must stay `list_tabs`: `list_sessions`
  does **not** exist in the daemon's vocabulary, and probing with it reports a healthy
  daemon as down.
- **`session_healthy(timeout=NAV_TIMEOUT)`** — navigates the existing tab to
  `linkedin.com/jobs/`, foregrounds it, waits for mount, and reports whether the
  session is authenticated. **Costs one LinkedIn request**, so it is a run-level check
  and the caller counts it against `max_requests_per_run`. Detects an auth wall, a
  logged-out jobs page, and a CAPTCHA. A CAPTCHA reports *unhealthy* and falls back —
  it is never worked around, per standing instruction.
- **`recover_session(timeout=NAV_TIMEOUT)`** — **one** attempt to rescue a session that
  just reported unhealthy. Foregrounds the tab and hard-reloads it (`ignoreCache: true`,
  because a from-cache reload re-serves exactly the page that just failed), then re-reads
  the same probe. **Costs one more LinkedIn request.** Shares its body with
  `session_healthy` via `_health_verdict`, so the two can never disagree about what a
  signed-in page looks like.

  The only failure a reload can actually fix is a **stale render** — a tab left on a
  cached or half-mounted page, which `session_healthy` reports with the same string as a
  genuinely lapsed login because from the outside the two look alike. A reload cannot
  restore an expired cookie and nothing here tries to. Hence one attempt, not a loop:
  spending several requests on a login that has genuinely lapsed is worse than falling
  back immediately, and the guest CLI is waiting.

### The pre-flight chain, and what each rung costs

`browser_fetch_path()` in `enrich_linkedin.py` runs this **once per run, before a single
job is selected** — before, because the pre-flight *spends* requests out of the same
`detail_enrich_budget` the searches were already deducted for. Selecting 25 targets and
*then* discovering the budget is 23 would put the day over `max_requests_per_run`.

| Rung | Cost | On failure |
|---|---|---|
| Load `scripts/linkedin_extract.py` | free | guest |
| `daemon_reachable()` | free (local `list_tabs`) | try `kimi-webbridge start`, poll 3× |
| `session_healthy()` | 1 LinkedIn request | one `recover_session()` — **unless CAPTCHA**, which goes straight to guest |
| `recover_session()` | 1 LinkedIn request | guest |

Every rung logs its own decision, so a morning's log reads
`browser primary | daemon started | session recovered | fallback guest` and the final
line reports `path=… browser_jobs=… guest_jobs=… linkedin_requests=n/limit`.

**The ledger, not the job count, is the cap.** `extract()` retries a partial mount up to
`MAX_ATTEMPTS` times and *each attempt is a real navigation*, so charging one request per
job would let 24 jobs spend 72 against a cap of 60. `RequestLedger` charges actual
attempts (`Extraction.attempts` / `ExtractionError.attempts`), the pre-flight's own spend
is subtracted before targets are chosen, and a spent ledger refuses the next job rather
than exceeding the cap.

**A mid-run switch, not a per-job retry.** A login can lapse *between* jobs on an
unattended run. After `BROWSER_FAILURE_STREAK = 2` consecutive browser failures the
phase finishes on the guest CLI and the report says `fetch_path: "browser->guest"`. Two
rather than one because a single posting genuinely can fail on its own (deleted,
region-locked, slow mount) and condemning the fast path for one bad card would be an
overreaction. There is deliberately **no per-job guest retry** before that point: every
request in this phase is budgeted one-per-job, so re-fetching job 3 on the second path
would spend job 24's request. A single browser failure is the same non-fatal event a
single guest failure already is — that job keeps its snippet and the ranker says so.

Both are pinned by `tests/test_linkedin_extract.py` (25 tests), which is mostly a
failure-mode suite: the safety-critical property is that neither can throw inside
Phase 1c and take down a run that had a working fallback.

Two lessons are baked into `session_healthy` because both cost a false negative on
first live run, and both would have silently disabled the browser path forever:

- **Foreground the tab.** LinkedIn's SPA does not mount in a background tab. The first
  live probe read 1,172 chars off a hidden tab and reported a good session as
  unauthenticated. `extract()` calls `Page.bringToFront` for the same reason and its
  comment calls it "the single most important line in this module."
- **Read the nav text, not class names.** Every `global-nav__*` CSS selector the check
  first shipped with matched nothing on 2026-08-24 against a session that was
  demonstrably logged in. The detector now requires 2 of 3 authenticated nav labels
  (*My Network*, *Messaging*, *Notifications*), which are product surface rather than
  markup and churn far more slowly. Live result after the fix: `LinkedIn session
  authenticated (3/3 nav labels)` in 3.2 s.

### Persistent auth — where the session lives, and why there is no credential

**There is no LinkedIn username or password anywhere in this repo, and none is wanted.**
Not in `config/search_matrix.json`, not in an env var, not in the keychain, not in code.
`tests/test_enrich_browser_path.py::PipelineWiring::test_no_linkedin_credential_is_anywhere_near_this_path`
fails the build if one appears.

The session persists because the browser the pipeline borrows is **your real browser,
owned by the Kimi Desktop App**, and its profile is on disk the same way it is when you
use it by hand. That is the whole persistence mechanism: the pipeline does not create a
browser, so it does not manage a profile.

> **A deviation from the integration brief worth knowing about.** The brief asked for
> the pipeline to *"spawn the agent-browser process (use persistent user-data-dir)"*.
> That is the wrong tool for this stack and was not implemented. `agent-browser` is
> Vercel's headless Rust CLI: it cannot serve the WebBridge protocol on port 10086, and
> it starts **logged out**, so it could never see an authenticated posting. The daemon
> this path needs is `~/.kimi-webbridge/bin/kimi-webbridge`, which attaches to the
> already-running desktop browser — and therefore has **no `--user-data-dir` for the
> pipeline to pass**. `start_daemon()` runs `kimi-webbridge start` and nothing else.

**Re-authenticating, when the session lapses.** Nothing automated will do this, by
design — the fallback exists precisely so that a lapsed login costs speed rather than a
run:

1. Open LinkedIn in your normal browser (the one the Kimi Desktop App drives) and log in.
2. Confirm the pipeline can see it:
   `python3 -c "import importlib.util as u; s=u.spec_from_file_location('lx','scripts/linkedin_extract.py'); m=u.module_from_spec(s); import sys; sys.modules['lx']=m; s.loader.exec_module(m); print(m.session_healthy())"`
   A healthy session prints `(True, 'LinkedIn session authenticated (3/3 nav labels)')`.
   That probe costs one LinkedIn request.
3. Nothing to re-run. The next scheduled run picks the browser path up on its own.

If a **CAPTCHA** appears, clear it yourself in the browser. The pipeline will not touch
it: `browser_fetch_path()` short-circuits to the guest CLI on a CAPTCHA verdict without
even attempting the reload, because reloading a challenge page is the first step of
hammering it.

### Manual daemon start, when the pipeline cannot do it

`start_daemon()` tries `kimi-webbridge start` (the only lifecycle verb automation may
use — `stop`/`restart`/`uninstall` fight the Desktop App) and then polls the port three
times before giving up. It succeeded when tested from a shell on 2026-08-24, and the
launchd job is a **LaunchAgent** (`~/Library/LaunchAgents/com.salman.jobsearch.daily.plist`),
so it runs inside your GUI session rather than a sandboxed system context — the spawn is
plausible at 08:00.

But the daemon is only half the dependency, and launchd cannot supply the other half:
**it cannot start the Kimi Desktop App or its browser extension.** A daemon with no
browser answers `list_tabs` and then fails every navigation. So if the desktop app is
not running at 08:00, `start_daemon()` may well succeed and `session_healthy()` will
still fail — which is exactly why the session check is a separate rung rather than
something the daemon check implies.

To fix it by hand:

```sh
~/.kimi-webbridge/bin/kimi-webbridge status   # extension_connected, running, version
~/.kimi-webbridge/bin/kimi-webbridge start    # idempotent, safe to repeat
```

If `status` reports `running: true` but `extension_connected: false`, the daemon is up
and the browser side is not — open the Kimi Desktop App. **Do not** `stop` or `restart`
the daemon to fix this; that fights the app that owns the browser.

### The config flag and the two CLI switches

`config/search_matrix.json` → `linkedin.use_browser_extractor`. Consulted at Phase 1c
entry: `true` means browser primary with guest fallback, `false` means guest only, and
the branch decision is logged either way. Currently `true`.

Two flags on `scripts/enrich_linkedin.py`:

| Flag | Who passes it | Why |
|---|---|---|
| `--no-browser` | nobody, by default | Forces the guest CLI even when the flag is `true`. For benchmarking one path against the other. An explicit choice, so it never raises a fallback alert. |
| `--alert-on-fallback` | `run_daily.sh`, on the unattended path only | Sends **one** Telegram alert per run if the browser path is unavailable. Manual runs stay silent so a benchmark does not ping the phone about a condition the operator is already watching. |

## Troubleshooting

**If the browser is down, jobs still flow via guest HTTP.** That is the design, not a
degraded mode to be alarmed by: the guest CLI returns full posting bodies (11/11 rows,
2,502–10,132 chars, measured 2026-08-24) and every gate keeps working on them. The
visible cost is time — Phase 1c goes from roughly 2.5 minutes to roughly 23 — and the
absence of the browser's extra ~1% of text.

| Symptom | Cause | What to do |
|---|---|---|
| `cannot reach the WebBridge daemon at http://127.0.0.1:10086/command` | Daemon not running | `~/.kimi-webbridge/bin/kimi-webbridge start`. The run already fell back; nothing to re-run. |
| `daemon answered but rejected list_tabs` | Extension stale or wedged | Check `kimi-webbridge status` for `extension_connected` and the version match. Do not restart the daemon from automation. |
| `LinkedIn session is not authenticated (auth wall on /jobs/)` | Login lapsed | Log in to LinkedIn in the real browser. Until then every run uses the guest path. |
| `CAPTCHA on the LinkedIn session probe` | Challenge on the page | Open LinkedIn yourself and clear it. Nothing in the pipeline will attempt to pass it. |
| `no auth wall but no authenticated nav either (… n/3 nav labels …)` | Page did not mount, or the nav labels changed | If `readyState=loading`, the settle window was too short (`HEALTH_SETTLE_ATTEMPTS`/`HEALTH_SETTLE_DELAY`). If `readyState=complete` with 0/3 labels on a session you know is logged in, LinkedIn renamed the nav — update the three regexes. |
| Rows show `evidence_chars: 0` / verdicts UNKNOWN | **Budget starvation, not fetch failure** | Read the `enrich:` lines in `logs/daily/<date>.log` — the phase logs the exact budget to raise `detail_enrich_budget` to. Switching fetch paths will not fix this. |
| Phase 1 aborts with `FATAL: plan asks for N LinkedIn searches` | Planner and shell disagree on the reserve | One key drives both. Do not add a second reserve key — see the warning under **Traffic budget**. |

### Verdicts can drift between the two paths

The gates are sensitive to sentence segmentation, so a whitespace difference between
fetch sources can move a verdict. On 2026-08-24, 10 of 11 rows agreed; **Stryker**
flipped browser `FAIL@4` → guest `PASS@4` because a 38-char difference moved a sentence
boundary and joined *"or equivalent demonstrated experience"* to *"4 years minimum"*.
It failed open. This is accepted drift, not a bug to chase — but it means a verdict
should be read together with which path fetched it.

## Files

| Path | Role |
|---|---|
| `scripts/enrich_linkedin.py` | Phase 1c. `fetch_detail()` (guest CLI), `merge_detail()` (host-aware cap), `select_targets()` (who gets a request). **The dual-path branch goes here.** |
| `scripts/linkedin_extract.py` | Browser extractor + `daemon_reachable()` / `session_healthy()`. |
| `tests/test_linkedin_extract.py` | Health-check guards, offline, no requests. |
| `config/search_matrix.json` | `max_requests_per_run`, `detail_enrich_budget`, `use_browser_extractor`. |
| `scripts/build_search_plan.py` | Reserves the enrichment share out of the cap. |
| `scripts/run_daily.sh` | Phase 1c invocation (~line 513) and the independent cap check (~line 376). |
| `docs/PHASE_B_BROWSER_VERIFICATION.md` | A *different* feature — browser verification of the ranked set, publishing `browser_verified` beside the gate verdicts. |
