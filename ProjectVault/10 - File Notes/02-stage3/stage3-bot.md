---
tags: [file]
path: stage_3/bot.py
type: python
---

# stage3-bot

## Location
`stage_3/bot.py`

## Purpose
python-telegram-bot application (880 ln): /run /status /cancel /schedule commands, geo/count keyboards, progress editor, scheduler loop, notification wiring; loads geos via build_search_plan.py --list-geos.

## Connections
### Calls or imports:
- [[build-search-plan]] - --list-geos feeds bot buttons
- [[stage3-config]] - load_config
- [[stage3-diagnostics]] - safe logging
- [[stage3-orchestrator]] - run supervision
- [[stage3-progress]] - RunState
- [[stage3-render]] - keyboards + HTML
- [[stage3-schedules]] - cron store

### Called by or imported by:
- [[docs-stage3]] - documents
- [[docs-telegram]] - documents control bot
- [[test_notification_e2e]] - tests
- [[test_stage3_bot]] - tests
- [[test_stage3_docs]] - tests
- [[test_stage3_regression]] - tests

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
