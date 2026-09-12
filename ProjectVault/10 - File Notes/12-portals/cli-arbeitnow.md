---
tags: [file]
path: .agents/skills/arbeitnow-search/cli/src/cli.ts
type: other
---

# cli-arbeitnow

## Location
`.agents/skills/arbeitnow-search/cli/src/cli.ts`

## Purpose
arbeitnow-search CLI entry point (TypeScript): parses flags, routes to command modules, JSON/markdown output.

## Connections
### Calls or imports:
- [[helpers-arbeitnow]] - shared helpers
- [[cmd-search-arbeitnow]] - routes to command
- [[cmd-detail-arbeitnow]] - routes to command

### Called by or imported by:
- [[run-daily]] - bun run portal fetch
- [[tsconfig-arbeitnow]] - compiles
- [[test-cli-flag-validation-arbeitnow]] - exercises CLI
- [[test-parsing-arbeitnow]] - exercises CLI
- [[skill-arbeitnow-search]] - invoked via bun run
- [[readme-cli-arbeitnow]] - usage docs for

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
