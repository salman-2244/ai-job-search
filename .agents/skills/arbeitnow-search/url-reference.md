# Arbeitnow API reference

The endpoint, parameters, and response shape this skill depends on. This is the
file to update if the Arbeitnow API changes. Base URL defaults to
`https://www.arbeitnow.com` and is overridable via the `ARBEITNOW_API_URL` env var.

Official API docs: <https://www.arbeitnow.com/blog/job-board-api>

## Authentication

None. The job-board API is a **free public API** — no key, no signup. Arbeitnow's
terms (returned in every response's `meta.terms`) ask callers **not to abuse the
API and to link back to the site**. This skill honours that by bounding its page
scan (`--max-pages`) and always emitting the real posting `url`.

## The one endpoint

```
GET /api/job-board-api?page=<n>
```

Verified against the live API:

| Request | Status |
|---------|--------|
| `GET /api/job-board-api?page=1` | 200 |
| `GET /api/job-board-api?page=2` | 200 (via `links.next`) |

There is **no keyword/search parameter** and **no per-slug detail endpoint** — the
only knob is `page`. Everything else (keyword, tag, location, remote, age filtering,
and per-job lookup) is done client-side by this skill.

## Envelope

```jsonc
{
  "data": [ /* array of job objects, ~176 per page, newest-first */ ],
  "links": {
    "first": "https://www.arbeitnow.com/api/job-board-api?page=1",
    "last":  null,                 // not provided
    "prev":  null,                 // null on page 1
    "next":  "https://www.arbeitnow.com/api/job-board-api?page=2"  // null past the end
  },
  "meta": {
    "current_page": 1,
    "per_page": 176,
    "from": 1,
    "to": 176,
    "path": "https://www.arbeitnow.com/api/job-board-api",
    "terms": "This is a free public API for jobs, please do not abuse. …",
    "info":  "Jobs are updated every hour and order by the `created_at` timestamp. …"
  }
}
```

Notes:
- **Pagination is by `links.next`**, not a total-count/last-page number:
  `meta.last_page` and `meta.total` are **not provided** (they came back `null`),
  so this skill drives pagination off `links.next` and reports its own `matched` /
  `api_pages_fetched` counts instead of a server total.
- The board is **ordered by `created_at` descending** (newest first). This is what
  makes the `--jobage` early-cutoff safe: once a whole page is older than the
  window, no later page can be newer.

## Job object (the fields the skill reads)

```jsonc
{
  "slug": "software-engineer-berlin-233707", // -> result.id, and detail's <slug>
  "company_name": "Preiswecker",             // -> result.company
  "title": "Software Engineer",              // -> result.title
  "description": "<p>…</p>",                  // HTML; sometimes DOUBLE entity-encoded
                                             //   ("&lt;p&gt;…"), stripped client-side
  "remote": false,                           // plain boolean -> result.remote
  "url": "https://www.preiswecker.com/",     // the real posting URL (employer/ATS)
  "tags": ["Software Engineering", "E-Commerce"],
  "job_types": ["Full-time"],
  "location": "Berlin",                       // free-text -> result.location
  "created_at": 1786516800                    // Unix epoch SECONDS -> result.date (ISO)
}
```

- `created_at` is **epoch seconds**, not milliseconds — `isoFromEpoch` multiplies
  by 1000. `date` in results is the ISO string derived from it.
- `remote` is a bare boolean; there is no hybrid signal, so `--remote` supports only
  `remote` / `onsite`.
- `description` HTML is inconsistent: some rows are raw HTML, others are
  entity-encoded HTML that itself contains entities. `cleanHtml` decodes entities
  **repeatedly until stable** (bounded) before stripping tags, so both shapes render
  to clean prose.
- The internal numeric id is never exposed; `slug` is the stable identifier.

## `detail` (client-side, no endpoint)

There is no `GET /jobs/{slug}`. `detail` fetches board pages in order and returns
the first job whose `slug` matches, bounded by `--max-pages`. A posting that has
aged off the recent board is reported as `NOT_FOUND` — the same as a missing slug.

## Parsing notes

- The response is JSON, so there is no HTML card parsing (unlike the scraping
  portals). The only markup handling left client-side is the description's, which
  `cleanHtml` (`cli/src/helpers.ts`) strips into readable text.
- Fetch uses a browser-ish User-Agent, `Accept: application/json`, and exponential
  backoff with jitter on 429/5xx (max 6 retries). A connection error (API
  unreachable) fails fast with a clear message — no retry, since it is not transient
  server load — the graceful-degradation contract: an outage degrades this source
  quickly instead of hanging the caller.
