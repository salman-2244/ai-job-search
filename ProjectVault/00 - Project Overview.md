---
tags: [note, index]
---
# 00 - Project Overview

**Project:** AI Job Search — an automated job-application pipeline for Salman Ahmed
**Repo:** `/Users/salman/Projects/ai-job-search` (a fork of `MadsLorentzen/ai-job-search`)
**Git:** `master` branch; origin `salman-2244/ai-job-search`, upstream `MadsLorentzen/ai-job-search`
**Docs:** [[01 - Pipeline Architecture]] · [[03 - Business Rules and Guardrails]] · [[08 - Dependencies]] · [[09 - Recommendations]]

## What this project is

An end-to-end, AI-powered job-search automation system that runs on a personal Mac.
Every morning at 08:00 (Europe/Budapest) it scrapes European job portals, filters and
scores the results with deterministic gates plus one LLM ranking pass, offers the
ranked list to the owner on Telegram, and drafts tailored CV + cover-letter PDFs for
the jobs the owner picks by pressing buttons in the chat.

The pipeline's core design principle: **the machine selects and drafts; the human
decides.** Nothing is ever auto-submitted to an employer — documents land on disk and
the owner chooses whether to send them.

## Overall goal

Surface the best-fit AI / data / process-management roles across Europe for a
specific candidate profile (Junior Performance Manager at Nokia, Budapest,
non-EU national on a Hungarian permit), and turn each selected posting into a
complete, honest, ATS-ready application package.

## The main phases (one daily run)

| Phase | What happens | Key files |
|---|---|---|
| 0b | Read LinkedIn job-alert emails (IMAP) into the corpus | `scripts/linkedin_alerts.py` |
| 1 | Fetch jobs from 4 portals (LinkedIn, freehire, arbeitnow, WeWorkRemotely) | `scripts/run_daily.sh`, `build_search_plan.py`, `.agents/skills/*-search/cli` |
| 1b | Free deterministic pre-rank: score, hard gates, cut corpus 500→80 (shortlist) | `scripts/prerank_jobs.py`, `two_axis_score.py`, `hard_gates.py` |
| 1c | Enrich top LinkedIn cards with full posting bodies (browser/guest/Playwright tiers) | `scripts/enrich_linkedin.py`, `linkedin_extract.py`, `linkedin_playwright.py` |
| 1b-final | Re-score enriched shortlist, cut to 25-job rankset | `scripts/prerank_jobs.py --stage final` |
| 2 | One direct Anthropic-compatible Messages API call scores all 25 jobs | `scripts/rank_jobs_api.py`, `prompts/pipeline_phase1_rank.md` |
| 2b | Deterministic document gate re-check (score ≥ 75, or ≥ 60 if alert-matched) | `scripts/gate_jobs.py` |
| 3 | Offer ranked list on Telegram; start selector listener via launchd | `write_selection_handoff.py`, `selector_listener.py`, `telegram_select.py` |
| (4-5) | QA review + markdown report generation (lightweight; drafting happens in the listener) | inline in `run_daily.sh` |
| (later) | Owner presses Submit → CV + cover letter drafted per job via `claude -p` | `telegram_select.generate_one`, `prompts/selected_job_draft.md` |

Full detail: [[01 - Pipeline Architecture]].

## Technologies used

- **Python 3.10+** (stdlib-only for the pipeline; no requests library — `urllib` + `subprocess`)
- **Bash 3.x-compatible `run_daily.sh`** — the master orchestration script (~1,700 lines)
- **Bun + TypeScript** — portal-search CLIs in `.agents/skills/*-search/cli/` (zero runtime deps)
- **Claude Code CLI (`claude -p`)** — spawns the drafter agent per selected job
- **Direct Anthropic Messages API** — the ranker (`rank_jobs_api.py`), configured via `ANTHROPIC_BASE_URL`/`ANTHROPIC_MODEL`/`ANTHROPIC_AUTH_TOKEN` env (gateway-compatible)
- **Telegram bots (python-telegram-bot 22.8)** — three separate bots: Claude Code bridge bot, job-selector bot, Stage 3 control bot
- **Playwright 1.62 (optional)** — tier-3 authenticated LinkedIn enrichment, headless by default
- **Kimi WebBridge daemon** — tier-1 browser enrichment through the owner's real logged-in browser
- **LaTeX (lualatex)** — CV/cover-letter compilation; active templates: `onepage-ats` CV + `minimal-onepage` letter
- **macOS launchd** — two plist jobs: daily 08:00 pipeline + on-demand selector listener
- **GitHub Actions** — lint, security guards, unit tests, LaTeX smoke compile

## Repo layout at a glance

```
scripts/       24 Python + 4 shell scripts — the pipeline itself
stage_3/       Telegram control bot (orchestrator, bot, schedules, progress, render)
config/        search_matrix.json (queries/budgets) + automation.json (secrets, gitignored)
prompts/       LLM prompt templates (rank, draft, QA)
tests/         49 test files, ~1,466 test functions
tools/         dev/CI utilities (lint, security guards, PDF verify, salary, upstream triage)
.agents/skills/ 8 portal-search CLIs (LinkedIn, freehire, arbeitnow, WWR + 4 Danish)
.claude/       skills (job-application-assistant etc.), 15 commands, settings, hooks
job_scraper/   seen_jobs.json + alert_matched.json run state
cv/, cover_letters/  generated application documents (gitignored)
documents/     source materials + per-application archives
docs/          TELEGRAM.md, STAGE_3.md, GATE_TRIBUNAL.md, BROWSER_ENRICHMENT.md, ...
reports/daily/ one markdown digest per run
logs/daily/    one log per run
```

Per-file detail: [[06 - File by File Analysis]].

## Current state (2026-09-11)

- Master is clean, committed, and pushed.
- Stage 3 (Telegram on-demand control) is **complete** per `docs/STAGE_3_COMPLETION_REPORT.md`.
- Phase B (browser verification of the deep-ranked set) is **confirmed but not built** (`docs/PHASE_B_BROWSER_VERIFICATION.md`).
- The 08:00 scheduled run and the Stage 3 bot coexist via two launchd plists.
- `docs/STAGE_3_RESUME.md` is a historical checkpoint; `docs/STAGE_3_HANDOFF.md` records design decisions.

Related notes: [[04 - Telegram Bot Features]] · [[05 - Playwright and Headless Mode]] · [[07 - Known Issues and Bugs]]

## Vault graph

Full per-file index: [[_Master File Index]] · Flow: [[_Pipeline Flow]] · Config: [[_Configuration Files]] · Tests: [[_Test Files]] · Scripts: [[_Script Files]] · Prompts: [[_Prompt Files]] · Stats: [[_Graph Summary]]
