---
tags: [file]
path: .agents/skills/jobnet-search/cli/src/cli.ts
type: other
---

# cli-jobnet

## Location
`.agents/skills/jobnet-search/cli/src/cli.ts`

## Purpose
jobnet-search CLI entry point (TypeScript): parses flags, routes to command modules, JSON/markdown output.

## Connections
### Calls or imports:
- [[helpers-jobnet]] - shared helpers
- [[cmd-search-jobnet]] - routes to command
- [[cmd-detail-jobnet]] - routes to command
- [[cmd-occupations-jobnet]] - routes to command
- [[cmd-suggestions-jobnet]] - routes to command

### Called by or imported by:
- [[tsconfig-jobnet]] - compiles
- [[test-cli-contract-jobnet]] - exercises CLI
- [[test-cli-flag-validation-jobnet]] - exercises CLI
- [[test-detail-formatting-jobnet]] - exercises CLI
- [[test-request-timeout-jobnet]] - exercises CLI
- [[test-retry-backoff-jobnet]] - exercises CLI
- [[test-search-normalization-jobnet]] - exercises CLI
- [[test-user-agent-jobnet]] - exercises CLI
- [[skill-jobnet-search]] - invoked via bun run
- [[readme-cli-jobnet]] - usage docs for

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
