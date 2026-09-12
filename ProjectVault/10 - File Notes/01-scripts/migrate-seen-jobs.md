---
tags: [file]
path: scripts/migrate_seen_jobs.py
type: python
---

# migrate-seen-jobs

## Location
`scripts/migrate_seen_jobs.py`

## Purpose
One-time (2026-08-18) canonicalization of seen_jobs.json keys (raw URLs → url:linkedin:<id> etc.) using aggregate_jobs key logic.

## Connections
### Calls or imports:
- [[aggregate-jobs]] - key canonicalization
- [[seen-jobs]] - rewrites keys

### Called by or imported by:
- [[test_seen_jobs_migration]] - tests

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
