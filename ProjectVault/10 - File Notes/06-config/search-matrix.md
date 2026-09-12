---
tags: [file]
path: config/search_matrix.json
type: config
---

# search-matrix

## Location
`config/search_matrix.json`

## Purpose
The pipeline's brain (~24 KB): LinkedIn tracks/geos/budgets/caps, alerts IMAP block + track_map, prerank budgets, two-axis scoring weights and vocabulary; _-prefixed keys are inline docs.

## Connections
### Calls or imports:
- (none directly)

### Called by or imported by:
- [[run-daily]] - reads queries/budgets
- [[build-search-plan]] - reads tracks/geos/budgets
- [[prerank-jobs]] - scoring weights + budgets
- [[linkedin-alerts]] - alerts block + track_map
- [[test_linkedin_alerts]] - tests
- [[test_search_plan]] - tests
- [[test_two_axis_score]] - tests

## Notes
Folder hub: [[_Configuration Files]] · Index: [[_Master File Index]]
