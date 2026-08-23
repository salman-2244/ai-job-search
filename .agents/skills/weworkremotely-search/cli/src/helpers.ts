// Data source: We Work Remotely public RSS feeds (XML).
// No authentication required — same zero-signup bar as linkedin-search.
// RSS feeds are publicly accessible at weworkremotely.com/remote-job-rss-feed
// and category-specific feeds.

export const DEFAULT_BASE_URL = "https://weworkremotely.com"

export function writeError(error: string, code: string): void {
  process.stderr.write(JSON.stringify({ error, code }) + "\n")
}

const UA = "weworkremotely-search-skill/1.0 (+https://weworkremotely.com)"

/** Primary feed: all jobs (100 items, all categories) */
export const ALL_JOBS_FEED = "/remote-jobs.rss"

/** Category-specific feeds (only working ones — others return 403/empty 301) */
export const CATEGORY_FEEDS: Record<string, string> = {
  programming: "/categories/remote-programming-jobs.rss",
  design: "/categories/remote-design-jobs.rss",
}

/** Category names as they appear in the RSS <category> tag */
export const RSS_CATEGORIES: Record<string, string> = {
  programming: "Full-Stack Programming",
  design: "Design",
  marketing: "Sales and Marketing",
  "customer-support": "Customer Support",
  "finance-legal": "Management and Finance",
  devops: "DevOps and Sysadmin",
  business: "All Other Remote",
  product: "Product",
}

/** Fetch XML from We Work Remotely RSS feeds with retry */
export async function fetchRSS(url: string): Promise<string> {
  const maxRetries = 3
  let delay = 1000

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const response = await fetch(url, {
        headers: {
          "User-Agent": UA,
          Accept: "application/rss+xml, application/xml, text/xml, */*",
        },
        redirect: "follow",
        signal: AbortSignal.timeout(15000),
      })

      if (response.status === 429 || response.status >= 500) {
        if (attempt === maxRetries) {
          throw new Error(`Request failed: ${response.status} ${response.statusText}`)
        }
        await new Promise((r) => setTimeout(r, delay + Math.floor(Math.random() * 500)))
        delay = Math.min(delay * 2, 8000)
        continue
      }

      if (response.status === 404) return ""
      if (response.status === 301 || response.status === 302) {
        // Redirect with empty body — treat as unavailable
        return ""
      }
      if (!response.ok) {
        throw new Error(`Request failed: ${response.status} ${response.statusText}`)
      }

      return response.text()
    } catch (e) {
      if (attempt === maxRetries) {
        throw new Error(`Request failed after retries: ${e instanceof Error ? e.message : String(e)}`)
      }
      await new Promise((r) => setTimeout(r, delay))
      delay = Math.min(delay * 2, 8000)
    }
  }

  throw new Error("Request failed after retries")
}

export interface WWRJob {
  id: string
  title: string
  company: string | null
  location: string | null
  date: string | null
  url: string
  description: string | null
  category: string | null
}

/**
 * Parse RSS XML into job objects.
 * RSS 2.0 format: <item> contains <title>, <link>, <pubDate>, <description>, etc.
 */
export function parseRSSJobs(xml: string, category: string): WWRJob[] {
  const jobs: WWRJob[] = []
  const itemRegex = /<item>([\s\S]*?)<\/item>/gi
  let match

  while ((match = itemRegex.exec(xml)) !== null) {
    const itemXml = match[1]
    const title = extractTag(itemXml, "title")
    const link = extractTag(itemXml, "link")
    const pubDate = extractTag(itemXml, "pubDate")
    const description = extractTag(itemXml, "description")

    if (!title || !link) continue

    // Extract company from title (format: "Company Name: Job Title")
    let company = null
    let jobTitle = title
    const colonIdx = title.indexOf(":")
    if (colonIdx > 0 && colonIdx < 60) {
      company = title.substring(0, colonIdx).trim()
      jobTitle = title.substring(colonIdx + 1).trim()
    }

    jobs.push({
      id: link.split("/").pop() || link,
      title: jobTitle,
      company,
      location: "Remote", // All WWR jobs are remote
      date: pubDate ? new Date(pubDate).toISOString() : null,
      url: link,
      description: description ? stripHTML(decodeHTMLEntities(description)) : null,
      category,
    })
  }

  return jobs
}

function extractTag(xml: string, tag: string): string | null {
  // Handle CDATA sections
  const cdataRegex = new RegExp(`<${tag}[^>]*>\\s*<!\\[CDATA\\[([\\s\\S]*?)\\]\\]>\\s*</${tag}>`, "i")
  const cdataMatch = cdataRegex.exec(xml)
  if (cdataMatch) return cdataMatch[1].trim()

  // Handle regular tags
  const regex = new RegExp(`<${tag}[^>]*>([\\s\\S]*?)</${tag}>`, "i")
  const match = regex.exec(xml)
  return match ? match[1].trim() : null
}

function stripHTML(html: string): string {
  return html.replace(/<[^>]*>/g, "").replace(/\s+/g, " ").trim()
}

function decodeHTMLEntities(text: string): string {
  return text
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&#x27;/g, "'")
    .replace(/&#x2F;/g, "/")
}
