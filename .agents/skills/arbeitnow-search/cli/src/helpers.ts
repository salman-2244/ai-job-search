// Data source: the Arbeitnow public "job-board-api" (JSON, `{data, links, meta}`
// envelope). Reads are unauthenticated — no API key, the same bar as
// linkedin-search — and unlike the HTML-scraping portals there is no markup to
// parse: we fetch JSON and reshape it into the portal-skill contract's result
// fields. The base URL is swappable via ARBEITNOW_API_URL for a mirror/proxy.
//
// The board serves one big page of ~176 jobs at a time, ordered by created_at
// (newest first), and offers NO server-side keyword search and NO per-slug
// endpoint. So keyword/tag/location/remote filtering happens client-side, and
// `detail` locates a job by scanning recent pages for its slug. Both scans are
// bounded (see search.ts / detail.ts) to honour the API's "please do not abuse"
// note — Arbeitnow asks callers to keep volume low and link back to the site.

export const DEFAULT_BASE_URL = "https://www.arbeitnow.com"
export const API_PATH = "/api/job-board-api"

/** API base URL: ARBEITNOW_API_URL (for a mirror/proxy) or the default. */
export function baseUrl(): string {
  const raw = (process.env.ARBEITNOW_API_URL ?? "").trim()
  return (raw || DEFAULT_BASE_URL).replace(/\/+$/, "")
}

export function writeError(error: string, code: string): void {
  process.stderr.write(JSON.stringify({ error, code }) + "\n")
}

const UA = "Mozilla/5.0 (compatible; arbeitnow-search-cli/1.0; +https://www.arbeitnow.com)"

/** The Arbeitnow response envelope: {data, links, meta}. */
export interface Envelope {
  data: ArbeitnowJob[]
  links?: { first?: string | null; last?: string | null; prev?: string | null; next?: string | null }
  meta?: { current_page?: number; per_page?: number; from?: number; to?: number; path?: string }
  error?: string
}

/**
 * An Arbeitnow job — the fields this skill reads (the wire shape carries no more
 * than these). `created_at` is a Unix epoch in **seconds**.
 */
export interface ArbeitnowJob {
  slug: string
  company_name: string
  title: string
  description: string
  remote: boolean
  url: string
  tags: string[]
  job_types: string[]
  location: string
  created_at: number
}

/**
 * A search result in the portal-skill contract shape. `id` is the slug (what
 * `detail <slug>` consumes) and `date` is the posting date as an ISO timestamp;
 * missing values are `null`, never omitted. `remote`/`tags`/`job_types`/
 * `description` are a permitted superset (the contract requires
 * id/title/company/location/date/url).
 *
 * `description` is here because the board's list endpoint already sends it. Leaving
 * it out cost real ranking accuracy for nothing: every Arbeitnow job reached the
 * pipeline's scorer with an empty body and so could only ever be judged on its
 * title, while `detail` spent a second request recovering text the first response
 * had already delivered.
 */
export interface JobResult {
  id: string
  title: string
  company: string | null
  location: string | null
  date: string | null
  url: string
  remote: boolean
  tags: string[]
  job_types: string[]
  description: string | null
}

/**
 * A job detail. Structurally the same as a search result now that search carries
 * the description too — kept as a distinct name because `detail` is a distinct
 * command with its own contract, and collapsing them would make that contract
 * depend on search's superset staying exactly this wide.
 */
export interface JobDetailResult extends JobResult {
  description: string | null
}

/**
 * GET one page of the Arbeitnow board. Retries 429/5xx (transient server states)
 * with backoff; a connection failure fails fast with a clear message — no retry,
 * so an outage degrades this source quickly rather than hanging the caller (the
 * graceful-degradation contract). `page` is 1-indexed.
 */
export async function apiGet(page: number): Promise<Envelope> {
  const url = `${baseUrl()}${API_PATH}?page=${page}`
  const maxRetries = 6
  let delay = 500

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    let response: Response
    try {
      response = await fetch(url, {
        headers: { "User-Agent": UA, Accept: "application/json" },
        redirect: "follow",
        signal: AbortSignal.timeout(15000),
      })
    } catch (e) {
      throw new Error(
        `could not reach the Arbeitnow API at ${baseUrl()} (${e instanceof Error ? e.message : String(e)})`,
      )
    }

    if (response.status === 429 || response.status >= 500) {
      if (attempt === maxRetries) {
        throw new Error(`Arbeitnow API request failed: ${response.status} ${response.statusText}`)
      }
      await sleep(delay + Math.floor(Math.random() * 500))
      delay = Math.min(delay * 2, 8000)
      continue
    }

    const body = (await response.json().catch(() => null)) as Envelope | null
    if (!response.ok) {
      throw new Error(body?.error || `Arbeitnow API request failed: ${response.status} ${response.statusText}`)
    }
    if (!body || !Array.isArray(body.data)) {
      throw new Error("Arbeitnow API returned an unparseable response body")
    }
    return body
  }
  // Unreachable in practice; the loop returns or throws on the last attempt.
  throw new Error("Arbeitnow API request failed after retries")
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}

/** Convert a Unix epoch (seconds) to an ISO timestamp, or null when absent/invalid. */
export function isoFromEpoch(seconds: number | null | undefined): string | null {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return null
  return new Date(seconds * 1000).toISOString()
}

/** Reshape an Arbeitnow job into the contract search-result fields. */
export function toResult(j: ArbeitnowJob): JobResult {
  return {
    id: j.slug,
    title: j.title || "(untitled)",
    company: j.company_name || null,
    location: j.location || null,
    date: isoFromEpoch(j.created_at),
    url: j.url || `${baseUrl()}/view/${j.slug}`,
    remote: Boolean(j.remote),
    tags: Array.isArray(j.tags) ? j.tags : [],
    job_types: Array.isArray(j.job_types) ? j.job_types : [],
    // The full cleaned body, not a snippet. The API already sent it, `detail`
    // returns exactly this so a truncation here would make the two commands
    // disagree for no reason, and truncation policy already lives downstream in
    // `scripts/aggregate_jobs.py` (500 chars into `description_snippet`). Output
    // size is bounded by `--limit`, not by how many pages were scanned.
    description: cleanHtml(j.description),
  }
}

/**
 * Reshape an Arbeitnow job into the detail result. Identical to `toResult` now that
 * search carries the description; the spread is kept so `detail` keeps returning
 * whatever the contract shape is, rather than a hand-listed copy that could drift.
 */
export function toDetail(j: ArbeitnowJob): JobDetailResult {
  return { ...toResult(j), description: cleanHtml(j.description) }
}

function numericEntity(cp: number): string {
  return cp >= 0 && cp <= 0x10ffff ? String.fromCodePoint(cp) : ""
}

function decodeHtmlEntities(text: string): string {
  return text
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&apos;/g, "'")
    .replace(/&#(\d+);/g, (_, dec) => numericEntity(parseInt(dec, 10)))
    .replace(/&#[xX]([0-9a-fA-F]+);/g, (_, hex) => numericEntity(parseInt(hex, 16)))
    .replace(/&nbsp;/g, " ")
}

/**
 * Decode HTML entities repeatedly until stable. Arbeitnow descriptions are
 * inconsistent: some are raw HTML (`<p>…`), others are entity-encoded HTML
 * (`&lt;p&gt;…`) that itself contains entities (`&amp;nbsp;`), so a single decode
 * pass is not enough to reveal the real tags. Bounded to avoid pathological input.
 */
function decodeEntitiesDeep(s: string): string {
  let prev = s
  let cur = decodeHtmlEntities(s)
  for (let i = 0; i < 5 && cur !== prev; i++) {
    prev = cur
    cur = decodeHtmlEntities(cur)
  }
  return cur
}

/**
 * Strip an Arbeitnow description's HTML into readable prose: entity-encoded HTML
 * is decoded first so its tags become real, then block/line-break tags become
 * newlines, remaining tags are removed, and whitespace is normalized. Null for
 * empty input.
 */
export function cleanHtml(html: string | null | undefined): string | null {
  if (!html) return null
  const decoded = decodeEntitiesDeep(html)
  const withBreaks = decoded
    .replace(/<\s*br\s*\/?>/gi, "\n")
    .replace(/<\/(p|li|ul|ol|div|h\d)>/gi, "\n")
  const text = withBreaks
    .replace(/<[^>]+>/g, " ")
    .replace(/[ \t]+/g, " ")
    .replace(/ *\n */g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim()
  return text || null
}

/**
 * A lowercased searchable blob for a job: title, company, tags, job types, and
 * the HTML-stripped description. Used for client-side keyword matching, since the
 * API has no server-side search.
 */
export function haystack(j: ArbeitnowJob): string {
  return [
    j.title,
    j.company_name,
    ...(Array.isArray(j.tags) ? j.tags : []),
    ...(Array.isArray(j.job_types) ? j.job_types : []),
    cleanHtml(j.description) ?? "",
  ]
    .join(" \n ")
    .toLowerCase()
}

/**
 * Does a job match a free-text query? Space-separated terms are ANDed
 * (case-insensitive substring), so `"machine learning"` requires both words to
 * appear somewhere in the job's searchable text. An empty query matches everything.
 */
export function matchesQuery(j: ArbeitnowJob, query: string | undefined): boolean {
  if (!query || !query.trim()) return true
  const hay = haystack(j)
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((term) => hay.includes(term))
}

/** Does a job carry any of the given tags/job-types (case-insensitive)? Empty list = match. */
export function matchesTags(j: ArbeitnowJob, tags: string[]): boolean {
  if (tags.length === 0) return true
  const owned = new Set([...(j.tags ?? []), ...(j.job_types ?? [])].map((t) => t.toLowerCase()))
  return tags.some((t) => owned.has(t.toLowerCase()))
}

/** Does a job's location contain the given substring (case-insensitive)? Empty = match. */
export function matchesLocation(j: ArbeitnowJob, location: string | undefined): boolean {
  if (!location || !location.trim()) return true
  return (j.location || "").toLowerCase().includes(location.trim().toLowerCase())
}

/**
 * Remote filter. `remote` keeps jobs flagged remote; `onsite` keeps the rest.
 * The API exposes only a boolean, so there is no hybrid signal. Undefined = match.
 */
export function matchesRemote(j: ArbeitnowJob, mode: string | undefined): boolean {
  if (!mode) return true
  const m = mode.toLowerCase()
  if (m === "remote") return Boolean(j.remote)
  if (m === "onsite" || m === "on-site") return !j.remote
  return true // unknown mode: don't filter (the CLI validates the flag separately)
}

/**
 * Is a job newer than `days` old, per its created_at epoch? `days >= 9999` (the
 * CLI's "unset" sentinel) or a missing timestamp means no age filter for that job.
 */
export function withinJobAge(j: ArbeitnowJob, days: number, now: number): boolean {
  if (!days || days <= 0 || days >= 9999) return true
  if (!j.created_at || !Number.isFinite(j.created_at)) return true
  const cutoff = now - days * 86400 * 1000
  return j.created_at * 1000 >= cutoff
}

/** Extract an Arbeitnow slug from a bare slug or a /view/<slug> or /jobs/<slug> URL. */
export function normalizeSlug(input: string): string | null {
  const trimmed = input.trim()
  if (!trimmed) return null
  const m = trimmed.match(/\/(?:view|jobs)\/([^/?#]+)/)
  if (m) return m[1]
  // A bare slug: alphanumerics and hyphens (no path/scheme).
  if (/^[a-z0-9][a-z0-9-]*$/i.test(trimmed)) return trimmed
  return null
}
