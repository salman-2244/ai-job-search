---
tags: [hub]
type: other
---


# _Pipeline Flow

How one daily run connects the files, phase by phase. Detail: [[01 - Pipeline Architecture]]. Index: [[_Master File Index]].

## Entry points

- [[com-salman-jobsearch-daily-plist]] → [[run-daily]] (launchd 08:00)
- [[stage3-orchestrator]] → [[run-daily]] (Telegram `/run`)
- [[com-salman-jobsearch-selector-plist]] → [[selector-listener]] (on-demand)

## Phase 0b — alert ingestion

- [[linkedin-alerts]] reads [[automation-json]] + [[search-matrix]] → writes [[alert-matched]]

## Phase 1 — fetch

- [[build-search-plan]] reads [[search-matrix]] → TSV of CLI calls
- [[run-daily]] runs the portal CLIs: [[cli-linkedin]] ([[cmd-search-linkedin]], [[cmd-detail-linkedin]]), [[cli-freehire]], [[cli-arbeitnow]], [[cli-weworkremotely]]
- [[aggregate-jobs]] merges into the corpus; [[seen-jobs]] marks repeats

## Phase 1b/1c — prerank + enrich

- [[prerank-jobs]] uses [[two-axis-score]] + [[hard-gates]] → shortlist 80
- [[enrich-linkedin]] tiers: [[linkedin-extract]] (WebBridge) → [[linkedin-playwright]] (via [[linkedin-session]]) → guest [[cmd-detail-linkedin]]
- [[prerank-jobs]] --stage final → 25-job rankset

## Phase 2 — rank + gate

- [[rank-jobs-api]] + [[pipeline-phase1-rank]] (context: [[skill-04-job-evaluation]], [[skill-01-candidate-profile]]) → scores
- [[gate-jobs]] + [[alert-matched]] + [[automation-json]] → ≤5 cleared

## Phase 3 — Telegram selection

- [[write-selection-handoff]] → marker → [[selector-listener]] (imports [[telegram-select]]) posts cards
- Submit → [[telegram-select]] → `claude -p` with [[selected-job-draft]] → [[cv-generated-applications]] + [[cover-letters-generated-applications]] + [[job-search-tracker-csv]]

## Phase 4–7 — QA, report, cleanup

- [[pipeline-phase3-qa]] (vestigial) · [[reports-daily]] · [[logs-daily]] · [[send-email]] (retired) · tg-notify

## Stage 3 control plane

- [[stage3-bot]] ↔ [[stage3-config]], [[stage3-orchestrator]], [[stage3-progress]] (parses [[logs-daily]]), [[stage3-render]], [[stage3-schedules]], [[stage3-diagnostics]]

## Legacy / side paths

- [[rank-jobs-legacy]] (superseded), [[generate-batch]] + [[watch-generation]] (batch path, [[pipeline-phase2-draft]]), [[optimize-documents]] + [[optimize-content]] + [[apply-optimizations]] (stale), [[migrate-seen-jobs]] (one-time)

Index: [[_Master File Index]]
