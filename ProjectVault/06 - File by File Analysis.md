---
tags: [note, files]
---
# 06 - File by File Analysis

Every file in the project, by directory. Generated apps under `cv/`, `cover_letters/`,
`documents/applications/`, `reports/daily/`, `logs/daily/` are run artifacts, grouped
rather than listed per company (50+ applications each with 4 files). Parent:
[[00 - Project Overview]] · issues feed [[07 - Known Issues and Bugs]].

## Root

| File | What it does | Depends on / depended on by |
|---|---|---|
| `CLAUDE.md` | Candidate profile + workflow rules + verification checklist; Claude Code's project instructions | Read by every Claude session; the prompt ranker gets `04-job-evaluation.md` + `01-candidate-profile.md` instead |
| `README.md` | Upstream-facing framework README (fork of MadsLorentzen's) | Human readers |
| `AGENTS.md` / `SETUP.md` / `CONTRIBUTING.md` / `SECURITY.md` / `CHANGELOG.md` / `LICENSE` | Agent-tool entry point, setup guide, contribution policy, threat model, history, MIT license | Humans |
| `.gitignore` | Ignores all personal/run state (CVs, tracker, seen_jobs, reports, logs, automation.json, settings.json, combined JSONs) | Enforced by `tools/security_guards.py` |
| `requirements-stage3.txt` | Exactly `python-telegram-bot==22.8`, `playwright==1.62.0` | Stage 3 runtime |
| `salary_lookup.py` (429 ln) | Salary benchmarking lookup (BYO JSON data) | `tools/convert_salary_excel.py`, tests |
| `job_search_tracker.csv` | Application tracking spreadsheet (date, company, sector, role, role_type, channel, status, contact_person, fit_rating, notes, cv_file, cover_letter_file, source) — gitignored but present | Appended by `telegram_select.append_tracker` / `generate_batch.py`; read by ranker context, `/html-report`, `/outcome` |
| `freehire-combined-results.json` / `weworkremotely-combined.json` | One-off combined scrape dumps (Aug 16) — gitignored | Historical only |
| `main_eaton_*.{aux,log,out}`, `cover_eaton_*`, `template.*`, `texput.log` | Stray LaTeX build artifacts in root | Cleanup candidates (see [[07 - Known Issues and Bugs]]) |
| `com.salman.jobsearch.daily.plist` / `com.salman.jobsearch.selector.plist` | launchd templates with placeholders | Rendered by `install_scheduler.sh` |
| `.DS_Store` files | macOS noise | — |

## `scripts/` — the pipeline

| File | Purpose (ln) | Imports/depends on | Depended on by |
|---|---|---|---|
| `run_daily.sh` | Master orchestrator, ~1,700 ln: config guards, lock, Phases 0b–7, retry policy, report generation, cleanup, notify trap | Every script below; `bun`, `tg-notify`, `launchctl` | launchd daily plist; Stage 3 orchestrator (spawns it) |
| `build_search_plan.py` (283) | Matrix → TSV of CLI invocations; LinkedIn rotation arithmetic; `--list-geos` for bot buttons | `search_matrix.json` | `run_daily.sh` Phase 1; `stage_3/bot._load_geos` |
| `aggregate_jobs.py` (260) | Portal JSONs → unified corpus; dedup keys; newest-first sort | — (stdlib) | `run_daily.sh`; imported by `enrich_linkedin`, `prerank_jobs`, `migrate_seen_jobs` (for `LINKEDIN_JOB_ID`) |
| `hard_gates.py` (1,347) | Six deterministic gates; pure module, no I/O | — (stdlib re/unicodedata) | `prerank_jobs` (`evaluate`), tests |
| `two_axis_score.py` (415) | Domain/enabler category scorer; weights from matrix | matrix `scoring` block | `prerank_jobs` (`ScoringModel`), tests |
| `prerank_jobs.py` (1,336) | Two-stage selection (shortlist 80 / final 25); track floors; alert slots; near-dup collapse; repeat markers; corpus join | imports `enrich_linkedin` (`title_match_score`), `gate_jobs` (`live_alert_keys`), `two_axis_score`, `hard_gates` by path | `run_daily.sh` Phases 1b/1b-final |
| `enrich_linkedin.py` (1,501) | Phase 1c: 3-tier detail fetching with `RequestLedger`, verification-first allocation, merge/degrade rules | `aggregate_jobs`, `linkedin_extract` (lazy), `linkedin_playwright` (lazy), portal CLI, `tg-notify` | `run_daily.sh` Phase 1c; `prerank_jobs` imports it |
| `linkedin_extract.py` (625) | Tier-1 browser extraction via Kimi WebBridge daemon (`127.0.0.1:10086`); semantic-id selectors; foregrounds tabs | WebBridge daemon | `enrich_linkedin` (lazy import) |
| `linkedin_playwright.py` (370) | Tier-3 provider; typed errors; 0600 enforcement; lazy `playwright` import | playwright (lazy) | `enrich_linkedin` (lazy) |
| `linkedin_session.py` (194) | Terminal-only headed session provisioner; `--show` describer | playwright (headed) | Operator, manually |
| `linkedin_alerts.py` (648) | Phase 0b: IMAP alert ingestion → portal file + `alert_matched.json` store; URL rebuild from numeric ids | `automation.json` creds, matrix alerts block | `run_daily.sh` Phase 0b |
| `rank_jobs_api.py` (465) | Phase 2: direct Messages API call, strict validation, atomic outputs, seen-store update | `urllib` only; env/settings for creds | `run_daily.sh` Phase 2 |
| `rank_jobs.py` (707) | **Legacy** earlier ranker (reads /tmp rankset, writes rank output) — superseded by `rank_jobs_api.py` | — | Kept for reference; see [[07 - Known Issues and Bugs]] |
| `gate_jobs.py` (368) | Phase 2b deterministic gate + cap + alert expiry + `--prune` | `automation.json`, `alert_matched.json` | `run_daily.sh` Phase 2b; `prerank_jobs` (live_alert_keys) |
| `telegram_select.py` (1,069) | Selector library: `JobRow`, render, `SelectionState`, `generate_one` (`claude -p`), run-scoped paths, tracker append; plus its own interactive entry | PTB (lazy), `prompts/selected_job_draft.md` | `selector_listener` (imports whole module); manual runs |
| `selector_listener.py` (755) | launchd-driven selection listener; handoff marker; crash-resume; poller-death detection; deliberate exit codes | imports `telegram_select` by path | launchd selector plist; `run_daily.sh` Phase 3 kickstart |
| `write_selection_handoff.py` (65) | Atomic JSON handoff writer (mode 0600) | — | `run_daily.sh` Phase 3 |
| `generate_batch.py` (591) | Sequential batch drafter with state file + quota abort | imports `telegram_select` pieces; `claude -p` | `watch_generation`; manual |
| `watch_generation.py` (786) | Terminal dashboard: S/X/R/A/Q controls; 403 quota guard | imports `generate_batch` | Manual |
| `optimize_documents.py` (268) / `optimize_content.py` (226) / `apply_optimizations.sh` | One-time bulk LaTeX fixers (awards removal, summary/skills templates) | — (regex on files) | Manual; see [[07 - Known Issues and Bugs]] |
| `migrate_seen_jobs.py` (205) | One-time seen_jobs key canonicalization (raw URLs → `url:linkedin:<id>` etc.) | `aggregate_jobs` | Ran once (2026-08-18) |
| `send_email.py` (225) | SMTP digest mailer (Phase 6 retired; kept for the credential block) | `automation.json` | Nothing live |
| `install_scheduler.sh` / `uninstall_scheduler.sh` / `render_launchd_plist.py` (50) | Install/remove both launchd jobs for this physical checkout (worktree- and space-safe; 0600 outputs; picks `STAGE3_PYTHON` → `.venv` → `python3`) | the two root plist templates, `plistlib` | Operator; CI does not run these |

## `stage_3/`

| File | Purpose | Notes |
|---|---|---|
| `__init__.py` | Package marker | |
| `config.py` (339) | Env-file parsing, token isolation, allowlist, `child_env` sanitization, redacted repr | No secret ever logged |
| `orchestrator.py` (1,003) | Run supervision: ids, manifests, process groups/identity, reattach, cancel escalation, runtime ceiling, retention | The only place `run_daily.sh` is spawned |
| `bot.py` (880) | PTB application: commands, callbacks, progress editor, scheduler loop, notification wiring | |
| `progress.py` (164) | Pure log-line → RunState projection (phases, weights, stall detection) | |
| `render.py` (212) | Telegram HTML rendering + keyboards; escapes everything | |
| `schedules.py` (340) | Crash-safe cron store (atomic + `.bak`), Vixie-cron parser, `due`/`next_run` | |
| `diagnostics.py` (95) | Secret-safe logging: token-redacting previews, 0600 file handler, 4096-char truncation | |

## `config/` · `prompts/` · `job_scraper/`

- `config/search_matrix.json` — see [[02 - Configuration and Settings]]
- `config/automation.json` (gitignored) + `automation.json.example` — pipeline switches + email creds
- `prompts/pipeline_phase1_rank.md` — ranker prompt (strict JSON, trust boundary, gates, dimensions)
- `prompts/selected_job_draft.md` — one-job drafter prompt (templates, grounding audit, ATS check, JSON out)
- `prompts/pipeline_phase2_draft.md` / `pipeline_phase3_qa.md` — older batch-draft + QA prompts (batch path)
- `prompts/batch3-scores.json` — one-off archived scores
- `job_scraper/seen_jobs.json` — `{"seen": {key: {status, rank_score, rank_verdict, rank_date, location, portal, track, alert_matched, url}}}` (gitignored) + `.bak` + `.pre-migration-*` backups
- `job_scraper/alert_matched.json` — `{key: {first_alerted: YYYY-MM-DD, …}}` (gitignored)

## `tools/`

`security_guards.py` (307) — CI guards: permission allowlist, gitignore rules, package manifest checks. `lint_skills.py` (122) — skill/command frontmatter lint (needs PyYAML). `verify_pdf.py` (104) — pdfinfo/pdftotext checks. `robots_check.py` (142) — RFC 9309 parser gating the 09-web-research curl retry. `check_upstream_updates.py` (196) — framework-version diff vs upstream. `upstream_triage.py` (227) — behind-commit triage report (weekly workflow). `check_framework_version.py` (147) — version-stamp consistency (upstream CI). `convert_salary_excel.py` (332) — Excel→JSON for the salary tool. `README_SALARY_TOOL.md` — salary data format.

## `tests/` — 49 files, ~1,466 test functions

Biggest: `test_prerank_jobs.py` (152 fns), `test_hard_gates.py` (142), `test_enrich_linkedin.py` (121), `test_enrich_browser_path.py` (65), `test_two_axis_score.py` (58), `test_search_plan.py` (58), `test_ranker_calibration.py` (58), `test_gate_jobs.py` (65), `test_linkedin_alerts.py` (66). Stage 3: `test_stage3_{bot,config,orchestrator,outputs,progress,render,schedules,regression,docs}.py`. Telegram: `test_telegram_select.py` (53), `test_selector_listener.py` (50), `test_selector_resilience.py`, `test_telegram_integration.py`, `test_notification_{diagnostics,e2e}.py`. Ranking: `test_rank_jobs_api.py`, `test_rank_command.py`. Others cover tools (lint, security guards, robots, verify_pdf, salary), skills (upskill, readme assets), migrations, tracker vocabulary, html-report/notion-sync/gmail-sync/outcome commands. `fixtures/sandbox_rankset.json` + `__init__.py`.

## `.agents/skills/` — 8 portal CLIs (Bun/TS, zero runtime deps)

`linkedin-search` (guest jobs API — the one the pipeline uses), `freehire-search` (REST aggregator), `arbeitnow-search`, `weworkremotely-search` (in the active matrix), and 4 Danish demos (`jobindex`, `jobnet`, `jobbank`, `jobdanmark` — installed but not in the matrix). Each: `SKILL.md` (frontmatter + `enabled:`), `cli/` (src, tests, `package.json`, `bun.lock`, `tsconfig.json`, node_modules for dev types only), some with `url-reference.md`.

## `.claude/`

- `skills/job-application-assistant/` — `SKILL.md` + 9 numbered references: `01-candidate-profile.md`, `02-behavioral-profile.md`, `03-writing-style.md`, `04-job-evaluation.md`, `05-cv-templates.md`, `06-cover-letter-templates.md`, `07-interview-prep.md`, `08-application-forms.md`, `09-web-research.md`; the candidate's single source of truth
- `skills/job-scraper/` — `/scrape` orchestration + `search-queries.md` + its own tracker copy
- `skills/upskill/` — gap analysis skill
- `commands/` — 15 slash commands: `apply.md`, `setup.md`, `rank.md`, `automation.md`, `add-portal.md`, `add-template.md`, `expand.md`, `gmail-sync.md`, `html-report.md`, `interview.md`, `linkedin-alerts.md`, `notion-sync.md`, `outcome.md`, `reset.md`
- `agents/gemini-research-expert.md` — a research subagent definition
- `settings.json` (gitignored, gateway env) + `settings.local.json`; `worktrees/stage-3-completion/` — leftover git worktree; `scheduled_tasks.lock`

## `docs/`

`TELEGRAM.md`, `STAGE_3.md`, `STAGE_3_HANDOFF.md`, `STAGE_3_RESUME.md` (historical), `STAGE_3_COMPLETION_REPORT.md`, `GATE_TRIBUNAL.md` (gate forensics), `BROWSER_ENRICHMENT.md` (dual-path design), `LINKEDIN_SELECTOR_FINDINGS.md`, `PHASE_B_BROWSER_VERIFICATION.md` (confirmed, not built), plus `superpowers/specs/2026-08-18-linkedin-job-discovery-design.md`, `superpowers/specs/2026-09-08-stage-3-completion-design.md`, and `superpowers/plans/2026-09-08-stage-3-completion.md`.

## `.github/`

`workflows/ci.yml` — lint, security-guards, `python -m unittest discover`, dependency-review (PR, graceful-skip), LaTeX smoke compiles (lualatex CV + xelatex cover example) with text-layer checks. `workflows/upstream-watch.yml` — weekly fork-behind triage issue (reports only, never merges). `FUNDING.yml`, `PULL_REQUEST_TEMPLATE.md`, `upstream-wontport.txt`.

## Generated / run-state directories (gitignored)

- `cv/` (~43 entries) & `cover_letters/` (~45): per-application `<Company>_<Role>/` with `Salman-Resume.tex/.pdf` + `Salman-Cover-Letter.tex/.pdf`; new run-scoped form `cv/<date>/<run_id>/<slug>/`; plus `cover.cls`, `cover_example.tex`, `OpenFonts/`, `main_example.tex`
- `documents/` — README + cv/linkedin/diplomas/references/applications/postings subfolders (per-application archives incl. `job_posting.md`)
- `templates/` — `cv/onepage-ats/` + `cover_letters/minimal-onepage/` (TEMPLATE.md + template.tex, placeholders only)
- `reports/daily/*.md`, `logs/daily/*.log` — one per run
- `job_scraper/` (above), `upskill/` (gitkeep), `applications/` (empty), `linkedin/` (empty), `assets/mascot/`
- `.backups/` — aggregate baseline + seen_jobs snapshots + wipe marker
- `manual_run_2026-08-19/` — one manual-run snapshot tree
- `.venv/`, `__pycache__/`, `.pytest_cache/`, `.claude/worktrees/` — environment

## Vault graph

This note summarizes; per-file notes live under `10 - File Notes/`. Complete index: [[_Master File Index]] · group notes: [[cv-generated-applications]] · [[cover-letters-generated-applications]] · [[documents-application-archives]] · [[logs-daily]] · [[reports-daily]] · [[backups-dir]] · [[manual-run-2026-08-19]] · [[worktree-stage3-completion]] · [[environment-dirs]]
