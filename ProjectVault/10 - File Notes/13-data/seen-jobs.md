---
tags: [file]
path: job_scraper/seen_jobs.json
type: data
---

# seen-jobs

## Location
`job_scraper/seen_jobs.json`

## Purpose
Every ranked job key with status/score/verdict/date/track (gitignored personal history); no longer a filter — the 'seen X runs ago' marker.

## Connections
### Calls or imports:
- (none directly)

### Called by or imported by:
- [[run-daily]] - seen-store lifecycle
- [[prerank-jobs]] - repeat markers
- [[rank-jobs-api]] - updates store
- [[migrate-seen-jobs]] - rewrites keys
- [[skill-job-scraper]] - dedup state
- [[seen-jobs-bak]] - backup of
- [[seen-jobs-premigration]] - pre-migration snapshot
- [[test_seen_jobs_migration]] - tests
- [[gitkeep-jobscraper]] - tracks the dir holding

## Notes
Folder hub: [[_Master File Index]] · Index: [[_Master File Index]]
