---
tags: [file]
path: .agents/skills/jobbank-search/cli/src/cli.ts
type: other
---

# cli-jobbank

## Location
`.agents/skills/jobbank-search/cli/src/cli.ts`

## Purpose
jobbank-search CLI entry point (TypeScript): parses flags, routes to command modules, JSON/markdown output.

## Connections
### Calls or imports:
- [[helpers-jobbank]] - shared helpers
- [[cmd-search-jobbank]] - routes to command
- [[cmd-detail-jobbank]] - routes to command

### Called by or imported by:
- [[tsconfig-jobbank]] - compiles
- [[test-cli-contract-jobbank]] - exercises CLI
- [[test-cli-flag-validation-jobbank]] - exercises CLI
- [[test-detail-jsonld-jobbank]] - exercises CLI
- [[test-request-timeout-jobbank]] - exercises CLI
- [[test-retry-backoff-jobbank]] - exercises CLI
- [[test-rss-fetch-jobbank]] - exercises CLI
- [[test-rss-parsing-jobbank]] - exercises CLI
- [[skill-jobbank-search]] - invoked via bun run
- [[readme-cli-jobbank]] - usage docs for

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
