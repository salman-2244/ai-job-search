---
name: arbeitnow-search
version: 1.0.0
description: >
  Use this skill to search live job listings on the Arbeitnow job board — a
  Germany/EU-focused aggregator with many English-language and remote tech,
  data, and engineering roles — via its free public JSON API, or to look up a
  specific posting by slug. Good for German/EU-market and remote-Europe coverage
  (priorities 2–5 of this repo's Europe-wide search). Trigger phrases: jobs in
  Germany, German job board, Berlin/Munich tech jobs, EU tech jobs, remote Europe
  jobs, data/ML jobs in Germany, "are there any <role> jobs on Arbeitnow", look
  up this Arbeitnow posting.
context: fork
enabled: true  # set to false to keep this portal installed but have /scrape skip it
allowed-tools: Bash(bun run .agents/skills/arbeitnow-search/cli/src/cli.ts *)
---

# Arbeitnow Search Skill

Search live job listings from the **[Arbeitnow](https://www.arbeitnow.com)** job
board — a Germany/EU-focused aggregator with a large share of English-language and
remote tech/data/engineering roles — through its free public JSON API. No
authentication, no API key, and **zero runtime dependencies** — it runs with just
`bun`.

> Added to this repo via `/add-portal` as the European aggregator for the
> Europe-wide search (see `.claude/skills/job-scraper/search-queries.md`). Like
> `freehire-search`, it queries a public JSON API rather than scraping HTML, so
> results are structured. Unlike freehire, Arbeitnow's API has **no server-side
> search and no per-slug endpoint**, so this skill filters client-side over a
> bounded scan of recent pages (see the design note below).

## ℹ️ Free public API — keep volume low

Arbeitnow publishes this as a **free public API** with no key. Its own terms ask
callers **not to abuse it and to link back to the site**. This skill honours that
by:

- scanning only a **bounded number of pages** per query (`--max-pages`, default 5
  ≈ the ~880 most recent postings), and
- always emitting the real posting `url` so downstream applications link back to
  Arbeitnow / the employer, not to a scraped copy.

If the API is unreachable, the CLI fails gracefully — a non-zero exit with a clear
error message — so an outage degrades this source rather than breaking the
surrounding workflow. Point `ARBEITNOW_API_URL` at a mirror/proxy to swap the source.

## Design note: client-side filtering

The Arbeitnow board API (`/api/job-board-api`) returns one page of ~176 jobs at a
time, ordered newest-first, with **no `q`/keyword parameter and no per-slug lookup**.
So:

- **`search`** pages through the board (bounded by `--max-pages`), decodes and
  filters each job **client-side** (keyword, tag, location, remote, age), then
  paginates the filtered set. It stops early once it has enough matches for the
  requested page, once the board is exhausted, or once every job on a page is older
  than the `--jobage` window (newest-first ordering makes that a safe cutoff).
- **`detail`** locates a job by scanning recent pages for its slug. A posting that
  has aged off the board is reported as `NOT_FOUND`.

Because matching is client-side, the JSON `meta` reports `matched` (how many jobs
passed the filters within the scan) and `scan_capped` (true when `--max-pages`
stopped the scan before the board was exhausted — the count is then a floor, raise
`--max-pages` to search deeper).

## When to use this skill

- Search Germany/EU and remote-Europe job openings by keyword, location, tag, or
  recency — each result carries its posting URL and remote flag
- Look one Arbeitnow posting up by its slug (while it is still on the recent board)

## Commands

### Search job listings

```bash
bun run .agents/skills/arbeitnow-search/cli/src/cli.ts search [-q "<keywords>"] [filters]
```

Key flags:
- `--query <text>` / `-q <text>` — keywords matched against title, company, tags,
  and description. Space-separated terms are **ANDed**. Client-side; optional.
- `--location <text>` / `-l <text>` — case-insensitive substring of the job
  location, e.g. `-l "Berlin"`. Client-side.
- `--remote <mode>` — `remote` or `onsite`. The API exposes only a remote boolean,
  so there is **no hybrid** value.
- `--tag <list>` — comma-separated tags/job-types, ORed, e.g.
  `--tag "Full-time,Software Engineering"`. Values come from the postings
  themselves (`tags` + `job_types`), not a fixed vocabulary.
- `--jobage <days>` — posted within N days (client-side, from `created_at`).
- `--page <n>` — 1-indexed page over the **filtered** results. Default 1.
- `--limit <n>` / `-n <n>` — results per page. Default 25.
- `--max-pages <n>` — API pages to scan (176 jobs each). Default 5. Raise it to
  search deeper when a specific query returns few matches (`scan_capped: true`).
- `--format json|table|plain` — default `json`.

### Fetch full job detail

```bash
bun run .agents/skills/arbeitnow-search/cli/src/cli.ts detail <slug|url> [--format json|plain]
```

`slug` is the `id` from a `search` result (e.g.
`remote-ai-solution-architect-nurnberg-mittelfranken-336398`). You may also pass a
full `https://www.arbeitnow.com/view/<slug>` URL. Returns the full (HTML-stripped)
description, tags, job types, remote flag, and posting URL. `--max-pages` (default
5) bounds how deep the slug scan goes.

## Usage examples

```bash
# Machine-learning roles, table view
bun run .agents/skills/arbeitnow-search/cli/src/cli.ts search -q "machine learning" --limit 10 --format table

# Data scientist roles in Berlin, last 14 days
bun run .agents/skills/arbeitnow-search/cli/src/cli.ts search -q "data scientist" -l "Berlin" --jobage 14 --format table

# Remote Python roles, scanning deeper (10 pages ≈ 1760 recent postings)
bun run .agents/skills/arbeitnow-search/cli/src/cli.ts search -q "python" --remote remote --max-pages 10 --format table

# Full details for a specific job
bun run .agents/skills/arbeitnow-search/cli/src/cli.ts detail remote-ai-solution-architect-nurnberg-mittelfranken-336398 --format plain
```

## Output formats

| Format | Best for |
|--------|----------|
| `json` | Default — programmatic use; carries `meta.matched` / `meta.scan_capped` |
| `table` | Quick human-readable scanning |
| `plain` | Reading a single job's full detail (`detail` command) |

Search JSON is `{ "meta": { "count", "page", "matched", "api_pages_fetched",
"scan_capped" }, "results": [...] }`; each result carries `id` (the Arbeitnow
slug), `title`, `company`, `location`, `date` (ISO, from `created_at`), `url`,
`remote`, `tags`, and `job_types` (missing values are `null`). All errors are
written to **stderr** as `{ "error": "...", "code": "..." }` and the process exits
with code `1`.

## Notes on the data

- **Descriptions can be German.** Arbeitnow is a German board; many postings are in
  German even when the role welcomes English speakers, and some *require* German.
  This skill does not judge that — the repo's **Language Gate**
  (`04-job-evaluation.md`) is where a German-language *requirement* gets flagged or
  excluded against the candidate profile.
- Description HTML is sometimes **double entity-encoded** (`&lt;p&gt;…`); the CLI
  decodes it fully before stripping tags, so `plain`/`detail` output is clean prose.
- `remote` is a plain boolean from the API — a posting titled "remote …" may still
  have `remote: false` if Arbeitnow didn't set the flag; `--remote` keys off the
  boolean, not the text.
- `date` is derived from the `created_at` Unix timestamp; the board is ordered by it.
- The API retries 429/5xx with exponential backoff; an unreachable API exits
  non-zero with a clear message (free public API — keep volume low, see above).
