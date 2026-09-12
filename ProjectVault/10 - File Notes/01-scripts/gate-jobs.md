---
tags: [file]
path: scripts/gate_jobs.py
type: python
---

# gate-jobs

## Location
`scripts/gate_jobs.py`

## Purpose
Phase 2b deterministic gate: score ≥75 (or ≥60 + live alert), 30-day alert expiry, cap max_jobs_to_apply, --prune; the alert store is the authority.

## Connections
### Calls or imports:
- [[alert-matched]] - expiry authority
- [[automation-json]] - max_jobs_to_apply/min_score

### Called by or imported by:
- [[run-daily]] - Phase 2b
- [[prerank-jobs]] - live_alert_keys import
- [[cmd-linkedin-alerts]] - alert expiry interplay
- [[test_gate_jobs]] - tests

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
