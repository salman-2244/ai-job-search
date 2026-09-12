---
tags: [file]
path: .github/workflows/upstream-watch.yml
type: config
---

# upstream-watch-workflow

## Location
`.github/workflows/upstream-watch.yml`

## Purpose
Weekly fork-behind triage: runs check_upstream_updates + upstream_triage, files a report issue, never merges.

## Connections
### Calls or imports:
- [[check-upstream-updates]] - weekly diff
- [[upstream-triage]] - triage issue

### Called by or imported by:
- (none known)

## Notes
Folder hub: [[_Configuration Files]] · Index: [[_Master File Index]]
