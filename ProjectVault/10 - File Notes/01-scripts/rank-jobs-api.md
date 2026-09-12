---
tags: [file]
path: scripts/rank_jobs_api.py
type: python
---

# rank-jobs-api

## Location
`scripts/rank_jobs_api.py`

## Purpose
Phase 2: one direct Anthropic-compatible Messages API call (stdlib urllib) scores the 25-job rankset; strict decision validation, wrapper-computed overall/verdicts, atomic 0600 outputs, seen-store update.

## Connections
### Calls or imports:
- [[pipeline-phase1-rank]] - prompt template
- [[skill-04-job-evaluation]] - trusted rubric context
- [[skill-01-candidate-profile]] - trusted profile context
- [[seen-jobs]] - updates store
- [[alert-matched]] - alert-matching input
- [[job-search-tracker-csv]] - tracker context
- [[claude-settings]] - ANTHROPIC_* env for endpoint/model

### Called by or imported by:
- [[run-daily]] - Phase 2
- [[test_rank_jobs_api]] - tests
- [[test_stage3_outputs]] - tests
- [[sandbox-rankset]] - rankset shape fixture
- [[skill-02-behavioral-profile]] - feeds the behavioral scoring dimension

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
