---
tags: [file]
path: .claude/settings.json
type: config
---

# claude-settings

## Location
`.claude/settings.json`

## Purpose
Claude Code project settings (gitignored): env block with ANTHROPIC_AUTH_TOKEN/BASE_URL/MODEL key names — the gateway the ranker reads; values never documented.

## Connections
### Calls or imports:
- (none directly)

### Called by or imported by:
- [[rank-jobs-api]] - ANTHROPIC_* env for endpoint/model
- [[security-guards]] - allowlist read from disk
- [[claude-settings-local]] - local overrides for

## Notes
Folder hub: [[_Configuration Files]] · Index: [[_Master File Index]]
