---
tags: [file]
path: scripts/aggregate_jobs.py
type: python
---

# aggregate-jobs

## Location
`scripts/aggregate_jobs.py`

## Purpose
Normalizes portal JSONs into one corpus: canonical dedup keys (url:linkedin:<id> / url:<clean> / ct:<company>|<title>), newest-first sort, dedup_key published on each job.

## Connections
### Calls or imports:
- (none directly)

### Called by or imported by:
- [[run-daily]] - Phase 1 aggregation
- [[enrich-linkedin]] - LINKEDIN_JOB_ID key logic
- [[migrate-seen-jobs]] - key canonicalization
- [[test_aggregate_jobs]] - tests
- [[test_enrich_linkedin]] - tests
- [[test_prerank_jobs]] - tests
- [[test_stage3_outputs]] - tests

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
