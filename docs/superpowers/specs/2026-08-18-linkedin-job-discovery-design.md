---
date: 2026-08-18
topic: LinkedIn-led job discovery, profile calibration, and match-gated document generation
status: draft — awaiting approval
---

# LinkedIn-Led Job Discovery + Profile Calibration

## Goal

Make LinkedIn the primary discovery source, fix the candidate profile and scoring
framework so they recognize Salman's *whole* profile (not just AI Engineer roles),
treat LinkedIn job-alert emails as a priority signal, and generate CVs and cover
letters **only** for genuine matches.

## Decisions locked with the user (2026-08-18)

| Decision | Choice |
|---|---|
| Work eligibility | **Hungarian residence/work permit only** (non-EU national). Roles outside Hungary are FLAGged for sponsorship, never silently passed. Confirms the standing assumption in `CLAUDE.md` |
| Document generation gate | **Score ≥ 75 (Strong Fit)** — OR **score ≥ 60 when the job was alert-matched by LinkedIn** |
| LinkedIn request volume | **Moderate: ~40–60 requests/day** (5 tracks × ~10 geos, one page each, sequential + delayed) |
| Authentication | **None.** No LinkedIn password, token, cookie, or session is stored anywhere |
| LinkedIn score bonus | **None.** Alert-match changes the *gate* and *ordering*, never the fit score |

## Non-goals

- No authenticated LinkedIn scraping, no browser automation against LinkedIn, no
  saved-jobs or "recommended for you" harvesting. Rejected on account-risk grounds.
- No auto-apply, no auto-submit, no auto-email to employers. The existing
  `safety` block in `config/automation.json` stays untouched.
- No changes to the LaTeX templates, the launchd plist, or `/apply`'s document workflow.

---

## Part 1 — Profile calibration (do this first)

The profile is the root cause of the "only AI Engineer roles" symptom. Two files
carry it, and both are wrong in ways that distort every downstream score.

### 1.1 Define five profile tracks

Today `prompts/pipeline_phase1_rank.md:29` scores career alignment as
`AI/ML/Data/Analytics = 100, Software Engineering = 50, Other = 0`. Under that rule
a **Performance Manager** posting — Salman's literal current job title — scores 0.
Replace the single AI-centric ladder with five equally-valid tracks, added to
`01-candidate-profile.md` as a new `## Profile Tracks` section:

| Track | Titles it covers | Evidence in profile |
|---|---|---|
| **T1 · AI / ML / GenAI** | AI Engineer, ML Engineer, GenAI Engineer, LLM Engineer, AI Specialist | LLM fine-tuning, Azure Document Intelligence pipeline, Azure ML Studio predictive models, Keras/ANN thesis |
| **T2 · Data Science / Analytics / BI** | Data Scientist, Data Analyst, BI Developer, Analytics Engineer, Power BI Developer | Power BI + DAX, SQL, pandas, inventory analytics, Dash dashboard |
| **T3 · AI Product / AI Automation** | AI Product Manager, AI Solutions Lead, Intelligent Automation Lead, Low-Code AI Lead | Led AI squad, AI use-case development + PoC delivery, Power Automate/Power Apps, Selenium RPA |
| **T4 · Supply Chain / Operations Analytics** | Supply Chain Analyst, Demand Planning Analyst, Procurement Analyst, Operations Analyst, Supply Chain Excellence | SAP Ariba tender management, ~20% cost efficiency, WIP/DOP/LSMGIT/CSMGIT inventory analytics |
| **T5 · Process / Performance Mgmt / Digital Transformation** | Performance Manager, Process Manager, Business Excellence, Digital Transformation, Process Improvement | **Current role.** Excellence & Process Management team, ~35% process-efficiency gain, KPI frameworks |

**Career score = best-matching track's score.** A posting matching *any* track
scores 90–100. Multi-track postings (e.g. "Supply Chain Data Scientist" = T2+T4)
score 100. Adjacent-but-thin (generic software engineering, pure data engineering)
scores 40–60. Genuinely unrelated scores 0–20.

### 1.2 Fix the Experience calibration — it is currently backwards

`pipeline_phase1_rank.md:28` scores `Junior/Mid = 100, Senior = 80, Lead = 60,
Intern = 40`. That grades the *posting's* seniority label, not whether Salman fits
it. A "Senior, 8+ years required" role scores 80 — near-perfect — when it is
actually a hard gap.

Add an honest experience baseline to `01-candidate-profile.md`:

- **~3.7 years continuous professional experience** (Dec 2022 → present), of which
  ~2.75 years were structured traineeships held while studying
- **~1 year post-degree full-time professional** (graduated June 2025; Junior
  Performance Manager from Sep 2025)
- **0 years formal people management** (has led a squad, has not line-managed)

Rescore Experience against *that* baseline:

| Posting asks | Score | Why |
|---|---|---|
| 0–2 years / graduate / junior | 100 | Direct fit |
| 2–4 years | 85 | Total experience covers it |
| 4–6 years | 55 | Real stretch; flag honestly |
| 6+ years / Senior / Staff / Principal | 25 | Genuine gap |
| Requires people management | 30 | Squad leadership ≠ line management |
| Internship / student | 30 | Over-qualified, backward step |

### 1.3 Normalize the Technical score

`pipeline_phase1_rank.md:27` says "count keyword matches" across 23 keywords with
no denominator, so scores are unbounded and incomparable between a 500-char snippet
and a full description. Replace with: *what fraction of the posting's stated
must-have technical requirements does the profile genuinely satisfy?* — expressed
0–100, scored from requirements rather than from raw keyword hits.

### 1.4 Unify the two divergent frameworks

`/rank` uses Technical 30 / Experience 25 / Behavioral 15 / Career 30.
`pipeline_phase1_rank.md:32` uses Technical 35 / Experience 30 / Career 35.
**Adopt the 4-dimension version everywhere** (it is the documented framework in
`04-job-evaluation.md`). Behavioral is scorable from a posting's described ways of
working; dropping it silently was an unforced divergence.

### 1.5 Other profile corrections

- `01-candidate-profile.md` is `framework_version: 1.1.1` while `CLAUDE.md` carries
  richer content. Sync it and bump the version.
- Add **target-title vocabulary** per track (drives the query matrix — one source of truth).
- Add **explicit deal-breakers and must-haves** in gate-checkable form.
- **GitHub URL — resolved 2026-08-18.** `01-candidate-profile.md:13` claimed a
  username that does not exist; the real profile is `github.com/salman-2244`
  (confirmed live, display name "MUHAMMAD SALMAN AHMED"). Corrected in the profile
  and in all 22 already-generated CVs under `cv/`.
- Record **notice period** and **salary expectation band** (currently absent;
  needed for interview prep and the optional salary dimension).

---

## Part 2 — LinkedIn-led discovery

### 2.1 Correct a documented error

`search-queries.md:14` states `--location "Remote"` is broken and only cities work.
Live probes on 2026-08-18 disprove the second half: `-l "Hungary"` returned Budapest
and Üllő roles; `-l "European Union"` returned Netherlands and Lithuania roles.
**Country- and region-level location values work** — a free coverage multiplier that
the docs currently forbid.

### 2.2 Query matrix as configuration

New file `config/search_matrix.json` replaces the three hardcoded LinkedIn blocks
in `run_daily.sh:103-110`:

```json
{
  "linkedin": {
    "enabled": true,
    "max_requests_per_run": 60,
    "delay_seconds": 4,
    "jobage_days": 14,
    "limit_per_query": 10,
    "detail_enrich_budget": 15,
    "tracks": {
      "T1_ai_ml":        { "enabled": true, "queries": ["AI Engineer", "Machine Learning Engineer", "Generative AI"] },
      "T2_data_bi":      { "enabled": true, "queries": ["Data Scientist", "Data Analyst", "Business Intelligence"] },
      "T3_ai_product":   { "enabled": true, "queries": ["AI Product Manager", "Intelligent Automation"] },
      "T4_supply_ops":   { "enabled": true, "queries": ["Supply Chain Analytics", "Demand Planning", "Operations Analyst"] },
      "T5_process_perf": { "enabled": true, "queries": ["Performance Management", "Process Improvement", "Digital Transformation"] }
    },
    "geos": ["Hungary", "Germany", "Netherlands", "Austria", "Ireland",
             "Finland", "Sweden", "Switzerland", "United Kingdom", "European Union"]
  }
}
```

Volume control is explicit: `max_requests_per_run` is a **hard stop**, not a target.
The runner selects a rotating subset of (track × geo) pairs each day so all
combinations get covered across the week without exceeding the cap on any one day.

### 2.3 Budgeted detail enrichment

Other portals give `aggregate_jobs.py` a 500-char snippet. LinkedIn's `detail`
subcommand returns the **full description** plus `seniority`, `employmentType`, and
`applyUrl`. A new Phase 1c calls `detail` for the top `detail_enrich_budget` (15)
LinkedIn cards by preliminary title match, so the ranker scores real requirements
instead of a truncated blurb. This is why LinkedIn jobs rank better — **better
evidence, not a bonus.**

### 2.4 Dedup hardening

`aggregate_jobs.py:74-84` keys on the URL with the query string stripped. LinkedIn
serves the same posting from country subdomains (`hu.`, `de.`, `nl.linkedin.com`),
so one job can enter twice under two keys. Add canonical keying: any
`*.linkedin.com/jobs/view/...-<id>` collapses to `url:linkedin:<jobId>`.

---

## Part 3 — LinkedIn job alerts as a priority signal

### 3.1 Honest framing of what the signal is

A LinkedIn job alert fires on **saved-search criteria you defined**, and some alert
emails additionally include a profile-based "recommended for you" block. It is a
real signal that LinkedIn's own matching surfaced the role for you — it is **not** a
fit score, and it must not be treated as one. Accordingly: alert-match **lowers the
document gate from 75 to 60** and breaks ties in ordering. It never adds points.

### 3.2 Why this cannot live inside the cron job

The Gmail integration is an MCP connector (`mcp__claude_ai_Gmail__*`). It is not
currently connected, and interactively-authenticated MCP servers are routinely
absent in headless/cron runs — so the 08:00 launchd job cannot rely on it.
`/gmail-sync` already forbids an IMAP fallback, and that rule is respected here.

**Decoupled design:**

```
  You (interactive, any time)          Cron (08:00, unattended)
  ───────────────────────────          ────────────────────────
  /linkedin-alerts                     run_daily.sh
      ↓ reads Gmail label                  ↓ reads the file
      ↓ parses alert emails                ↓ no MCP needed
  job_scraper/alert_matched.json ─────────→ gate + ordering
```

`alert_matched.json` is a small persistent store, keyed by the same dedup key
`aggregate_jobs.py` produces, with a 30-day expiry so a stale alert stops
privileging an old posting forever. Synthetic example:

```json
{
  "url:linkedin:4443802767": {
    "first_alerted": "2026-08-18",
    "alert_name": "T1 AI ML",
    "track": "T1_ai_ml",
    "source": "linkedin-alert"
  }
}
```

### 3.3 Step-by-step setup (what you do by hand, once)

**Stage A — create the alerts on LinkedIn (~10 minutes)**

1. Go to `linkedin.com/jobs` → search box.
2. For each of the five tracks, run one search using the track's titles, set
   **Location** to your priority geo, then set **Date posted → Past week**.
3. Toggle **"Set alert"** on the results page.
4. Set frequency to **Daily** and delivery to **Email** (plus in-app if you like).
5. Name each alert to match its track, exactly: `T1 AI ML`, `T2 Data BI`,
   `T3 AI Product`, `T4 Supply Ops`, `T5 Process Perf`.
   *The name is the join key — the parser reads it to know which track fired.*
6. Repeat per geo you care most about. Start with **Hungary, Germany, Netherlands,
   European Union**; add more once you see the volume.
7. Review all alerts at `linkedin.com/jobs/alerts` — confirm each is Daily + Email.

**Stage B — label them in Gmail (~3 minutes)**

8. Gmail → Settings → **Filters and Blocked Addresses** → *Create a new filter*.
9. **From:** `jobalerts-noreply@linkedin.com`
10. Actions: **Apply the label** `JobSearch/LinkedIn-Alerts` (create it), and tick
    **Never send it to Spam**. Do *not* tick "Skip the Inbox" until you trust it.
11. Wait for one alert to arrive and confirm the label was applied.

**Stage C — connect Gmail to Claude (~2 minutes)**

12. claude.ai → **Settings → Connectors → Gmail** → connect the account
    `cs.salman.ahmed@gmail.com`.
13. Confirm by running `/gmail-sync` — it will tell you if the tools are missing.

**Stage D — ingest (whenever you like, before or after the morning run)**

14. Run `/linkedin-alerts`. It reads only the `JobSearch/LinkedIn-Alerts` label,
    parses job cards, enriches each via the LinkedIn `detail` CLI, shows you what it
    found, and **asks for approval before writing** `alert_matched.json` — the same
    approval pattern `/gmail-sync` already uses.

### 3.4 Why alert names are the join key

Parsing "which track matched" out of email body text is brittle. The alert *name*
is authored by you, travels in the email subject/body, and is stable — so the
parser matches on it and degrades gracefully (unknown name → still recorded as
alert-matched, `track` left empty) rather than guessing.

---

## Part 4 — Match-gated document generation

Current behavior drafts up to 5 jobs at score ≥ 60. New behavior:

```
For each ranked job:
    Eligibility Gate  → FAIL (outside Europe, no remote-EU)      → drop
                      → FLAG (non-HU, sponsorship needed)        → keep, annotate
    Language Gate     → FAIL (requires a language not spoken)    → drop
    ────────────────────────────────────────────────────────────────────
    score ≥ 75                                → DRAFT documents
    score ≥ 60 AND key in alert_matched.json  → DRAFT documents
    score ≥ 60, not alert-matched             → report only, no documents
    score < 60                                → report only
    ────────────────────────────────────────────────────────────────────
    Cap at max_jobs_to_apply (5), ordered by: score desc,
      then alert-matched first, then richer evidence (LinkedIn detail) first
```

Sponsorship-FLAG jobs still qualify for documents — flagging is information for
Salman, not a veto, per `CLAUDE.md`'s "flag, not auto-reject" rule.

The daily report gains a **"Matched but not drafted"** section so a Good Fit that
missed the gate is still visible and can be drafted manually with `/apply`.

---

## Part 5 — Files touched

| File | Change | Risk |
|---|---|---|
| `01-candidate-profile.md` | Add Profile Tracks, experience baseline, target titles, notice/salary; sync with `CLAUDE.md`; bump version | None |
| `04-job-evaluation.md` | Track-based Career dimension; corrected Experience bands; normalized Technical; confirm Hungarian-permit calibration; bump version | Low |
| `prompts/pipeline_phase1_rank.md` | **Rewrite scoring** to the unified 4-dimension framework | Medium — this is the intended behavior change |
| `.claude/commands/rank.md` | Align wording to one framework | Low |
| `config/search_matrix.json` *(new)* | Query matrix + volume caps | None |
| `scripts/run_daily.sh` | Matrix-driven LinkedIn loop; glob aggregate inputs (replaces hardcoded `:139-148`); Phase 1c enrichment; new gate logic | **Highest** — two coupled edits in one script; requires dry-run |
| `scripts/aggregate_jobs.py` | Canonical LinkedIn job-ID keying; consistent query-string stripping | Low |
| `scripts/linkedin_alerts.py` *(new)* | Alert-email → portal-shaped JSON parser | Low |
| `.claude/commands/linkedin-alerts.md` *(new)* | Interactive, approval-gated ingestion command | None |
| `job_scraper/alert_matched.json` *(new)* | Alert-match store, 30-day expiry | None |
| `job_scraper/seen_jobs.json` | One-time backfill: `portal` from URL domain (38 LinkedIn recoverable); normalize the 60 query-string keys | Medium — back up first |
| `.claude/skills/job-scraper/SKILL.md` | Document the real `seen_jobs.json` schema | None |
| `search-queries.md` | Correct the cities-only error; add track queries | None |

---

## Part 6 — Error handling

- **Empty result with HTTP 200** must log loudly. Today a failed portal writes
  `{"count":0}`, indistinguishable from "no new jobs" — a silently broken parser
  could go unnoticed for weeks. Add a per-run assertion: if *every* LinkedIn track
  returns zero, fail the phase and say so in the report.
- **429 / block page is never treated as breakage** (existing Step 4.75 rule) — it
  backs off and reports; it never triggers a workaround.
- **Volume cap breach** aborts remaining requests rather than continuing.
- **Malformed alert email** is skipped with a logged warning; one bad email never
  aborts ingestion.
- **Missing `alert_matched.json`** degrades to "no alert-matched jobs" — the pipeline
  runs normally at the 75 gate.
- **Migration safety:** timestamped backup plus a before/after entry-count assertion
  on `seen_jobs.json`.

## Part 7 — Testing

1. `aggregate_jobs.py` — unit-test canonical LinkedIn keying: three country
   subdomains of one job ID collapse to one key.
2. **Ranker regression** — re-score a saved snapshot of recent jobs under old and
   new formulas; assert T4/T5 roles (bp Performance Analyst, Eaton Demand Planning)
   move from ~0 Career to ≥ 90.
3. **Matrix dry-run** — `enabled: false`, confirm request count ≤ cap and that the
   aggregate output diffs sanely against the Phase 0 baseline.
4. **Gate unit test** — synthetic jobs at 74/75/60-alerted/60-unalerted produce
   exactly the intended draft/no-draft split.
5. **Alert parser** — run against one real alert email, verify keys match the
   dedup keys `aggregate_jobs.py` produces for the same jobs.
6. **Full supervised run** before re-arming launchd.

## Part 8 — Build order

- **Phase 0** — Back up `seen_jobs.json`; capture aggregate baseline; note next launchd fire time.
- **Phase 1** — Profile calibration (Part 1). Ranker regression test. *Highest value, zero LinkedIn risk.*
- **Phase 2** — Query matrix + `run_daily.sh` refactor + dedup hardening. Dry-run.
- **Phase 3** — `seen_jobs.json` migration.
- **Phase 4** — Detail enrichment.
- **Phase 5** — New document gate + report section.
- **Phase 6** — Alert ingestion (needs your Stage A–C setup first).
- **Phase 7** — Re-arm launchd, observe three mornings, tune caps and geos.

Phases 1–5 are useful with or without Phase 6, so Gmail connector delays never block progress.

## Risks accepted

LinkedIn User Agreement §8.2 prohibits scraping (#2), bots (#13), and unreasonable
load (#16). The existing public `jobs-guest` access already sits against #2/#13; this
design **raises volume**, which is the real change in exposure. Mitigations are
explicit and enforced in config: hard per-run cap, sequential requests with delays,
honest non-spoofed User-Agent, bounded enrichment, and no bypass of any protection.
Nothing here evades a CAPTCHA, an auth wall, or a rate limit — 429 is honored, never
circumvented. Phase 6 (alert emails) is the one channel with no ToS tension at all,
which is the argument for reaching it.

**Permanent limitations:** no saved jobs, no "recommended for you", no applicant
counts, no Easy Apply state, 10 results per page.

## Out of scope but flagged

`.claude/settings.json` is tracked by git and contains a plaintext
`ANTHROPIC_AUTH_TOKEN`; `config/automation.json` holds a plaintext Gmail app
password (gitignored). Neither is touched by this work. Both should be handled
separately — the token is not yet in committed history, so removing it before the
next commit avoids a history rewrite.
