import {
  apiGet,
  toResult,
  matchesQuery,
  matchesTags,
  matchesLocation,
  matchesRemote,
  withinJobAge,
  writeError,
  type ArbeitnowJob,
  type JobResult,
} from "../helpers.js"

export interface SearchOpts {
  query?: string
  location?: string
  remote?: string // "remote" | "onsite"
  tags: string[]
  jobage: number
  page: number
  limit: number
  maxPages: number
  now: number // epoch ms, injected so the age filter is testable/deterministic
  format: "json" | "table" | "plain"
}

/**
 * Fetch and filter the board. The API has no server-side search, so we page
 * through it (newest-first), decode+match each job client-side, and accumulate
 * matches. We stop as soon as we have enough matches to fill the requested client
 * page, or we run out of API pages, or we hit the `maxPages` scan cap — whichever
 * comes first. The cap keeps volume low per the API's "please do not abuse" note.
 */
async function collectMatches(opts: SearchOpts): Promise<{ matches: ArbeitnowJob[]; pagesFetched: number; capped: boolean }> {
  const needed = opts.page * opts.limit // enough to slice out the requested page
  const matches: ArbeitnowJob[] = []
  let pagesFetched = 0
  let capped = false

  for (let apiPage = 1; apiPage <= opts.maxPages; apiPage++) {
    const env = await apiGet(apiPage)
    pagesFetched++
    const jobs = env.data ?? []

    for (const j of jobs) {
      if (
        matchesQuery(j, opts.query) &&
        matchesTags(j, opts.tags) &&
        matchesLocation(j, opts.location) &&
        matchesRemote(j, opts.remote) &&
        withinJobAge(j, opts.jobage, opts.now)
      ) {
        matches.push(j)
      }
    }

    // The board is ordered newest-first. Once every job on a page is older than
    // the age window, no later page can be newer, so we can stop early.
    if (opts.jobage > 0 && opts.jobage < 9999 && jobs.length > 0) {
      const allOlder = jobs.every((j) => !withinJobAge(j, opts.jobage, opts.now))
      if (allOlder) break
    }

    const hasNext = Boolean(env.links?.next)
    if (!hasNext) break // exhausted the board
    if (matches.length >= needed) break // enough to serve the requested page
    if (apiPage === opts.maxPages) capped = true // more pages exist but we stopped
  }

  return { matches, pagesFetched, capped }
}

function shortDate(date: string | null): string {
  return date ? date.slice(0, 10) : "—"
}

interface Column {
  header: string
  width: number
  cell: (r: JobResult) => string
}

function renderTable(rows: JobResult[]): string {
  if (rows.length === 0) return "No results."
  const columns: Column[] = [
    { header: "SLUG", width: Math.max(4, ...rows.map((r) => r.id.length)), cell: (r) => r.id },
    { header: "TITLE", width: 36, cell: (r) => r.title },
    { header: "COMPANY", width: 22, cell: (r) => r.company ?? "—" },
    { header: "LOCATION", width: 18, cell: (r) => r.location ?? "—" },
    { header: "REMOTE", width: 6, cell: (r) => (r.remote ? "yes" : "no") },
    { header: "DATE", width: 10, cell: (r) => shortDate(r.date) },
  ]
  const row = (cells: string[]) =>
    cells.map((c, i) => c.slice(0, columns[i].width).padEnd(columns[i].width)).join("  ")
  const header = row(columns.map((c) => c.header))
  const body = rows.map((r) => row(columns.map((c) => c.cell(r))))
  return [header, "-".repeat(header.length), ...body].join("\n")
}

function renderPlain(rows: JobResult[]): string {
  if (rows.length === 0) return "No results."
  const block = (r: JobResult) =>
    [
      r.title,
      `  ${r.company ?? "—"} · ${r.location ?? "—"} · ${r.remote ? "remote" : "on-site"} · ${shortDate(r.date)}`,
      `  slug: ${r.id}`,
      `  ${r.url}`,
    ].join("\n")
  return rows.map(block).join("\n\n")
}

export async function runSearch(opts: SearchOpts): Promise<number> {
  try {
    const { matches, pagesFetched, capped } = await collectMatches(opts)

    // Client-side pagination over the filtered set.
    const start = (opts.page - 1) * opts.limit
    const rows = matches.slice(start, start + opts.limit).map(toResult)

    if (opts.format === "table") {
      process.stdout.write(renderTable(rows) + "\n")
    } else if (opts.format === "plain") {
      process.stdout.write(renderPlain(rows) + "\n")
    } else {
      process.stdout.write(
        JSON.stringify(
          {
            meta: {
              count: rows.length,
              page: opts.page,
              matched: matches.length,
              api_pages_fetched: pagesFetched,
              // True when matches were capped by --max-pages (more board pages
              // exist but weren't scanned). Signals the count is a floor, not total.
              scan_capped: capped,
            },
            results: rows,
          },
          null,
          2,
        ) + "\n",
      )
    }
    return 0
  } catch (e) {
    writeError(e instanceof Error ? e.message : String(e), "SEARCH_FAILED")
    return 1
  }
}
