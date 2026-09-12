---
tags: [file]
path: scripts/generate_batch.py
type: python
---

# generate-batch

## Location
`scripts/generate_batch.py`

## Purpose
Older sequential batch drafter with state file + quota abort; imports telegram_select pieces; still functional.

## Connections
### Calls or imports:
- [[telegram-select]] - reuses generate pieces

### Called by or imported by:
- [[watch-generation]] - drives batch as dashboard
- [[pipeline-phase2-draft]] - batch path prompt
- [[pipeline-phase3-qa]] - QA over batch output

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
