---
tags: [file]
path: scripts/write_selection_handoff.py
type: python
---

# write-selection-handoff

## Location
`scripts/write_selection_handoff.py`

## Purpose
Atomically writes /tmp/jobsearch_pending_selection.json (mode 0600) — the Phase 3 handoff marker that authorizes the selector listener to post.

## Connections
### Calls or imports:
- [[selector-listener]] - marker consumed by

### Called by or imported by:
- [[run-daily]] - Phase 3 handoff

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
