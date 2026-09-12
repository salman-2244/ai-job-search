---
tags: [note, architecture]
---
# 01 - Pipeline Architecture

The complete flow of one pipeline run, driven by `scripts/run_daily.sh` (~1,700 lines,
the master orchestrator). Parent note: [[00 - Project Overview]]. Guardrail detail:
[[03 - Business Rules and Guardrails]]. Telegram surfaces: [[04 - Telegram Bot Features]].

```
┌────────────────────────────────────────────────────────────────────┐
│ 08:00 launchd (com.salman.jobsearch.daily) → run_daily.sh          │
├────────────────────────────────────────────────────────────────────┤
│ Phase 0b  linkedin_alerts.py ── IMAP ──> alert jobs + store        │
│ Phase 1   build_search_plan.py → portal CLIs (bun) → aggregate.py   │
│              ~300-500 unique jobs (corpus)                          │
│ Phase 1b  prerank_jobs.py --stage shortlist → 80 jobs              │
│           (two_axis_score.py scoring + hard_gates.py, free)         │
│ Phase 1c  enrich_linkedin.py → full posting bodies (≤25 requests)  │
│           tiers: WebBridge browser → Playwright → guest CLI         │
│ Phase 1b- prerank_jobs.py --stage final → rankset (25 jobs)        │
│ final     (re-score with bodies; every cut job deferred w/ reason)  │
│ Phase 2   rank_jobs_api.py → ONE Messages API call → 25 scored      │
│           (weights: technical .30, experience .25,                  │
│            behavioral .15, career .30)                              │
│ Phase 2b  gate_jobs.py → deterministic gate ≥75 / ≥60-alert, cap 5  │
│ Phase 3   write_selection_handoff.py + launchctl kickstart the      │
│           selector_listener.py (separate launchd job)              │
│ Phase 4-5 QA + report → reports/daily/YYYY-MM-DD.md + tg-notify    │
├────────────────────────────────────────────────────────────────────┤
│ LATER (hours later, owner's phone):                                 │
│ selector_listener.py posts 25 job cards on Telegram → owner picks   │
│   → telegram_select.generate_one → `claude -p` per job →           │
│   CV + cover letter PDFs → job_search_tracker.csv rows              │
└────────────────────────────────────────────────────────────────────┘
```

## Phase 1 — Job Fetching

**Files:** `run_daily.sh:490-678` · `build_search_plan.py` · portal CLIs · `aggregate_jobs.py`

1. **Plan** (`build_search_plan.py`): turns `config/search_matrix.json` into a TSV of
   CLI invocations. LinkedIn rotates 13 track-queries × 18 geos against a **hard cap of
   `max_requests_per_run: 90`** (searches + enrichment together; searches get cap −
   `detail_enrich_budget: 25` = 65). `always_include_geos: ["Hungary"]` runs daily; the
   rest rotate on a deterministic date-keyed window. `--geo NAME` narrows to one
   country (the `/run <geo>` Telegram path); `--list-geos` feeds the bot's buttons.
2. **Fetch** (`run_portal()` in run_daily.sh): runs each portal CLI via `bun run
   .agents/skills/<portal>-search/cli/src/cli.ts`, sequentially with `delay_seconds: 4`
   between LinkedIn calls, each under a 300s wall-clock watchdog (deadline-based —
   sleep accumulators were a real bug). A tripped query is abandoned for the run, not
   retried, because a retry would spend a later query's request.
3. **Sources:** linkedin-search (guest jobs API), freehire (REST aggregator),
   arbeitnow, weworkremotely, plus linkedin-alert (Phase 0b email ingestion).
   Four Danish portal CLIs (jobindex, jobnet, jobbank, jobdanmark) exist as skills but
   are not in the active matrix.
4. **Aggregate** (`aggregate_jobs.py`): normalizes schemas, dedups by canonical key
   (`url:linkedin:<id>` for LinkedIn, `url:<clean-url>` otherwise, `ct:<company>|<title>`
   fallback), sorts newest-first by `date_posted`, publishes `dedup_key` on each job.
   Alert attribution survives dedup because the glob sorts `linkedin-alert` first.

**Run-state files:** `job_scraper/seen_jobs.json` (every ranked key with
score/verdict/date — no longer a filter, only the `🔁 seen X runs ago` marker),
`job_scraper/alert_matched.json` (alert keys + `first_alerted` dates, the 30-day
expiry authority), `/tmp/jobsearch_*` intermediates (cleaned in Phase 7 unless
`KEEP_TEMP=1`; Stage 3 runs keep durable inputs under `STAGE3_RESUME_ROOT`).

## Phase 1b/1c — Pre-rank and Enrichment (the funnel)

**Why:** Phase 2 costs ~68s per job (measured 2026-08-19: 1,696s for 25). Ranking all
500 would take 3.5h+ and did — twice. So the corpus is narrowed for free first.

- **`prerank_jobs.py`** runs in two stages with enrichment between:
  `--stage shortlist` cuts to 80 (wide, so enrichment can read bodies), `--stage final`
  cuts to 25 (`deep_rank_budget`). Both stages annotate every job with
  `prerank` (score, track, reason, gates) so **nothing is silently dropped** — the
  deferred list records every cut with its reason.
- **`two_axis_score.py`** (the active model, `scoring.enabled: true`): scores
  business-domain categories and AI/data enabler categories separately, rewarding
  overlap (the "hybrid bonus" — a business+AI role outranks pure research structurally).
  Weights live in the matrix, not code. Core-tech titles without a business domain get
  a −60 penalty.
- **`hard_gates.py`** (see [[03 - Business Rules and Guardrails]]): six deterministic
  gates — language, experience, sponsorship, seniority, pure-technical, closed-posting —
  run before budget division. Verdicts: PASS / FAIL / **UNKNOWN** (thin evidence —
  never treated as fail; UNKNOWN *inside the rank cut* is exactly what enrichment buys).
- **Selection is not top-N:** `per_track_floor: 2` guarantees slots per attributed
  track (anti-monopoly for T4/T5 vs T1); alert jobs claim up to `alert_budget: 10`
  before the score cut; near-duplicates collapse on Jaccard role-signature ≥ 80%;
  tie-breaks prefer alert-matched → Hungary/remote (no new permit) → newest posting.
- **`enrich_linkedin.py`** (Phase 1c) allocates ≤25 LinkedIn `detail` requests by an
  explicit priority: (0) unverified-gate jobs inside the rank cut, by prerank score;
  (1) half-hybrid cards (domain-without-enabler or vice versa — the missing half is in
  the body or nowhere); then alert-sourced, repeats last. Three fetch tiers:
  1. **Kimi WebBridge browser** (`linkedin_extract.py`, ~15× faster, authenticated)
  2. **Playwright storage-state** (`linkedin_playwright.py`, opt-in tier 3 — see
     [[05 - Playwright and Headless Mode]])
  3. **Guest CLI** (`fetch_detail`, always works, slower)

  Fallback is automatic and non-fatal; a `RequestLedger` enforces the shared cap across
  all tiers (each navigation is charged before it happens).

## Phase 2 — Ranking (the only paid LLM call)

**File:** `rank_jobs_api.py` (465 lines) · prompt: `prompts/pipeline_phase1_rank.md`

- One **direct HTTP POST** to an Anthropic-compatible `/v1/messages` endpoint
  (`ANTHROPIC_BASE_URL` → default `https://api.anthropic.com`), model from
  `ANTHROPIC_MODEL` (read from `.claude/settings.json` `env` block; fallback model
  from `ANTHROPIC_FALLBACK_MODEL`). Stdlib `urllib` only; 4,096 max tokens.
- The prompt wraps **trusted** context (evaluation rules, candidate profile, seen
  state, alert store, tracker) and **untrusted** postings in an `<untrusted_jobs>`
  envelope with escaped JSON — prompt-injection defense (postings are data, never
  instructions).
- The model returns per-job decisions: `score` (4 dimensions 0–100 + track +
  strengths/gaps) or `drop` (with reason). `validate_decisions()` strictly checks that
  returned keys exactly match input keys, scores are numbers 0–100, gates are
  PASS/FLAG/FAIL.
- The **wrapper** (never the model) computes overall =
  technical×0.30 + experience×0.25 + behavioral×0.15 + career×0.30, verdicts
  (Strong ≥75 / Good ≥60 / Moderate ≥45 / Weak ≥30), and alert-matching from
  `alert_matched.json` — the model cannot hallucinate a gate.
- Outputs written atomically (tmp + fsync + rename, mode 0600): ranked list
  (`$TOP5_FILE`), not-drafted list, updated seen store.
- Shell-side retry policy (`run_daily.sh:902-1101`): up to 3 attempts, 20s→40s backoff,
  **no retry on timeout** (it already spent the 1,800s budget; plist sets 4,800s), no
  retry on auth/quota-class errors, fallback model only on gateway-unavailable class.

## Phase 2b — The deterministic gate

**File:** `gate_jobs.py` — re-applies in code what the prompt merely states:
`score ≥ 75` → draft, `score ≥ 60 AND key live in alert store` → draft, else report.
The alert store is the **authority** — the ranker's `alert_matched` claim is overwritten.
30-day expiry on alerts, fail-closed on undatable entries. Cap: `max_jobs_to_apply: 5`
per run (over-cap jobs join the not-drafted list with a note). Rejections always land
in the report's "Matched but Not Drafted" table.

## Phase 3 — Telegram selection handoff

`write_selection_handoff.py` atomically writes `/tmp/jobsearch_pending_selection.json`
(today, rankset path, and for Stage 3 runs: run_id + output_root), then
`launchctl kickstart -k gui/$(id -u)/com.salman.jobsearch.selector` starts the
listener job. The marker is the **only authorization** to post a list — a bare launchd
start (KeepAlive) finds no marker and exits 0. `selector_listener.py` then posts the 25
job cards, holds `getUpdates` for up to 20h, and on Submit drafts documents.
See [[04 - Telegram Bot Features]].

## Phase 4/5 — QA and report

Phase 4 (QA via `claude -p` with `pipeline_phase3_qa.md`) and the document table are
mostly vestigial now that drafting happens in the listener — `run_daily.sh` writes a
synthetic empty document set for them to read. Phase 5 generates
`reports/daily/<date>.md` inline (Python heredoc): warnings first, funnel
(fetched/shortlisted/ranked/cleared), per-portal counts, per-alert breakdown, deferred
histogram with closest misses, **gate-verification table** (pass/fail/unverified with
evidence source — the fail-open fix), cleared-gate top 5, not-drafted table.

## Phase 7 — Cleanup and notification

Temp files removed (today's rankset deliberately kept for the selector; previous days'
aged out), the EXIT-trap Telegram ping (`tg-notify`) fires on **success and failure
alike** (a six-hour silent IMAP hang motivated this), lock released.

## Stage 3 — on-demand runs

`stage_3/orchestrator.py` supervises exactly one `run_daily.sh` child at a time
(own process group, PID+start-time identity, manifests under `~/.jobsearch-stage3-runs`,
3h runtime ceiling, SIGTERM→SIGKILL escalation) and `stage_3/bot.py` is the Telegram
command surface. Scheduling, progress rendering, and crash-safe resume are all in
`stage_3/` — see [[04 - Telegram Bot Features]].

## Phase 4 (interactive) — Document generation

When the owner presses Submit, `telegram_select.generate_one()` spawns
`claude -p prompts/selected_job_draft.md` (one job per process, **sequential** — the
API relay pre-consumes a ~$0.65 hold per request, so parallel jobs starve each other).
The drafter reads the active templates, tailors, compiles with lualatex, visually
verifies PDFs, runs `pdftotext` ATS checks, archives the posting verbatim, and
reports JSON. The coordinator (never the drafter) appends tracker rows. Stage 3 runs
write to `cv/<date>/<run_id>/<slug>/`; legacy runs to `cv/<slug>/`. Complete legacy
applications are read-only — never regenerated or deleted.

`generate_batch.py` / `watch_generation.py` are an older sequential-batch + terminal
dashboard path (still functional); `optimize_documents.py` / `optimize_content.py` /
`apply_optimizations.sh` are one-time bulk LaTeX fixers. See [[06 - File by File Analysis]].

## Vault graph

Phase files: [[run-daily]] · [[build-search-plan]] · [[aggregate-jobs]] · [[prerank-jobs]] · [[enrich-linkedin]] · [[rank-jobs-api]] · [[gate-jobs]] · [[selector-listener]] — full flow: [[_Pipeline Flow]]
