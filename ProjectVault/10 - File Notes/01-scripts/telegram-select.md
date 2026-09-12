---
tags: [file]
path: scripts/telegram_select.py
type: python
---

# telegram-select

## Location
`scripts/telegram_select.py`

## Purpose
Selector library: JobRow, card rendering, SelectionState, generate_one (claude -p drafter per job), run-scoped paths, tracker append; also its own interactive entry point.

## Connections
### Calls or imports:
- [[selected-job-draft]] - claude -p drafter prompt
- [[job-search-tracker-csv]] - append_tracker
- [[template-tex-onepage-ats]] - drafter tailors active template
- [[template-tex-minimal-onepage]] - drafter tailors active template
- [[cv-generated-applications]] - writes CV output dirs
- [[cover-letters-generated-applications]] - writes letter output dirs

### Called by or imported by:
- [[selector-listener]] - imports whole module by path
- [[generate-batch]] - reuses generate pieces
- [[stage3-config]] - selector env-path check
- [[docs-telegram]] - documents selector bot
- [[test_selector_listener]] - tests
- [[test_telegram_select]] - tests
- [[sandbox-rankset]] - rankset fixture loaded by SelectionState

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
