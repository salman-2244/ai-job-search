---
tags: [file]
path: scripts/install_scheduler.sh
type: shell
---

# install-scheduler

## Location
`scripts/install_scheduler.sh`

## Purpose
Installs both launchd jobs for this checkout: renders the plist templates via render_launchd_plist.py (0600 outputs), bootstraps with launchctl, picks STAGE3_PYTHON → .venv → python3.

## Connections
### Calls or imports:
- [[render-launchd-plist]] - renders plists
- [[com-salman-jobsearch-daily-plist]] - template input
- [[com-salman-jobsearch-selector-plist]] - template input

### Called by or imported by:
- [[cmd-automation]] - installs jobs

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
