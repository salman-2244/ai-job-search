---
tags: [file]
path: scripts/uninstall_scheduler.sh
type: shell
---

# uninstall-scheduler

## Location
`scripts/uninstall_scheduler.sh`

## Purpose
Removes the com.salman.jobsearch.daily and .selector launchd jobs (bootout + plist delete).

## Connections
### Calls or imports:
- [[com-salman-jobsearch-daily-plist]] - removes installed job
- [[com-salman-jobsearch-selector-plist]] - removes installed job

### Called by or imported by:
- (none known)

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
