---
tags: [file]
path: stage_3/render.py
type: python
---

# stage3-render

## Location
`stage_3/render.py`

## Purpose
Telegram HTML rendering + keyboards; escapes everything; geo names index-keyed for the 64-byte callback cap.

## Connections
### Calls or imports:
- [[stage3-progress]] - PHASES/RunState

### Called by or imported by:
- [[stage3-bot]] - keyboards + HTML
- [[test_stage3_render]] - tests

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
