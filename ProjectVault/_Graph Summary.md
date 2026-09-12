---
tags: [hub, summary]
type: other
---

# _Graph Summary

Vault enhancement pass of 2026-09-11. Goal: every file in the project appears in the
Obsidian graph with connections; no orphan notes. Parent index: [[_Master File Index]].

## Discovery totals (Step 1)

| Source | Count |
|---|---|
| Git tracked (`git ls-files`) | 313 |
| Untracked/ignored files with their own notes (config/automation.json, .claude/settings.json + settings.local + scheduled_tasks.lock, job_scraper state, tracker CSVs, combined JSONs, batch3-scores, bun.locks, worktree .env.stage3, etc.) | 26 |
| Worktree files (`.claude/worktrees/stage-3-completion/`) | 341 (340 grouped + `.env.stage3` as its own note) |
| All files on disk (`find . -type f`, excl. `.git` internals and the vault itself) | 35,984 |

Of the 35,984 on-disk files, **34,616 are environment/cache noise** — `node_modules/`
(31,098), `__pycache__/` (1,794), `.venv/` (1,672), `.pytest_cache/` (5), `.DS_Store` (47)
— summarized under [[environment-dirs]] rather than one note each. That leaves **1,368
meaningful files** (including the 341 worktree copies).

## Coverage (Steps 2–6)

- **Meaningful files covered: 1,368 / 1,368 (100%)**
  - 309 individual file notes (one per meaningful file)
  - 12 group notes holding the remaining 1,059 files (generated `cv/` + `cover_letters/`
    applications, `documents/` archives, logs, reports, `.backups/`, `manual_run_2026-08-19/`,
    OpenFonts, worktree, LaTeX strays, empty dirs)
- **Notes that already existed: 11** (00–09 + README) — all updated with frontmatter
  tags and graph links (additive only, no content deleted)
- **New notes created: 328** (309 file notes + 12 group notes + 6 hubs + this summary)
- **Files missing from the graph before this pass: effectively all** — the old vault had
  only 11 thematic notes; `06 - File by File Analysis` tabulated files in prose but no
  file had its own note or graph node. Now every meaningful file resolves to a node.

## Graph health (Step 7)

- Total wiki-links in vault: **2,403**
- Broken links: 0
- Orphaned notes (no incoming and no outgoing links): **0**
- Every file note links to its folder hub and to [[_Master File Index]]; hubs
  cross-link; the 11 original notes link into the new graph.

## Could not be analyzed / limitations

- **Binary files** (fonts, GIF, PDFs, .pyc): noted by name/purpose, contents not parsed.
- **Personal-data files** (seen_jobs.json, alert_matched.json, job_search_tracker.csv,
  automation.json, settings.json, .env.stage3): documented by *structure and purpose
  only* — no keys, tokens, credentials, or job-history contents copied into the vault.
- **node_modules / .venv**: third-party code, summarized as environment dirs, not
  individually documented.
- **Worktree duplicates**: the 340 near-identical copies under
  `.claude/worktrees/stage-3-completion/` are grouped in [[worktree-stage3-completion]]
  rather than duplicated as notes; only its unique files get individual treatment.
- **`scripts/rank_jobs.py`** and the `optimize_*` trio: purposes known but flagged stale
  (see [[07 - Known Issues and Bugs]]).

## Hubs

[[_Master File Index]] · [[_Pipeline Flow]] · [[_Configuration Files]] ·
[[_Test Files]] · [[_Script Files]] · [[_Prompt Files]]
