---
tags: [file]
path: job_search_tracker.csv
type: data
---

# job-search-tracker-csv

## Location
`job_search_tracker.csv`

## Purpose
Application tracking spreadsheet (date, company, role, status, fit_rating, cv/cover paths, source) — gitignored personal data; appended by telegram_select/generate_batch, read by ranker context and report commands.

## Connections
### Calls or imports:
- (none directly)

### Called by or imported by:
- [[rank-jobs-api]] - tracker context
- [[telegram-select]] - append_tracker
- [[salary_lookup]] - role/sector matching input
- [[cmd-html-report]] - renders
- [[cmd-gmail-sync]] - syncs into
- [[cmd-notion-sync]] - pushes rows
- [[cmd-outcome]] - records outcomes
- [[skill-upskill]] - reads postings vs profile
- [[job-scraper-tracker-csv]] - skill-local copy
- [[test_telegram_select]] - tests
- [[test_tracker_status_vocab]] - tests

## Notes
Folder hub: [[_Master File Index]] · Index: [[_Master File Index]]
