---
name: weworkremotely-search
version: 1.0.0
description: >
  Use this skill to search live remote job listings from We Work Remotely — one of
  the largest remote-only job boards — via its public RSS feeds. No authentication
  required. Covers programming, design, marketing, and other remote roles, many
  European-friendly. Trigger phrases: remote jobs, remote work, work from home,
  remote Europe jobs, remote tech jobs, "are there any remote jobs in <field>",
  weworkremotely.
context: fork
enabled: true  # set to false to keep this portal installed but have /scrape skip it
allowed-tools: Bash(bun run .agents/skills/weworkremotely-search/cli/src/cli.ts *)
---

# We Work Remotely Search Skill

Search live job listings from **[We Work Remotely](https://weworkremotely.com)** —
one of the largest remote-only job boards — via its public RSS feeds. No
authentication, no API key, and **zero runtime dependencies** — it runs with just
`bun`. The board focuses on remote positions, many European-friendly or worldwide.

## ⚠️ Scope: remote-only

We Work Remotely only lists remote positions. This skill is best used for finding
remote roles across Europe. For location-specific (onsite/hybrid) roles, use
`linkedin-search` or `freehire-search` instead.

## When to use this skill

- Search for remote job openings by keyword or category
- Filter by recency (posted within N days)
- Get the full description of a specific remote listing

## Feeds

| Feed | URL | Status |
|------|-----|--------|
| All jobs | `/remote-jobs.rss` | ✅ Primary — 100 jobs, all categories |
| Programming | `/categories/remote-programming-jobs.rss` | ✅ |
| Design | `/categories/remote-design-jobs.rss` | ✅ |
| Main feed | `/remote-job-rss-feed` | ❌ Cloudflare blocked |
| Other categories | marketing, devops, etc. | ❌ 403/empty redirect |

The `--category` flag filters client-side using RSS `<category>` tags when no
dedicated feed exists (e.g. `--category marketing` fetches all jobs and filters).

## Commands

### Search job listings

```bash
bun run .agents/skills/weworkremotely-search/cli/src/cli.ts search [flags]
```

Key flags:
- `--query <text>` / `-q <text>` — keyword search (title, skill, role). Optional.
- `--category <list>` — comma-separated: `programming`, `design`, `marketing`, `customer-support`, `finance-legal`, `devops`, `business`, `product`. Optional.
- `--jobage <days>` — posted within N days. Default: all.
- `--page <n>` — page number (1-indexed, 25 results per page). Default 1.
- `--limit <n>` / `-n <n>` — cap total results emitted (client-side).
- `--format json|table|plain` — default `json`.

### Fetch full job detail

```bash
bun run .agents/skills/weworkremotely-search/cli/src/cli.ts detail <url> [--format json|plain]
```

Returns the full description extracted from the posting URL.

## Usage examples

```bash
# Remote programming roles, table view
bun run .agents/skills/weworkremotely-search/cli/src/cli.ts search -q "data engineer" --format table

# Remote ML/AI roles, last 14 days
bun run .agents/skills/weworkremotely-search/cli/src/cli.ts search -q "machine learning" --jobage 14 --format table

# All remote jobs in programming category
bun run .agents/skills/weworkremotely-search/cli/src/cli.ts search --category programming --limit 20 --format table

# Remote design roles
bun run .agents/skills/weworkremotely-search/cli/src/cli.ts search --category design --format table

# All remote jobs (unfiltered)
bun run .agents/skills/weworkremotely-search/cli/src/cli.ts search --limit 50 --format table
```

## Output formats

| Format | Best for |
|--------|----------|
| `json` | Default — programmatic use, passing to detail |
| `table` | Quick human-readable scanning |
| `plain` | Reading a single job's full detail |

Search JSON is `{ "meta": { "count", "page", "total" }, "results": [...] }`; each
result carries at least `id`, `title`, `company`, `location`, `date`, `url`, and
`description` (missing values are `null`). All errors are written to **stderr** as
`{ "error": "...", "code": "..." }` and the process exits with code `1`.

## Notes

- Data is from We Work Remotely's public RSS feeds — no credentials required.
- RSS feeds may not include all postings; some may be delayed.
- Many postings are worldwide/remote-first, often European-friendly.
- The board focuses on tech, design, and marketing roles.
- Rate limiting is unlikely but keep volume reasonable.
- HTML in descriptions is decoded and stripped to plain text automatically.
