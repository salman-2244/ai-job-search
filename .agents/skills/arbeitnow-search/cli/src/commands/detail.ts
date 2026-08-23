import { apiGet, toDetail, normalizeSlug, writeError, type JobDetailResult } from "../helpers.js"

export interface DetailOpts {
  id: string // an Arbeitnow slug or a /view/<slug> URL
  maxPages: number
  format: "json" | "plain"
}

/**
 * Locate a job by slug. Arbeitnow has NO per-slug endpoint, so we scan the board
 * (newest-first) for a matching slug, bounded by `maxPages`. A job that has aged
 * off the board (only recent postings are served) will not be found — reported as
 * NOT_FOUND, same as a genuinely missing slug.
 */
export async function runDetail(opts: DetailOpts): Promise<number> {
  const slug = normalizeSlug(opts.id)
  if (!slug) {
    writeError(`could not parse an Arbeitnow slug from "${opts.id}"`, "BAD_ID")
    return 1
  }
  try {
    for (let apiPage = 1; apiPage <= opts.maxPages; apiPage++) {
      const env = await apiGet(apiPage)
      const found = (env.data ?? []).find((j) => j.slug === slug)
      if (found) {
        const job = toDetail(found)
        if (opts.format === "plain") {
          process.stdout.write(renderPlain(job) + "\n")
        } else {
          process.stdout.write(JSON.stringify(job, null, 2) + "\n")
        }
        return 0
      }
      if (!env.links?.next) break // exhausted the board
    }
    writeError(
      `job "${slug}" not found in the ${opts.maxPages} most recent board pages (it may have aged off the board — Arbeitnow serves only recent postings)`,
      "NOT_FOUND",
    )
    return 1
  } catch (e) {
    writeError(e instanceof Error ? e.message : String(e), "DETAIL_FAILED")
    return 1
  }
}

function renderPlain(job: JobDetailResult): string {
  const lines = [
    job.title,
    `${job.company ?? "—"} · ${job.location ?? "—"} · ${job.remote ? "remote" : "on-site"}`,
    job.date ? `Posted: ${job.date.slice(0, 10)}` : "",
    job.job_types.length ? `Type: ${job.job_types.join(", ")}` : "",
    job.tags.length ? `Tags: ${job.tags.join(", ")}` : "",
    "",
    job.description || "(no description)",
    "",
    `URL: ${job.url}`,
    `slug: ${job.id}`,
  ].filter((l) => l !== "")
  return lines.join("\n")
}
