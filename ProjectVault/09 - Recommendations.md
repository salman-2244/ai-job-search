---
tags: [note, recommendations]
---
# 09 - Recommendations

Suggestions from the 2026-09-11 read-only analysis. Nothing here was changed —
this is advice, ranked. Parent: [[00 - Project Overview]] · the underlying findings:
[[07 - Known Issues and Bugs]].

## Needs fixing soon (highest value first)

1. **Retire `scripts/rank_jobs.py`** (superseded by `rank_jobs_api.py`). It is
   runnable, reads the same rankset inputs, and imports with side effects — the
   classic "wrong tool invoked by a future maintainer" trap. Move it to
   `docs/archive/` or delete; verify what `tests/test_rank_command.py` covers
   before removal.
2. **Add stale-query `ack()` to `telegram_select.run_interactive`** — the manual
   selector path still loses redraws to Telegram's "query too old" exactly as
   production did on 2026-08-23 (`selector_listener.py:125` already has the fix).
   One small port; closes a real UX failure.
3. **Harden `selector_listener.main()` against corrupt inputs** — wrap
   `ts.load_rankset()` and `SelectionState.load()`'s JSON parse so a corrupt rankset
   or state file exits 0 with a `[listener]` message instead of crash-looping under
   KeepAlive every ThrottleInterval for 20 hours.
4. **Single-source the rank weights and gate thresholds.** `.30/.25/.15/.30`,
   verdict bands 75/60/45/30, and the 75/60 gate currently live in three places
   (rank prompt, `rank_jobs_api.py`, `gate_jobs.py`). A drift here changes scoring
   silently. Move them to one module (or the matrix) and have the prompt template
   interpolate them.
5. **Decide the fate of the optimize_* tools.** `optimize_documents.py`,
   `optimize_content.py`, `apply_optimizations.sh` hardcode an absolute home path
   and stale "ENHANCED_SUMMARY" content that predates the current profile wording.
   Running them today would rewrite CVs with outdated language. Archive them.

## Worth optimizing (quality-of-life)

- **Deduplicate the Phase 4 vestigial block**: `run_daily.sh` still carries a QA
  `claude -p` pass that runs only when documents exist — which the synthetic empty
  document set guarantees never happens. The branch is dead weight; delete Phase 4
  and shrink the run.
- **Deduplicate selector logic**: `telegram_select.run_interactive` vs
  `selector_listener.run` share ~400 lines of handlers (guard, markup, bulk, submit).
  The listener already imports the library module; the interactive entry could
  become a thin wrapper or be removed if the listener is the only production path.
- **Clean the repo root**: stray `main_eaton_*`, `cover_eaton_*`, `template.*`,
  `texput.log` build artifacts; empty `linkedin/`, `applications/` dirs; the
  `freehire-combined-results.json` / `weworkremotely-combined.json` dumps. All
  gitignored, all cluttering `ls`.
- **Surface `SELECTOR_WINDOW` and the plist's `RANK_TIMEOUT=4800` override** in
  `docs/STAGE_3.md`'s env-var table — they're discoverable only from code comments.
- **Document a side-effect-free replay**: `RESUME=1` + `KEEP_TEMP=1` + a sandbox
  tracker (`--tracker /tmp/...`) already make a full pipeline replay possible;
  writing the exact invocation down would make on-call diagnosis faster.
- **The two-axis weights are explicitly "first-fit, not tuned"** (matrix
  `_weights_comment`). A retune pass against the per-job axis log in the reports
  is the intended next step and is config-only.

## Working well — keep exactly as is

1. **The evidence-asymmetry design in `hard_gates.py`** — partial text can convict
   but never acquit; UNKNOWN-as-target for enrichment; every FAIL carries a
   quotable phrase. This is the project's crown jewel, with a 142-test suite.
2. **Deterministic gate re-check in Phase 2b** — the LLM is told the rule, and
   `gate_jobs.py` enforces it in code anyway; `alert_matched.json` is the authority
   for alert claims. Prompt-cannot-hallucinate-a-gate is exactly right.
3. **The untrusted-input posture** — postings escaped before every render,
   JSON-escaped into the rank prompt, URLs never followed from posting text, the
   selector's current-message check, bots refusing anything an allowlisted user
   didn't send. Consistent across every surface.
4. **Secrets discipline** — everything sensitive outside the repo in mode-600 files,
   three-bot token isolation with a startup validator, redacted reprs/logs,
   `tg-notify` keeping tokens out of argv.
5. **The funnel's honesty** — nothing is silently dropped: deferred lists with
   reasons, closest-miss tables, unverified-gate surfacing in reports and Telegram,
   over-budget score ranges. Every cut is auditable.
6. **Crash-safety pattern** — atomic writes (tmp+fsync+rename) everywhere state
   matters; selector state saved per message; run manifests with process identity;
   resume inputs kept durable. Reboots genuinely resume.
7. **Watchdog discipline** — deadline-based (not sleep-accumulator) timeouts after
   the real 2026-08-22/24 incidents; timeout-not-retried rank policy; request
   ledger shared across all three fetch tiers.
8. **Test depth** — ~1,466 test functions covering the gates (142), prerank (152),
   enrichment (121+65), Stage 3 lifecycle, and the Telegram paths, all runnable
   stdlib-only in CI. Keep this ratio.
9. **CI posture** — pinned actions, read-only token, security guards on
   permissions/gitignore/manifests, fork-aware jobs (placeholder checks upstream-only),
   LaTeX smoke with text-layer verification.

## Sequencing suggestion

Fixes 1–5 above are each small (an hour or less); do them as one cleanup commit.
The optimizations are independent and can follow. Phase B (browser verification of
the deep-ranked set) remains the one *new feature* with a completed investigation
and no code — pick it up only after the cleanup, since it builds on the enrichment
tier machinery documented in [[05 - Playwright and Headless Mode]].

## Vault graph

Recommendation targets: [[rank-jobs-legacy]] · [[telegram-select]] · [[selector-listener]] · [[pipeline-phase1-rank]] · [[optimize-documents]] — hub: [[_Master File Index]]
