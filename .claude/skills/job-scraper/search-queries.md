# Search Queries for Job Scraper

<!-- Europe-wide job search for Salman Ahmed: AI / Data / Analytics / Supply Chain positions.
     Target: English-speaking roles across Europe. Priorities: AI, Data Science, BI, Supply Chain Analytics. -->

## Installed portal CLIs (primary for `/scrape`)

`/scrape` discovers every portal skill under `.agents/skills/*/SKILL.md` and runs its CLI first. You do **not** need a matching `site:` line below for those CLIs to run.

### Enabled CLIs (4 active portals)

1. **`linkedin-search`** — LinkedIn jobs (Priority 1). Country-agnostic; pass European locations.
   - **Best for:** Country- and region-level sweeps across target markets (HU, DE, NL, AT, IE, FI, SE, CH, UK), narrowed to cities only when a specific metro matters
   - **Location values:** country names (`Hungary`, `Germany`) and regions (`European Union`) both work — verified by live probe on 2026-08-18, where `Hungary` returned Budapest/Üllő results and `European Union` returned NL/LT results. An earlier note here claimed specific cities were required for EU filtering; that was wrong. `--location "Remote"` does still return global results, so filter by country or region rather than by the word "Remote".
   - **Coverage:** All EU countries + UK, strong in DACH and Nordics
   - **Daily pipeline:** query × geo pairs come from `config/search_matrix.json`, not from this file — the pipeline caps LinkedIn at `max_requests_per_run` and rotates the remaining pairs across days (see `scripts/build_search_plan.py`)

2. **`freehire-search`** — European tech job aggregator (76k+ EU jobs).
   - **Best for:** Broad EU-wide sweeps, country filtering via `--region eu` or `--country DE,NL,IE`
   - **Limitation:** Tech-focused; some non-tech roles exist but coverage is uneven
   - **Coverage:** All EU countries, strong in Spain, Poland, Germany, Ireland, Switzerland

3. **`arbeitnow-search`** — Germany/EU-focused aggregator (English-friendly tech roles).
   - **Best for:** Targeted Germany city searches (Berlin, Munich, Frankfurt)
   - **Limitation:** Germany-centric; poor remote coverage; broad queries return low precision
   - **Coverage:** Primarily Germany, limited EU

4. **`weworkremotely-search`** — Remote-only job board via RSS (100 jobs, all categories).
   - **Best for:** Remote-first roles worldwide, many European-friendly; strong in tech, design, marketing
   - **Limitation:** Remote-only (no onsite/hybrid); ~100 jobs in feed; some postings are US-centric
   - **Coverage:** Worldwide remote, often European-friendly (Stripe, Vercel, Typeform, etc.)

### Disabled (Danish portals — keep off unless Denmark becomes a target market)
- `jobindex-search`, `jobbank-search`, `jobnet-search`, `jobdanmark-search`

## WebSearch Fallback (for portals without CLIs)

The `site:` query templates below are the **WebSearch fallback** — for portals without a CLI (StepStone, Xing, Indeed, The Local, national boards behind bot protection), company career pages, or when a CLI fails.

### Priority 2 — Major European aggregators (WebSearch fallback)
- **eures.europa.eu** — EU's official cross-border job portal (no public API, WebSearch only)
- **eu.jobs / europelanguagejobs.com** — pan-European aggregators (WebSearch only)

### Priority 3 — National boards (WebSearch `site:` fallback; most are bot-protected)
- Germany: `stepstone.de`, `xing.com/jobs`
- Hungary: `profession.hu`
- Austria: `karriere.at`, `stepstone.at`
- Finland: `duunitori.fi`, `oikotie.fi`, `jobly.fi`
- Sweden: `arbetsformedlingen.se` (Platsbanken), `thelocal.se/jobs`
- Netherlands: `nationalevacaturebank.nl`, `indeed.nl`, `thelocal.nl/jobs`
- Ireland: `irishjobs.ie`, `jobs.ie`
- Switzerland: `jobs.ch`, `jobup.ch`
- UK: `indeed.co.uk`, `reed.co.uk`, `cv-library.co.uk`, `otta.com`

**Note:** These portals are listed for reference but have no CLI integration. Use WebSearch as fallback only — CLIs take priority.

## Search Strategy

### Language Scope
Salman's CV language is English; working languages are English (professional), Urdu/Punjabi (native), Hungarian (elementary A2). The target is **English-speaking positions across Europe**, so all queries are written in **English**. Do not machine-translate them into German/Swedish/Dutch/etc.: a posting that *requires* a language Salman doesn't work in is excluded by the Language Gate anyway, and English-language queries are the honest match to the search goal.

### CLI Invocation Matrix

| Target | LinkedIn `--location` | freehire `--country` | arbeitnow `--location` | weworkremotely `--query` |
|--------|----------------------|---------------------|----------------------|--------------------------|
| Hungary | `"Budapest, Hungary"` | HU | — | — |
| Germany - Berlin | `"Berlin, Germany"` | DE | Berlin | — |
| Germany - Munich | `"Munich, Germany"` | DE | Munich | — |
| Germany - Frankfurt | `"Frankfurt, Germany"` | DE | Frankfurt | — |
| Austria - Vienna | `"Vienna, Austria"` | AT | — | — |
| Netherlands - Amsterdam | `"Amsterdam, Netherlands"` | NL | — | — |
| Finland - Helsinki | `"Helsinki, Finland"` | FI | — | — |
| Sweden - Stockholm | `"Stockholm, Sweden"` | SE | — | — |
| Ireland - Dublin | `"Dublin, Ireland"` | IE | — | — |
| Switzerland - Zurich | `"Zurich, Switzerland"` | CH | — | — |
| UK - London | `"London, United Kingdom"` | GB | — | — |
| Remote EU | `"Remote"` (broken — use freehire instead) | EU | — | (all jobs are remote) |
| Remote Worldwide | — | — | — | (all jobs are remote) |

### Freehire Country Codes (for `--country` flag)
- DE = Germany, AT = Austria, CH = Switzerland
- NL = Netherlands, BE = Belgium
- SE = Sweden, NO = Norway, FI = Finland, DK = Denmark
- IE = Ireland, GB = United Kingdom
- HU = Hungary, PL = Poland, CZ = Czech Republic
- ES = Spain, PT = Portugal, IT = Italy, FR = France

### Freehire Region Codes (for `--region` flag)
- `eu` = European Union + EEA countries
- `global` = worldwide
- `none` = unclassified remote roles

## Query Categories

### Priority 1: AI / Machine Learning (strongest, most desired direction)

**CLI invocations (primary):**
```
freehire-search: search -q "AI Engineer" --region eu --jobage 14 --limit 20
freehire-search: search -q "Machine Learning Engineer" --country DE,NL,IE --jobage 14 --limit 20
freehire-search: search -q "Generative AI" --region eu --jobage 14 --limit 15
linkedin-search: search -q "AI Engineer" -l "Berlin, Germany" --jobage 14 --limit 10
linkedin-search: search -q "Machine Learning" -l "Amsterdam, Netherlands" --jobage 14 --limit 10
linkedin-search: search -q "Generative AI" -l "Dublin, Ireland" --jobage 14 --limit 10
weworkremotely-search: search -q "AI" --jobage 14 --limit 20
weworkremotely-search: search -q "machine learning" --jobage 14 --limit 10
```

**WebSearch fallback:**
```
site:eures.europa.eu "AI Engineer" English
site:stepstone.de "Machine Learning" English
"LLM" OR "generative AI" engineer Europe English visa sponsorship
```

### Priority 2: Data Science / Data Analytics / Business Intelligence (domain strength)

**CLI invocations (primary):**
```
freehire-search: search -q "Data Scientist" --region eu --jobage 14 --limit 20
freehire-search: search -q "Data Analyst" --country DE,NL,IE,SE --jobage 14 --limit 20
freehire-search: search -q "Business Intelligence" --region eu --jobage 14 --limit 15
linkedin-search: search -q "Data Scientist" -l "Berlin, Germany" --jobage 14 --limit 10
linkedin-search: search -q "Data Analyst" -l "Amsterdam, Netherlands" --jobage 14 --limit 10
linkedin-search: search -q "Power BI" -l "Dublin, Ireland" --jobage 14 --limit 10
linkedin-search: search -q "Business Intelligence" -l "Budapest, Hungary" --jobage 14 --limit 10
weworkremotely-search: search -q "data" --jobage 14 --limit 15
```

**WebSearch fallback:**
```
site:stepstone.de "Data Scientist" English
"BI Developer" OR "Analytics Engineer" "Power BI" Europe English
```

### Priority 3: Supply Chain Analytics / Operations Analytics (direct domain experience)

**CLI invocations (primary):**
```
freehire-search: search -q "Supply Chain" analyst --region eu --jobage 14 --limit 15
freehire-search: search -q "Operations Analyst" --region eu --jobage 14 --limit 15
linkedin-search: search -q "Supply Chain Analytics" -l "Budapest, Hungary" --jobage 14 --limit 10
linkedin-search: search -q "Operations Analyst" -l "Berlin, Germany" --jobage 14 --limit 10
linkedin-search: search -q "Procurement Analytics" -l "Amsterdam, Netherlands" --jobage 14 --limit 10
```

**WebSearch fallback:**
```
"procurement analytics" OR "operations analytics" Europe English
```

### Priority 4: AI Product Management / AI Automation (pivot / growth direction)

**CLI invocations (primary):**
```
freehire-search: search -q "AI Product Manager" --region eu --jobage 14 --limit 15
freehire-search: search -q "AI Automation" --region eu --jobage 14 --limit 15
linkedin-search: search -q "AI Product Manager" -l "Berlin, Germany" --jobage 14 --limit 10
linkedin-search: search -q "Intelligent Automation" -l "Dublin, Ireland" --jobage 14 --limit 10
```

**WebSearch fallback:**
```
"Automation Engineer" ("Power Automate" OR RPA OR AI) Europe English
```

### Priority 5: Broader technical / consulting (wider net, English-speaking)

**CLI invocations (primary):**
```
freehire-search: search -q "Data Engineer" --region eu --jobage 14 --limit 20
freehire-search: search -q "Python Developer" --region eu --jobage 14 --limit 15
linkedin-search: search -q "Data Engineer" -l "London, United Kingdom" --jobage 14 --limit 10
linkedin-search: search -q "Python Developer" -l "Zurich, Switzerland" --jobage 14 --limit 10
weworkremotely-search: search --category programming --jobage 14 --limit 20
weworkremotely-search: search -q "python" --jobage 14 --limit 10
```

**WebSearch fallback:**
```
site:otta.com machine learning
"Python developer" (data OR ML OR AI) Europe English "visa sponsorship"
```

## Location Filter

Europe-wide search. Tiers (used when ranking, not to hard-exclude — remote can override geography):

- **Ideal:** Budapest / Hungary (no relocation needed); fully-remote roles open to EU/Europe or worldwide.
- **Acceptable (relocation for the right role):** Germany, Austria, Finland, Sweden, Netherlands, Ireland — target EU/EEA countries. Note the sponsorship/eligibility question (see `04-job-evaluation.md`'s candidate-specific calibration) since Salman is a non-EU national.
- **Acceptable with extra authorization:** Switzerland, United Kingdom (non-EU work-permit regimes; sponsorship almost always required — flag it).
- **Borderline:** other European countries not on the target list — include only for an exceptional AI/leadership role, flag for the user.
- **Too far / exclude:** roles outside Europe with no remote-EU option; on-site-only roles far from Budapest with no relocation support and no remote option.

## Eligibility & Sponsorship Filter

Salman is a non-EU national based in Budapest on a Hungarian permit. When filtering scraped results:
- Positively flag postings that say **"visa sponsorship available"**, **"we relocate"**, or **"international applicants welcome"**.
- For non-Hungarian roles that state **"must already have the right to work in <country>"** / **"no sponsorship"**, mark as a **sponsorship FLAG** (near-fail), not a silent pass — surface it so Salman can judge.
- Hungary-based and remote-EU-resident-in-Hungary roles need no sponsorship note.

## Language Filter

Working languages and levels are in CLAUDE.md's Languages table (English professional; Hungarian elementary). Apply `04-job-evaluation.md`'s Language Gate: a posting requiring a language not on the table as a job condition (e.g. "fluent German required", "must communicate with the team in Swedish") is **excluded**; a posting requiring a *higher English level* than declared is **flagged**, not excluded. Postings merely *written* in another language that only need English on the job are fine. Because the search targets English-speaking positions, prefer postings that state English is the working language.

## Date Filter

Only include jobs posted within the last 14 days, or with an application deadline that has not yet passed. If a posting date cannot be determined, include it but flag as "date unknown".

## Adapting Queries

If the user specifies a focus area, select queries from the matching category and generate 2-3 custom queries for that focus. Examples:
- "/scrape AI Berlin" -> Priority 1 queries scoped to Berlin + remote-DE
- "/scrape remote" -> Priority 1-2 queries with freehire `--region eu`
- "/scrape supply chain" -> Priority 3 queries across the target countries
- "/scrape germany" -> Priority 1-2 queries scoped to DE cities
- "/scrape netherlands" -> Priority 1-2 queries scoped to NL cities
- "/scrape hungary" -> Priority 1-3 queries scoped to Budapest

## Search Execution Rules

1. **CLI priority:** Always run CLI tools first; fall back to WebSearch only for portals without CLIs or when CLIs fail.
2. **Parallel execution:** Run all portal CLI calls in parallel where possible.
3. **Deduplication:** Check `seen_jobs.json` AND `job_search_tracker.csv` before presenting new jobs.
4. **Health checks:** If a portal returns zero results or garbled data, run the health check protocol (Step 4.75 in `/scrape`).
5. **Rate limiting:** Keep volume low for LinkedIn (ToS concern); Arbeitnow and Freehire are public APIs but respect their limits.
6. **UTM parameters:** Strip UTM parameters from Freehire URLs before storing in `seen_jobs.json`.
