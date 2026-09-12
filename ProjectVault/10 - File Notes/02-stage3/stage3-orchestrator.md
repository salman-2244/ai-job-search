---
tags: [file]
path: stage_3/orchestrator.py
type: python
---

# stage3-orchestrator

## Location
`stage_3/orchestrator.py`

## Purpose
Run supervision (1,003 ln): the only place run_daily.sh is spawned — one child at a time, own process group, PID+start-time identity, manifests under ~/.jobsearch-stage3-runs, 3h ceiling, SIGTERM→SIGKILL.

## Connections
### Calls or imports:
- [[run-daily]] - spawns as child
- [[stage3-config]] - Stage3Config
- [[stage3-progress]] - ProgressTracker

### Called by or imported by:
- [[stage3-bot]] - run supervision
- [[docs-stage3-handoff]] - design decisions
- [[test_stage3_orchestrator]] - tests
- [[test_stage3_outputs]] - tests
- [[test_stage3_regression]] - tests

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
