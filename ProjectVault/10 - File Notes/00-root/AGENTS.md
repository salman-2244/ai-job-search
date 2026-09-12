---
tags: [file]
path: AGENTS.md
type: doc
---

# AGENTS

## Location
`AGENTS.md`

## Purpose
Agent-tool entry point describing the thin-pointer design: how Codex/Antigravity/Cursor discover the canonical specs under .claude/ and the portal CLIs under .agents/.

## Connections
### Calls or imports:
- [[CLAUDE]] - thin-pointer to canonical profile
- [[skill-job-scraper]] - thin-pointer to workflow specs

### Called by or imported by:
- [[check-upstream-updates]] - framework_version stamp
- [[test_check_upstream_updates]] - tests

## Notes
Folder hub: [[_Master File Index]] · Index: [[_Master File Index]]
