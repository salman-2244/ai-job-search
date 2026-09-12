---
tags: [file]
path: .agents/skills/freehire-search/cli/src/cli.ts
type: other
---

# cli-freehire

## Location
`.agents/skills/freehire-search/cli/src/cli.ts`

## Purpose
freehire-search CLI entry point (TypeScript): parses flags, routes to command modules, JSON/markdown output.

## Connections
### Calls or imports:
- [[helpers-freehire]] - shared helpers
- [[cmd-search-freehire]] - routes to command
- [[cmd-detail-freehire]] - routes to command

### Called by or imported by:
- [[run-daily]] - bun run portal fetch
- [[freehire-combined-results-json]] - raw output of
- [[tsconfig-freehire]] - compiles
- [[test-cli-flag-validation-freehire]] - exercises CLI
- [[test-commands-freehire]] - exercises CLI
- [[test-parsing-freehire]] - exercises CLI
- [[test-request-timeout-freehire]] - exercises CLI
- [[test-retry-backoff-freehire]] - exercises CLI
- [[skill-freehire-search]] - invoked via bun run
- [[readme-cli-freehire]] - usage docs for

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
