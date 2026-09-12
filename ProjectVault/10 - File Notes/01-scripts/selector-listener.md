---
tags: [file]
path: scripts/selector_listener.py
type: python
---

# selector-listener

## Location
`scripts/selector_listener.py`

## Purpose
launchd-driven Telegram listener: the handoff marker is the only authorization to post; posts 25 job cards, holds getUpdates ≤20h, crash-resume, poller-death detection, stale-query ack().

## Connections
### Calls or imports:
- [[telegram-select]] - imports whole module by path

### Called by or imported by:
- [[run-daily]] - Phase 3 kickstart target
- [[write-selection-handoff]] - marker consumed by
- [[test_selector_listener]] - tests
- [[test_selector_resilience]] - tests

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
