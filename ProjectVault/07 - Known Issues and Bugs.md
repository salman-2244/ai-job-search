---
tags: [note, issues]
---
# 07 - Known Issues and Bugs

Findings from the 2026-09-11 read-only analysis. Everything here is an observation,
not a fix — nothing was modified. Parent: [[00 - Project Overview]] · file refs:
[[06 - File by File Analysis]].

## Bugs and code concerns found while reading

1. **`rank_jobs.py` is dead-but-live legacy code** (`scripts/rank_jobs.py`, 707 lines).
   It reads the same `/tmp/jobsearch_rankset_<date>.json` inputs and writes a
   *different* output file (`rank_output`) with its own scoring. Nothing in
   `run_daily.sh` calls it anymore (Phase 2 uses `rank_jobs_api.py`), but it sits in
   `scripts/` fully importable and runnable; a future maintainer could invoke the
   wrong ranker. It also parses `sys.argv` at module level, so importing it has side
   effects.
2. **`selector_listener` crash-loop window**: the only deliberate nonzero exit is
   poller-died (correct under KeepAlive), but a genuinely crashed listener (unhandled
   exception in `run()`) also exits nonzero and restarts into `resume`. If the crash
   is deterministic — e.g. a corrupt rankset — `load_rankset`'s `json.loads` in
   `main()` has no try/except, so it would crash-loop every ThrottleInterval for up
   to 20h instead of exiting 0 with a message.
3. **`telegram_select.run_interactive` still uses raw `query.answer()`** — the
   stale-query `ack()` wrapper landed only in `selector_listener.py:125`. The manual
   path can lose redraws to "query too old" exactly as production did on 2026-08-23
   (three taps recorded in state, none shown in chat).
4. **Two selector state files, one token, no lock file**: manual
   `telegram_select.py` uses `/tmp/jobsearch_selection_<today>.json` while the
   listener uses `..._<run_id>.json`. A manual run started while a listener run is
   open is blocked only by Telegram's one-`getUpdates`-per-token rule (409), not by
   any filesystem lock.
5. **Root-level LaTeX build artifacts** — `main_eaton_*.{aux,log,out}`,
   `cover_eaton_*`, `template.*`, `texput.log` are untracked leftovers in the repo
   root. `.gitignore` covers the patterns; the files just sit on disk.
6. **`enrich_linkedin.browser_fetcher` charges the ledger even on unexpected
   exceptions** (`enrich_linkedin.py:974`) — correct for the cap, but a daemon-side
   bug (malformed response) burns request budget with no LinkedIn request happening.
   Conservative by design; low impact.
7. **`gate_jobs.apply_gate` sorting subtlety** (`gate_jobs.py:193-207`): the kept
   list is sorted once, then sliced for the cap; a future edit that changes the sort
   in one place but not the other would make the cap reject the wrong jobs.
8. **`stage_3/bot.scheduler_tick` swallows all exceptions** (`bot.py:693`, deliberate
   `noqa: BLE001`) — a persistently failing `store.save()` would skip schedules with
   only one Telegram ping per failure. Monitor via `stage3-bot.log`.

## TODO-style items

The codebase carries no literal `TODO` markers — it uses docstring "why" notes
instead. The genuine open ends:

- **Phase B browser verification** (`docs/PHASE_B_BROWSER_VERIFICATION.md`):
  investigation complete, **no code built** — a designed-but-unimplemented feature.
- **Browser path drops structured `seniority`** (guest CLI only) — accepted in the
  matrix's `_browser_comment`; revisit if the seniority gate ever needs structured input.
- **`max_jobs_to_apply: 5`** — over-cap jobs are reported, never drafted; the cap has
  never been revisited.
- **Four Danish portal CLIs installed but not in the matrix** — wire them in or mark
  them demos.
- **`linkedin/` and `applications/` root directories are empty** — vestigial.

## Error-handling gaps

- Corrupt selection-state JSON raises into the crash-loop described above
  (`SelectionState.load` handles missing files but not `JSONDecodeError`).
- `stage_3/orchestrator._drain` catches bare `Exception` twice (deliberate) — "no
  progress lines" can also silently mean "log file never opened".
- `send_email.py` is dead code kept alive only because `automation.json`'s email
  block holds the IMAP credential Phase 0b reuses — the credential and the script
  should be reconsidered together.
- `run_daily.sh` Phase 4 still runs `claude -p` QA on a synthetic empty document set —
  harmless, but the wide `--allowedTools` (incl. `Agent`, `WebFetch`) is broader than
  the empty task justifies.

## Hardcoded values that should arguably be configurable

| Value | Where | Note |
|---|---|---|
| `/Users/salman/Projects/ai-job-search` | `optimize_documents.py:15`, `optimize_content.py:7`, `apply_optimizations.sh:4` | Absolute home path — breaks on any other checkout/user |
| Rank weights `.30/.25/.15/.30` and verdict bands 75/60/45/30 | `rank_jobs_api.py:23,241` + `gate_jobs.py:52` + the prompt text | The same numbers live in three places (prompt, wrapper, gate) — changing one silently desyncs the others |
| Description caps 6,000 / 20,000 | `enrich_linkedin.py:136,154` | Per-host in code, not the matrix |
| `MAX_PARALLEL_JOBS = 1` | `telegram_select.py:70` | Deliberate (relay pre-consume hold); deserves a knob if relays change |
| `BROWSER_FAILURE_STREAK = 2`, `MIN_CHARS = 400`, `POLITE_DELAY (3.5, 8.0)` | `enrich_linkedin.py:766`, `linkedin_extract.py:66,61` | Tuning constants in code |
| `DEFAULT_WINDOW_SECONDS = 72000` (20h) | `selector_listener.py:91` | Env-overridable via `SELECTOR_WINDOW`, undocumented in STAGE_3.md |
| `RANK_TIMEOUT` default 1,800s vs plist's 4,800s | `run_daily.sh:161`, daily plist | Split default/override works but lives only in code comments |
| Stale "ENHANCED_SUMMARY"/"ENHANCED_SKILLS" templates | `optimize_documents.py:20`, `optimize_content.py:11` | One-shot tools predating current profile wording — running them now would rewrite CVs with stale language; treat as retired |

## Security observations (no exploitable hole found — posture notes)

1. **Strong posture overall**: secrets live outside the repo in mode-600 files,
   redacted reprs, token never in argv, strict untrusted-input boundaries
   (postings escaped everywhere, never followed for URLs), allowlist-first bots,
   atomic writes, CI guards on permissions/gitignore/manifests,
   one-getUpdates-per-token isolation with a startup check.
2. `.claude/settings.json` pre-approves bare `Bash` (unscoped) — a deliberate,
   documented decision in `tools/security_guards.py:39-68` for the unattended 08:00
   run; the guard itself records that it no longer prevents unrestricted Bash.
3. `check_permissions` on the env files warns but does not fail — a chmod drift
   surfaces only in logs.
4. Personal data on disk is gitignored but plentiful (CVs, tracker CSV, application
   archives, seen_jobs) — nothing encrypts at rest (normal for a personal Mac; worth
   remembering for any cloud-sync of the home directory).
5. The Playwright tier-3 storage state is an authenticated LinkedIn session file —
   its 0600-mode enforcement in two places is the only guard; keep it that way.

Related: [[03 - Business Rules and Guardrails]] · [[09 - Recommendations]].

## Vault graph

Issue files: [[rank-jobs-legacy]] · [[selector-listener]] · [[telegram-select]] · [[optimize-documents]] · [[optimize-content]] · [[root-latex-strays]] — hub: [[_Master File Index]]
