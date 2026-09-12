---
tags: [file]
path: job_scraper/alert_matched.json
type: data
---

# alert-matched

## Location
`job_scraper/alert_matched.json`

## Purpose
Alert-matched job keys + first_alerted dates — the 30-day alert-expiry authority used by gate_jobs and prerank.

## Connections
### Calls or imports:
- (none directly)

### Called by or imported by:
- [[prerank-jobs]] - alert slots
- [[linkedin-alerts]] - writes store
- [[rank-jobs-api]] - alert-matching input
- [[gate-jobs]] - expiry authority

## Notes
Folder hub: [[_Master File Index]] · Index: [[_Master File Index]]
