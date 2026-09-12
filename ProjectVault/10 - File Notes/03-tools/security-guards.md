---
tags: [file]
path: tools/security_guards.py
type: python
---

# security-guards

## Location
`tools/security_guards.py`

## Purpose
CI guards (307 ln): permission allowlist, gitignore-rule presence, package-manifest checks — reads .claude/settings.json from disk.

## Connections
### Calls or imports:
- [[gitignore]] - asserts rules present
- [[claude-settings]] - allowlist read from disk

### Called by or imported by:
- [[ci-workflow]] - guards job
- [[SECURITY]] - enforcement
- [[test_security_guards]] - tests

## Notes
Folder hub: [[_Script Files]] · Index: [[_Master File Index]]
