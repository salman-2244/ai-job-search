---
tags: [file]
path: .agents/skills/jobindex-search/cli/src/cli.ts
type: other
---

# cli-jobindex

## Location
`.agents/skills/jobindex-search/cli/src/cli.ts`

## Purpose
jobindex-search CLI entry point (TypeScript): parses flags, routes to command modules, JSON/markdown output.

## Connections
### Calls or imports:
- [[helpers-jobindex]] - shared helpers
- [[cmd-search-jobindex]] - routes to command
- [[cmd-detail-jobindex]] - routes to command

### Called by or imported by:
- [[tsconfig-jobindex]] - compiles
- [[test-cli-contract-jobindex]] - exercises CLI
- [[test-cli-flag-validation-jobindex]] - exercises CLI
- [[test-parsing-jobindex]] - exercises CLI
- [[test-request-timeout-jobindex]] - exercises CLI
- [[test-retry-backoff-jobindex]] - exercises CLI
- [[skill-jobindex-search]] - invoked via bun run
- [[readme-cli-jobindex]] - usage docs for

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
