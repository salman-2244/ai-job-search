import {
  DEFAULT_BASE_URL,
  fetchRSS,
  parseRSSJobs,
  writeError,
  type WWRJob,
} from "../helpers.js"

export interface DetailOpts {
  url: string
  format: "json" | "plain"
}

export async function runDetail(opts: DetailOpts): Promise<number> {
  try {
    // We Work Remotely doesn't have a separate detail page API
    // The RSS feed contains the description already
    // We'll fetch all feeds and search for the URL
    const { CATEGORY_FEEDS } = await import("../helpers.js")

    for (const [cat, feedPath] of Object.entries(CATEGORY_FEEDS)) {
      const feedUrl = `${DEFAULT_BASE_URL}${feedPath}`
      const xml = await fetchRSS(feedUrl)
      if (!xml) continue

      const jobs = parseRSSJobs(xml, cat)
      const job = jobs.find((j) => j.url === opts.url || j.url.includes(opts.url))

      if (job) {
        if (opts.format === "plain") {
          process.stdout.write(
            `Title: ${job.title}\n` +
            `Company: ${job.company || "—"}\n` +
            `Location: ${job.location || "—"}\n` +
            `Date: ${job.date ? new Date(job.date).toISOString().slice(0, 10) : "—"}\n` +
            `URL: ${job.url}\n` +
            `Category: ${job.category || "—"}\n` +
            (job.description ? `\n${job.description}\n` : "")
          )
        } else {
          process.stdout.write(JSON.stringify(job) + "\n")
        }
        return 0
      }
    }

    writeError(`Job not found for URL: ${opts.url}`, "NOT_FOUND")
    return 1
  } catch (e) {
    writeError(e instanceof Error ? e.message : String(e), "DETAIL_FAILED")
    return 1
  }
}
