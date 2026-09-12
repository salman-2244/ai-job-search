---
tags: [file]
path: com.salman.jobsearch.daily.plist
type: config
---

# com-salman-jobsearch-daily-plist

## Location
`com.salman.jobsearch.daily.plist`

## Purpose
launchd template for the daily 08:00 pipeline job, with placeholders rendered by scripts/render_launchd_plist.py.

## Connections
### Calls or imports:
- (none directly)

### Called by or imported by:
- [[run-daily]] - launched by (rendered)
- [[install-scheduler]] - template input
- [[uninstall-scheduler]] - removes installed job

## Notes
Folder hub: [[_Master File Index]] · Index: [[_Master File Index]]
