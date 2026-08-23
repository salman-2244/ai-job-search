import {
  DEFAULT_BASE_URL,
  ALL_JOBS_FEED,
  CATEGORY_FEEDS,
  RSS_CATEGORIES,
  fetchRSS,
  parseRSSJobs,
  writeError,
  type WWRJob,
} from "../helpers.js"

export interface SearchOpts {
  query?: string
  categories: string[]
  jobage: number
  page: number
  limit: number
  format: "json" | "table" | "plain"
}

function shortDate(date: string | null): string {
  return date ? new Date(date).toISOString().slice(0, 10) : "—"
}

function renderTable(jobs: WWRJob[]): string {
  if (jobs.length === 0) return "No results."

  const rows = jobs.map((j) => {
    const title = (j.title || "").slice(0, 40).padEnd(40)
    const company = (j.company || "—").slice(0, 24).padEnd(24)
    const date = shortDate(j.date)
    return `${title} ${company} ${date}`
  })

  const header = "TITLE".padEnd(40) + " " + "COMPANY".padEnd(24) + " DATE"
  return [header, "-".repeat(header.length), ...rows].join("\n")
}

export async function runSearch(opts: SearchOpts): Promise<number> {
  try {
    let allJobs: WWRJob[] = []

    if (opts.categories.length > 0) {
      // Try category-specific feeds first, fall back to all-jobs feed with filtering
      const catFeedJobs: WWRJob[] = []
      const uncategorizedCats: string[] = []

      for (const cat of opts.categories) {
        const feedPath = CATEGORY_FEEDS[cat]
        if (feedPath) {
          const url = `${DEFAULT_BASE_URL}${feedPath}`
          const xml = await fetchRSS(url)
          if (xml) {
            catFeedJobs.push(...parseRSSJobs(xml, cat))
          }
        } else {
          uncategorizedCats.push(cat)
        }
      }

      if (catFeedJobs.length > 0) {
        allJobs.push(...catFeedJobs)
      }

      // For categories without dedicated feeds, use all-jobs feed and filter by RSS category name
      if (uncategorizedCats.length > 0) {
        const allJobsXml = await fetchRSS(`${DEFAULT_BASE_URL}${ALL_JOBS_FEED}`)
        if (allJobsXml) {
          const allJobsFromFeed = parseRSSJobs(allJobsXml, "all")
          const rssCatNames = uncategorizedCats
            .map((c) => RSS_CATEGORIES[c])
            .filter(Boolean)

          if (rssCatNames.length > 0) {
            allJobs.push(
              ...allJobsFromFeed.filter((j) =>
                rssCatNames.some(
                  (name) => j.category && j.category.toLowerCase().includes(name.toLowerCase())
                )
              )
            )
          }
        }
      }
    } else {
      // No category filter — fetch all jobs
      const xml = await fetchRSS(`${DEFAULT_BASE_URL}${ALL_JOBS_FEED}`)
      if (xml) {
        allJobs = parseRSSJobs(xml, "all")
      }
    }

    // Filter by query if provided
    if (opts.query) {
      const q = opts.query.toLowerCase()
      allJobs = allJobs.filter(
        (j) =>
          (j.title && j.title.toLowerCase().includes(q)) ||
          (j.company && j.company.toLowerCase().includes(q)) ||
          (j.description && j.description.toLowerCase().includes(q))
      )
    }

    // Filter by jobage if provided
    if (opts.jobage > 0) {
      const cutoff = Date.now() - opts.jobage * 24 * 60 * 60 * 1000
      allJobs = allJobs.filter((j) => {
        if (!j.date) return true // Include undated jobs
        return new Date(j.date).getTime() > cutoff
      })
    }

    // Sort by date (newest first)
    allJobs.sort((a, b) => {
      if (!a.date) return 1
      if (!b.date) return -1
      return new Date(b.date).getTime() - new Date(a.date).getTime()
    })

    // Paginate
    const totalCount = allJobs.length
    const start = (opts.page - 1) * opts.limit
    const pagedJobs = allJobs.slice(start, start + opts.limit)

    // Output
    const meta = {
      count: pagedJobs.length,
      page: opts.page,
      total: totalCount,
    }

    if (opts.format === "table") {
      process.stdout.write(renderTable(pagedJobs) + "\n")
    } else if (opts.format === "plain") {
      for (const job of pagedJobs) {
        process.stdout.write(
          `Title: ${job.title}\n` +
          `Company: ${job.company || "—"}\n` +
          `Location: ${job.location || "—"}\n` +
          `Date: ${shortDate(job.date)}\n` +
          `URL: ${job.url}\n` +
          `Category: ${job.category || "—"}\n` +
          (job.description ? `\n${job.description}\n` : "") +
          "\n---\n\n"
        )
      }
    } else {
      // JSON
      process.stdout.write(JSON.stringify({ meta, results: pagedJobs }) + "\n")
    }

    return 0
  } catch (e) {
    writeError(e instanceof Error ? e.message : String(e), "SEARCH_FAILED")
    return 1
  }
}
