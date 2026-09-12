---
tags: [file]
path: stage_3/config.py
type: python
---

# stage3-config

## Location
`stage_3/config.py`

## Purpose
Env-file parsing (.env.stage3), token isolation, chat/user allowlist, child_env sanitization, redacted repr — no secret ever logged.

## Connections
### Calls or imports:
- [[telegram-select]] - selector env-path check
- [[env-stage3-worktree]] - parses .env.stage3 format

### Called by or imported by:
- [[stage3-bot]] - load_config
- [[stage3-orchestrator]] - Stage3Config
- [[env-stage3-worktree]] - parsed by
- [[test_notification_e2e]] - tests
- [[test_stage3_config]] - tests
- [[test_stage3_orchestrator]] - tests
- [[test_telegram_integration]] - tests

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
