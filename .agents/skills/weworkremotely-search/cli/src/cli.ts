#!/usr/bin/env bun
// Self-contained CLI for searching We Work Remotely remote jobs via public RSS feeds.
// No external CLI framework and zero runtime dependencies, so it runs anywhere
// `bun` is available with nothing installed beyond the repo clone.

import { runSearch, type SearchOpts } from "./commands/search.js"
import { runDetail, type DetailOpts } from "./commands/detail.js"
import { CATEGORY_FEEDS, writeError } from "./helpers.js"

interface Flags {
  _: string[]
  [k: string]: string | boolean | string[]
}

function parseFlags(argv: string[]): Flags {
  const flags: Flags = { _: [] }
  const alias: Record<string, string> = { q: "query", n: "limit" }

  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a.startsWith("--") || a.startsWith("-")) {
      const key = alias[a.replace(/^-+/, "")] ?? a.replace(/^-+/, "")
      const next = argv[i + 1]
      if (next === undefined || next.startsWith("-")) {
        flags[key] = true
      } else {
        flags[key] = next
        i++
      }
    } else {
      ;(flags._ as string[]).push(a)
    }
  }
  return flags
}

const HELP = `weworkremotely-cli — search remote jobs on We Work Remotely

USAGE
  bun run src/cli.ts search [flags]
  bun run src/cli.ts detail <url> [--format json|plain]

SEARCH FLAGS
  --query, -q <text>      Keywords (job title, skill, or role). Optional.
  --category <list>       Comma-separated categories: ${Object.keys(CATEGORY_FEEDS).join(", ")}
  --jobage <days>         Posted within N days. Default: all.
  --page <n>              1-indexed page. Default 1.
  --limit, -n <n>         Cap results emitted (client-side).
  --format <fmt>          json (default) | table | plain.

EXAMPLES
  bun run src/cli.ts search -q "data engineer" --format table
  bun run src/cli.ts search -q "machine learning" --jobage 14 --format table
  bun run src/cli.ts search --category programming --limit 20 --format table
  bun run src/cli.ts detail https://weworkremotely.com/jobs/12345 --format plain
`

function commaList(raw: string | boolean): string[] {
  if (typeof raw !== "string") return []
  return raw.split(",").map((s) => s.trim()).filter(Boolean)
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
    const opts: SearchOpts = {
      query: typeof flags.query === "string" ? flags.query : undefined,
      categories: commaList(flags.category ?? ""),
      jobage: typeof flags.jobage === "string" ? parseInt(flags.jobage, 10) || 0 : 0,
      page: typeof flags.page === "string" ? parseInt(flags.page, 10) || 1 : 1,
      limit: typeof flags.limit === "string" ? parseInt(flags.limit, 10) || 25 : 25,
      format: (typeof flags.format === "string" ? flags.format : "json") as SearchOpts["format"],
    }

    if (!["json", "table", "plain"].includes(opts.format)) {
      writeError(`Invalid format: ${opts.format}. Must be json, table, or plain.`, "INVALID_FORMAT")
      return 1
    }

    return runSearch(opts)
  }

  if (cmd === "detail") {
    const url = (flags._ as string[])[1]
    if (!url) {
      writeError("URL is required for detail command.", "MISSING_URL")
      return 1
    }

    const opts: DetailOpts = {
      url,
      format: (typeof flags.format === "string" ? flags.format : "json") as DetailOpts["format"],
    }

    if (!["json", "plain"].includes(opts.format)) {
      writeError(`Invalid format: ${opts.format}. Must be json or plain.`, "INVALID_FORMAT")
      return 1
    }

    return runDetail(opts)
  }

  writeError(`Unknown command: ${cmd}. Use "search" or "detail".`, "UNKNOWN_COMMAND")
  return 1
}

const exitCode = await main()
process.exit(exitCode)
