#!/usr/bin/env bun
// Self-contained CLI for searching the Arbeitnow public "job-board-api" (JSON).
// No external CLI framework and zero runtime dependencies, so it runs anywhere
// `bun` is available with nothing installed beyond the repo clone.
//
// Hosted-service dependency: reads are public (no API key), but they hit
// arbeitnow.com — a free public API. Its terms ask callers not to abuse it and to
// link back to the site, so keyword/tag filtering is client-side over a *bounded*
// page scan (see --max-pages). Point ARBEITNOW_API_URL at a mirror to swap source.
//
// The board has no server-side search and no per-slug endpoint: `search` filters
// client-side over recent pages, and `detail` scans recent pages for the slug.

import { runSearch, type SearchOpts } from "./commands/search.js"
import { runDetail, type DetailOpts } from "./commands/detail.js"
import { baseUrl } from "./helpers.js"

interface Flags {
  _: string[]
  [k: string]: string | boolean | string[]
}

// Short-flag aliases.
const ALIAS: Record<string, string> = { q: "query", l: "location", n: "limit" }

const DEFAULT_MAX_PAGES = 5 // ~176 jobs/page → ~880 recent postings scanned by default

function parseFlags(argv: string[]): Flags {
  const flags: Flags = { _: [] }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (!a.startsWith("-")) {
      ;(flags._ as string[]).push(a)
      continue
    }
    const name = a.replace(/^-+/, "")
    const key = ALIAS[name] ?? name
    const next = argv[i + 1]
    let value: string | boolean = true
    if (next !== undefined && !next.startsWith("-")) {
      value = next
      i++
    }
    flags[key] = value
  }
  return flags
}

type FlagValue = string | boolean | string[] | undefined

/** A flag's string value; a bare flag (no value) yields `whenBare`. */
function stringFlag(raw: FlagValue, whenBare?: string): string | undefined {
  if (typeof raw === "string") return raw
  if (raw === true) return whenBare
  return undefined
}

/** Split a comma-separated flag value ("Full-time,Contract") into a trimmed list. */
function commaList(raw: FlagValue): string[] {
  if (typeof raw !== "string") return []
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
}

const HELP = `arbeitnow-cli — search the Arbeitnow job board (Germany/EU, English-friendly tech)

USAGE
  bun run src/cli.ts search [-q "<keywords>"] [filters] [--format json|table|plain]
  bun run src/cli.ts detail <slug|url> [--format json|plain]

SEARCH FLAGS
  --query, -q <text>      Keywords (title/company/tags/description). Space = AND. Client-side.
  --location, -l <text>   Substring-match the job location, e.g. -l "Berlin". Client-side.
  --remote <mode>         remote | onsite. The API exposes only a remote boolean (no hybrid).
  --tag <list>            Comma-separated tags/job-types (OR), e.g. --tag "Full-time,Software Engineering".
  --jobage <days>         Posted within N days (client-side, from created_at).
  --page <n>              1-indexed page over the FILTERED results. Default 1.
  --limit, -n <n>         Results per page. Default 25.
  --max-pages <n>         API pages to scan (176 jobs each). Default ${DEFAULT_MAX_PAGES}. Raise to search deeper.
  --format <fmt>          json (default) | table | plain.

DETAIL
  <slug|url>              An Arbeitnow slug (from a search result's id/slug) or a
                          full https://www.arbeitnow.com/view/<slug> URL. Located by
                          scanning recent board pages (--max-pages, default ${DEFAULT_MAX_PAGES}).

EXAMPLES
  bun run src/cli.ts search -q "machine learning" --limit 10 --format table
  bun run src/cli.ts search -q "data scientist" -l "Berlin" --jobage 14 --format table
  bun run src/cli.ts search -q "python" --remote remote --max-pages 10 --format table
  bun run src/cli.ts detail software-engineer-berlin-233707 --format plain

Reads are public (no API key). Source: ${baseUrl()}${""} — a free public API; its terms
ask callers not to abuse it and to link back. Override with ARBEITNOW_API_URL to use a mirror.
`

function parseIntFlag(name: string, raw: string | boolean | string[]): number | null {
  const val = parseInt(raw as string, 10)
  if (isNaN(val)) {
    process.stderr.write(JSON.stringify({ error: `--${name} must be a number, got "${raw}"`, code: "BAD_ARG" }) + "\n")
    return null
  }
  return val
}

async function main(): Promise<number> {
  const argv = process.argv.slice(2)
  const flags = parseFlags(argv)
  const cmd = (flags._ as string[])[0]

  if (!cmd || flags.help || flags.h) {
    process.stdout.write(HELP)
    return cmd ? 0 : 1
  }

  if (cmd === "search") {
    const fmt = (flags.format as string) || "json"

    // Validate --remote before any network call: an unknown mode should fail
    // loudly rather than silently returning unfiltered results.
    const remote = stringFlag(flags.remote, "remote")
    if (remote !== undefined && !["remote", "onsite", "on-site"].includes(remote.toLowerCase())) {
      process.stderr.write(
        JSON.stringify({ error: `--remote must be one of remote|onsite, got "${remote}"`, code: "BAD_ARG" }) + "\n",
      )
      return 1
    }

    for (const name of ["jobage", "page", "limit", "max-pages"] as const) {
      if (flags[name] !== undefined) {
        const v = parseIntFlag(name, flags[name])
        if (v === null) return 1
        flags[name] = String(v)
      }
    }

    const opts: SearchOpts = {
      query: stringFlag(flags.query),
      location: stringFlag(flags.location),
      remote,
      tags: commaList(flags.tag),
      jobage: flags.jobage ? parseInt(flags.jobage as string, 10) : 9999,
      page: flags.page ? Math.max(1, parseInt(flags.page as string, 10)) : 1,
      limit: flags.limit ? Math.max(1, parseInt(flags.limit as string, 10)) : 25,
      maxPages: flags["max-pages"] ? Math.max(1, parseInt(flags["max-pages"] as string, 10)) : DEFAULT_MAX_PAGES,
      now: Date.now(),
      format: (["json", "table", "plain"].includes(fmt) ? fmt : "json") as SearchOpts["format"],
    }
    return runSearch(opts)
  }

  if (cmd === "detail") {
    const id = (flags._ as string[])[1]
    if (!id) {
      process.stderr.write(JSON.stringify({ error: "detail requires a <slug|url>", code: "NO_ID" }) + "\n")
      return 1
    }
    if (flags["max-pages"] !== undefined) {
      const v = parseIntFlag("max-pages", flags["max-pages"])
      if (v === null) return 1
      flags["max-pages"] = String(v)
    }
    const fmt = (flags.format as string) || "json"
    const opts: DetailOpts = {
      id,
      maxPages: flags["max-pages"] ? Math.max(1, parseInt(flags["max-pages"] as string, 10)) : DEFAULT_MAX_PAGES,
      format: fmt === "plain" ? "plain" : "json",
    }
    return runDetail(opts)
  }

  process.stderr.write(JSON.stringify({ error: `Unknown command "${cmd}"`, code: "BAD_CMD" }) + "\n")
  return 1
}

main()
  .then((code) => process.exit(code))
  .catch((e) => {
    process.stderr.write(
      JSON.stringify({
        error: e instanceof Error ? e.message : String(e),
        code: "INTERNAL_ERROR",
      }) + "\n",
    )
    process.exit(1)
  })
