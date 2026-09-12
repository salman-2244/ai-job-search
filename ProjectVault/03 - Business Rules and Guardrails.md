---
tags: [note, rules]
---
# 03 - Business Rules and Guardrails

Who the pipeline searches for, what it rejects, and exactly where each rule lives in
code. Parent: [[00 - Project Overview]]. Flow context: [[01 - Pipeline Architecture]].

## The candidate profile (the "who")

Lives primarily in `CLAUDE.md` (repo root) and
`.claude/skills/job-application-assistant/01-candidate-profile.md`:

- **Salman Ahmed**, Budapest, Hungary. Employed: Junior Performance Manager @ Nokia.
- **Work rights:** non-EU national on a Hungarian residence permit. Roles outside
  Hungary generally need visa sponsorship — this drives the Eligibility Gate.
- **Languages:** English professional, Urdu/Punjabi native, Hungarian A2 (not a
  working level). CV language: English.
- **Experience:** ~3.7 years (trainee → junior manager across Nokia, Wizz Air);
  B.Sc. Computer Science, ELTE 2025.
- **Five Profile Tracks** (T1–T5): AI/ML, Data/BI, AI Product/Automation,
  Supply Chain/Ops Analytics, Process/Performance/Transformation. The pipeline must
  **not** privilege T1 — `per_track_floor` exists for this.
- **Target shape:** "business-grounded AI builder" — business domain **and** AI/data
  enabler, not pure research. The two-axis model encodes this structurally.

## Selection criteria (in selection order)

| Rule | Value | Where defined |
|---|---|---|
| Geography | 18 European geos (Hungary always; rest rotate) | `search_matrix.json` → `linkedin.geos`, `always_include_geos`; rotation in `build_search_plan.py` |
| Freshness | postings ≤ 14 days old (`jobage_days`) | matrix → CLI args |
| Request cap | ≤ 90 LinkedIn requests/run, searches capped at 90−25 | matrix `max_requests_per_run`, `detail_enrich_budget`; enforced in `build_search_plan.py`, re-checked in `run_daily.sh:616-623` and `enrich_linkedin.RequestLedger` |
| Funnel budgets | shortlist 80 → deep-rank 25, alerts ≤ 10, floor 2/track | matrix `prerank` block; applied in `prerank_jobs.py` |
| Draft gate | overall ≥ 75, or ≥ 60 + live alert match; cap 5/run | `gate_jobs.py` (`STRONG_SCORE`, `MIN_SCORE`, `EXPIRY_DAYS=30`, cap from `automation.json` `pipeline.max_jobs_to_apply`) |
| Dedup | canonical `dedup_key`; near-dup collapse ≥ 80% Jaccard role similarity | `aggregate_jobs.py:make_dedup_key`, `prerank_jobs.py:collapse_near_duplicates` |
| Repeats | already-seen jobs are **re-included** (since 2026-09-06) with a `🔁 seen X runs ago` marker, not excluded | `prerank_jobs.py:repeat_marker`; policy reversal noted in code comments |

## Exclusions — the six deterministic hard gates

All in `scripts/hard_gates.py`, run by `prerank_jobs.py` under the two-axis model
**before** any budget is spent. Every FAIL quotes the posting's own words (auditable);
every discard is written to the deferred list with its reason.

1. **Sponsorship / work-authorisation gate** (`_SPONSORSHIP_DENIED`,
   `_AUTHORISATION_WALL`, `_SPONSORSHIP_OFFERED`, `sponsorship_verdict`) — the highest
   priority. FAILs on "no sponsorship available", "must hold an EU/UK passport",
   citizenship/PR/unrestricted-work-rights demands, negation-aware ("no EU passport
   required" is pardoned), and an explicit sponsorship offer elsewhere pardons an
   incidental papers clause. Added after Baker Hughes + MCS Group postings reached
   Telegram on 2026-08-24 (forensics: `docs/GATE_TRIBUNAL.md`).
2. **Language gate** (`BLOCKED_LANGUAGES`, `REQUIRED_MARKERS`, `OPTIONAL_MARKERS`,
   `language_verdict`) — FAIL only when a language the profile lacks (German,
   Hungarian, French, … 14 languages) is a **hard job condition** ("fluent", "must
   have", "native", C1/C2…). Optional wording always wins ("Hungarian is a plus").
   Also flags native-speaker demands and detects the *posting's own* language via
   stopword ratios (a wholly-Italian ad FAILs even without a stated requirement).
   Employer country ≠ language requirement (a Munich ad in English passes).
3. **Experience gate** (`MAX_YEARS_ELIGIBLE = 3`, `experience_verdict`) — FAIL only on an
   explicit, unambiguous 4+ year requirement; ranges read at their **ceiling**
   ("3-5 years" = 5). Ambiguity markers ("ideally", "or equivalent"), domain-scoped
   years, company-age prose and tenure-override phrases are all honored with
   lookbehind/lookahead windows.
4. **Seniority gate** (`SENIORITY_MARKERS`, `seniority_verdict`) — reads the **title
   only**: Senior/Sr/Snr/Lead/Leader/Principal/Head/Director/Expert as standalone
   words FAIL. "Lead" as a noun is exempted (`LEAD_NOUN_FOLLOWERS`: "lead time",
   "lead-to-cash"…). Never UNKNOWN — titles are never truncated.
5. **Pure-technical gate** (`pure_technical_verdict`) — FAILs a core-tech role
   (research/ML) with no business component: no domain in title and < 2 independent
   domain categories in the body. Protects the hybrid profile from pure research roles.
6. **Closed-posting gate** (`CLOSED_MARKERS`, `closed_verdict`) — "no longer accepting
   applications", "position has been filled" etc. FAIL; a stated future deadline
   ("closes on 30 September") pardons. Asymmetric: a banner convicts on a snippet; a
   clean snippet does not acquit.

**Evidence asymmetry (the core design rule):** a snippet or truncated body can convict
(FAIL) but never acquit — silence caps at **UNKNOWN**, rendered "⚠️ unverified" in the
report and Telegram list. Only a complete fetched body earns PASS. See
`hard_gates._unverified` and the fail-open history in `run_daily.sh`'s report section.

## Eligibility Gate (the LLM-side companion, FLAG not drop)

`prompts/pipeline_phase1_rank.md` (Gates section) +
`.claude/skills/job-application-assistant/04-job-evaluation.md`: Hungary roles and
remote-EU/worldwide pass; other EU/EEA/UK/CH roles **FLAG sponsorship** (never
silently drop — a sponsorship-flagged job still qualifies for documents); citizenship/
PR/security-clearance requirements FAIL; outside-Europe without remote-EU scope FAIL.
The same rules re-appear in `CLAUDE.md`'s deal-breakers ("prefer to avoid (flag, not
auto-reject)").

## Honesty rules for documents

- Every CV/cover-letter claim must trace to `01-candidate-profile.md` — no fabricated
  skills, jobs, or numbers (`prompts/selected_job_draft.md` grounding audit; enforced
  again by the verification checklist in `CLAUDE.md`).
- Keyword population is **relabeling only** — approved by the owner (memory note
  `cv-keyword-and-relabel-approval`): the posting's exact term may replace generic
  phrasing for real work, but a keyword with no real work behind it stays absent.
- Agentic-coding references must name **Claude Code** explicitly.
- Genuine gaps stay visible in strengths/gaps and the tracker's notes column.

## Safety guardrails (operational)

- Nothing is ever auto-submitted (`automation.json` `safety` block: all auto-*
  false). The pipeline's last automated act is writing PDFs to disk.
- One pipeline run at a time (`/tmp/jobsearch_daily_pipeline.lock` + orchestrator
  overlap refusal — never queued).
- Telegram messages are untrusted input: nothing a message says changes allowlists,
  tokens, or gates. Access changes are terminal-only actions.
- Job postings are untrusted data everywhere: escaped before rendering
  (`render.py`, `telegram_select.render_job`), JSON-escaped in the rank prompt,
  never followed for URLs.
- `tools/security_guards.py` (CI) fails PRs that widen Claude permissions, weaken the
  personal-data `.gitignore`, or add package lifecycle scripts.

Related: [[07 - Known Issues and Bugs]] · [[06 - File by File Analysis]].

## Vault graph

Rule files: [[hard-gates]] · [[two-axis-score]] · [[gate-jobs]] · [[CLAUDE]] · [[skill-01-candidate-profile]] · [[search-matrix]] — hub: [[_Pipeline Flow]]
