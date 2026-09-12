---
tags: [file]
path: .agents/skills/jobdanmark-search/cli/src/cli.ts
type: other
---

# cli-jobdanmark

## Location
`.agents/skills/jobdanmark-search/cli/src/cli.ts`

## Purpose
jobdanmark-search CLI entry point (TypeScript): parses flags, routes to command modules, JSON/markdown output.

## Connections
### Calls or imports:
- [[helpers-jobdanmark]] - shared helpers
- [[cmd-search-jobdanmark]] - routes to command
- [[cmd-detail-jobdanmark]] - routes to command
- [[cmd-categories-jobdanmark]] - routes to command
- [[cmd-locations-jobdanmark]] - routes to command
- [[cmd-autocomplete-jobdanmark]] - routes to command

### Called by or imported by:
- [[tsconfig-jobdanmark]] - compiles
- [[test-cli-contract-jobdanmark]] - exercises CLI
- [[test-cli-flag-validation-jobdanmark]] - exercises CLI
- [[test-detail-jsonld-jobdanmark]] - exercises CLI
- [[test-detail-parsing-jobdanmark]] - exercises CLI
- [[test-request-timeout-jobdanmark]] - exercises CLI
- [[test-retry-backoff-jobdanmark]] - exercises CLI
- [[test-user-agent-jobdanmark]] - exercises CLI
- [[skill-jobdanmark-search]] - invoked via bun run
- [[readme-cli-jobdanmark]] - usage docs for

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
