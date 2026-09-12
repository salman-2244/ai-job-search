---
tags: [file]
path: scripts/run_daily.sh
type: shell
---

# run-daily

## Location
`scripts/run_daily.sh`

## Purpose
Master orchestrator (~1,700 ln): config guards, lock, Phases 0b–7 (fetch → prerank → enrich → rank → gate → handoff → report → cleanup), retry policy, tg-notify trap. Spawned by launchd daily plist and by stage_3/orchestrator.py.

## Connections
### Calls or imports:
- [[linkedin-alerts]] - Phase 0b invocation
- [[build-search-plan]] - Phase 1 plan step
- [[aggregate-jobs]] - Phase 1 aggregation
- [[prerank-jobs]] - Phases 1b/1b-final
- [[enrich-linkedin]] - Phase 1c
- [[rank-jobs-api]] - Phase 2
- [[gate-jobs]] - Phase 2b
- [[write-selection-handoff]] - Phase 3 handoff
- [[selector-listener]] - Phase 3 kickstart target
- [[send-email]] - Phase 6 (retired path)
- [[search-matrix]] - reads queries/budgets
- [[automation-json]] - reads switches/creds
- [[pipeline-phase1-rank]] - ranker prompt (via rank_jobs_api)
- [[pipeline-phase3-qa]] - Phase 4 QA prompt
- [[cli-linkedin]] - bun run portal fetch
- [[cli-freehire]] - bun run portal fetch
- [[cli-arbeitnow]] - bun run portal fetch
- [[cli-weworkremotely]] - bun run portal fetch
- [[reports-daily]] - writes daily report
- [[logs-daily]] - writes daily log
- [[seen-jobs]] - seen-store lifecycle
- [[com-salman-jobsearch-daily-plist]] - launched by (rendered)

### Called by or imported by:
- [[stage3-orchestrator]] - spawns as child
- [[cmd-automation]] - configures
- [[test_prerank_jobs]] - tests
- [[test_ranker_calibration]] - tests
- [[test_report_gate_surface]] - tests
- [[test_stage3_config]] - tests
- [[scheduled-tasks-lock]] - bookkeeping for scheduled runs

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
