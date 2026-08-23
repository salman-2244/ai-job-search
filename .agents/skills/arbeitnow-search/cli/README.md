# arbeitnow-cli

CLI for searching the [Arbeitnow](https://www.arbeitnow.com) job board —
Germany/EU-focused, with many English-language and remote tech/data/engineering
roles — via its free public JSON API.

**Data source**: Arbeitnow job-board API (`GET /api/job-board-api?page=<n>`).
**Authentication**: None required — it is a free public API (no key, no signup).
**Dependencies**: None (plain `bun` + `fetch`). `bun install` is optional and only pulls dev type defs.

> **Free public API — keep volume low.** Arbeitnow's terms ask callers **not to
> abuse the API and to link back to the site**. This CLI bounds its page scan
> (`--max-pages`, default 5) and always emits the real posting URL. If the API is
> unreachable the CLI exits non-zero with a clear error rather than hanging, so an
> outage degrades gracefully. Point `ARBEITNOW_API_URL` at a mirror/proxy to swap
> the source.

## Installation

```bash
cd .agents/skills/arbeitnow-search/cli
bun install   # optional — only installs TypeScript dev types
```

The CLI runs without any install because it has zero runtime dependencies.

## Design note: client-side filtering

The Arbeitnow API returns one page of ~176 jobs at a time (newest-first) with **no
keyword-search parameter and no per-slug endpoint**. So `search` pages through the
board (bounded by `--max-pages`), filters client-side (keyword/tag/location/
remote/age), and paginates the filtered set; `detail` scans recent pages for the
slug. See `../url-reference.md` for the full API shape.

## Commands

| Command | Description |
|---------|-------------|
| `search` | Search jobs by keyword, location, tag, remote, and recency (client-side) |
| `detail` | Fetch full detail for a single job by its slug (scans recent pages) |

`search` accepts `--format json|table|plain` (default `json`); `detail` accepts `--format json|plain`.
All errors are written to **stderr** as `{ "error": "...", "code": "..." }` with exit code `1`.

## Quick examples

```bash
# Machine-learning roles, table view
bun run src/cli.ts search -q "machine learning" --limit 10 --format table

# Data scientist roles in Berlin, last 14 days
bun run src/cli.ts search -q "data scientist" -l "Berlin" --jobage 14 --format table

# Remote Python roles, scanning deeper
bun run src/cli.ts search -q "python" --remote remote --max-pages 10 --format table

# Full detail for one job (slug from a search result's id)
bun run src/cli.ts detail remote-ai-solution-architect-nurnberg-mittelfranken-336398 --format plain
```

See `../SKILL.md` for the full flag reference and the free-public-API note.

## Search flags

| Flag | Alias | Description |
|------|-------|-------------|
| `--query` | `-q` | Keywords over title/company/tags/description. Space = AND. Client-side. |
| `--location` | `-l` | Case-insensitive substring of the job location. Client-side. |
| `--remote` | | `remote` \| `onsite` (the API exposes only a boolean — no hybrid). |
| `--tag` | | Comma-separated tags/job-types, ORed (e.g. `"Full-time,Software Engineering"`). |
| `--jobage` | | Posted within N days (client-side, from `created_at`). |
| `--page` | | 1-indexed page over the filtered results. Default 1. |
| `--limit` | `-n` | Results per page. Default 25. |
| `--max-pages` | | API pages to scan (176 jobs each). Default 5. Raise to search deeper. |
| `--format` | | `json` \| `table` \| `plain`. |

Search JSON `meta` carries `matched` (jobs passing the filters within the scan)
and `scan_capped` (true when `--max-pages` stopped the scan before the board was
exhausted — the count is then a floor; raise `--max-pages`).

## Tests

```bash
bun run typecheck   # tsc --noEmit
bun test            # unit (parsing/filtering) + flag-validation; network-free
```
