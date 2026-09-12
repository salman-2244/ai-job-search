---
tags: [file]
path: scripts/prerank_jobs.py
type: python
---

# prerank-jobs

## Location
`scripts/prerank_jobs.py`

## Purpose
Two-stage free pre-rank (--stage shortlist → 80, --stage final → 25): track floors, alert slots, near-dup collapse, repeat markers; annotates every cut with a reason via two_axis_score + hard_gates.

## Connections
### Calls or imports:
- [[two-axis-score]] - ScoringModel by path
- [[hard-gates]] - evaluate() by path
- [[enrich-linkedin]] - title_match_score import
- [[gate-jobs]] - live_alert_keys import
- [[search-matrix]] - scoring weights + budgets
- [[seen-jobs]] - repeat markers
- [[alert-matched]] - alert slots

### Called by or imported by:
- [[run-daily]] - Phases 1b/1b-final
- [[rank-jobs-legacy]] - reads same rankset inputs
- [[test_prerank_jobs]] - tests
- [[test_two_axis_score]] - tests

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
