---
tags: [note, index]
---
# ProjectVault — Index

An Obsidian vault documenting the **AI Job Search** project, generated 2026-09-11 by a
read-only analysis of the entire repository. Open this folder as an Obsidian vault.

## Notes

| # | Note | What's inside |
|---|---|---|
| 00 | [[00 - Project Overview]] | What the project is, goal, phases, technologies, layout, current state |
| 01 | [[01 - Pipeline Architecture]] | Phase-by-phase flow: fetching → pre-rank → enrichment → ranking → gate → selection → generation |
| 02 | [[02 - Configuration and Settings]] | Every config file, env var, key/endpoint, and model — names only, never values |
| 03 | [[03 - Business Rules and Guardrails]] | Candidate profile, selection criteria, the six hard gates, eligibility, honesty rules |
| 04 | [[04 - Telegram Bot Features]] | Three-bot design, all commands, UI (bars/cards/keyboards), notifications, authorization |
| 05 | [[05 - Playwright and Headless Mode]] | Tier-3 provider, headless config (current: true), 0600 rule, documented browser issues |
| 06 | [[06 - File by File Analysis]] | Every file: purpose, imports, dependents, concerns |
| 07 | [[07 - Known Issues and Bugs]] | Bugs found, open ends, error-handling gaps, hardcoded values, security posture |
| 08 | [[08 - Dependencies]] | Python pins, non-Python deps, portal CLIs, missing/implicit deps |
| 09 | [[09 - Recommendations]] | Urgent fixes, optimizations, what to keep as-is, sequencing |

## Reading order

New to the project: 00 → 01 → 03. Operating the bots: 04 + `docs/TELEGRAM.md` /
`docs/STAGE_3.md` in the repo. Debugging: 07 → 06. Changing scoring/budgets: 02 + 03.

## Conventions

- `file:line` references are clickable in editors that support them.
- `docs/…` references point at the repo's own operational documents — the vault
  summarizes them; they remain authoritative for setup and runbooks.
- No secrets appear anywhere in this vault: only key names and env-var names.
- The vault describes the working tree as of 2026-09-11 (commit 3d8b09b); generated
  run artifacts (CVs, reports, logs) are grouped, not listed individually.

## Vault graph

Enhancement pass (2026-09-11): 326 per-file/group notes under `10 - File Notes/` + hubs [[_Master File Index]] · [[_Pipeline Flow]] · [[_Configuration Files]] · [[_Test Files]] · [[_Script Files]] · [[_Prompt Files]] · [[_Graph Summary]]
