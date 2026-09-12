---
tags: [file]
path: .agents/skills/linkedin-search/cli/src/cli.ts
type: other
---

# cli-linkedin

## Location
`.agents/skills/linkedin-search/cli/src/cli.ts`

## Purpose
linkedin-search CLI entry point (TypeScript): parses flags, routes to command modules, JSON/markdown output.

## Connections
### Calls or imports:
- [[helpers-linkedin]] - shared helpers
- [[cmd-search-linkedin]] - routes to command
- [[cmd-detail-linkedin]] - routes to command

### Called by or imported by:
- [[run-daily]] - bun run portal fetch
- [[tsconfig-linkedin]] - compiles
- [[test-cli-flag-validation-linkedin]] - exercises CLI
- [[test-parsing-linkedin]] - exercises CLI
- [[test-request-timeout-linkedin]] - exercises CLI
- [[test-retry-backoff-linkedin]] - exercises CLI
- [[test-search-linkedin]] - exercises CLI
- [[skill-linkedin-search]] - invoked via bun run
- [[readme-cli-linkedin]] - usage docs for

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
