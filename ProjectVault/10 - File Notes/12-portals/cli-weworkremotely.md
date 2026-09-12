---
tags: [file]
path: .agents/skills/weworkremotely-search/cli/src/cli.ts
type: other
---

# cli-weworkremotely

## Location
`.agents/skills/weworkremotely-search/cli/src/cli.ts`

## Purpose
weworkremotely-search CLI entry point (TypeScript): parses flags, routes to command modules, JSON/markdown output.

## Connections
### Calls or imports:
- [[helpers-weworkremotely]] - shared helpers
- [[cmd-search-weworkremotely]] - routes to command
- [[cmd-detail-weworkremotely]] - routes to command

### Called by or imported by:
- [[run-daily]] - bun run portal fetch
- [[weworkremotely-combined-json]] - raw output of
- [[tsconfig-weworkremotely]] - compiles
- [[skill-weworkremotely-search]] - invoked via bun run

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
