---
tags: [file]
path: tools/upstream_triage.py
type: python
---

# upstream-triage

## Location
`tools/upstream_triage.py`

## Purpose
Behind-commit triage report honoring .github/upstream-wontport.txt; reports only, never merges.

## Connections
### Calls or imports:
- [[upstream-wontport]] - skip list

### Called by or imported by:
- [[upstream-watch-workflow]] - triage issue
- [[test_upstream_triage]] - tests

## Notes
Folder hub: [[_Script Files]] · Index: [[_Master File Index]]
