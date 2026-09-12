---
tags: [file]
path: scripts/linkedin_alerts.py
type: python
---

# linkedin-alerts

## Location
`scripts/linkedin_alerts.py`

## Purpose
Phase 0b: reads LinkedIn job-alert emails over IMAP, rebuilds job URLs, writes portal-format JSON + the alert_matched.json store.

## Connections
### Calls or imports:
- [[automation-json]] - IMAP creds
- [[search-matrix]] - alerts block + track_map
- [[alert-matched]] - writes store

### Called by or imported by:
- [[run-daily]] - Phase 0b invocation
- [[cmd-linkedin-alerts]] - manual run
- [[test_aggregate_jobs]] - tests
- [[test_gate_jobs]] - tests
- [[test_linkedin_alerts]] - tests

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
