---
framework_version: 1.3.0
---

# Job Evaluation Framework

<!-- SETUP: Skill match areas and career goals are personalized by running /setup -->

## Eligibility Gate — run before scoring

If the candidate is not a citizen or permanent resident of the country they are applying in, run this first. It is a hard filter, not a scoring dimension, and it is separate from work-permit *timing*: timing asks "can they work the required hours yet?", eligibility asks "are they permitted to hold this job at all?". A candidate can pass timing and still be categorically excluded.

Read the posting's eligibility / work rights / "who can apply" section **verbatim** and classify:

| Posting wording | Verdict |
|-----------------|---------|
| Names a **citizenship or permanent-residency requirement** ("must be a citizen of X", "permanent resident", "PR required", "full working rights" where the employer means citizen/PR) | **FAIL — hard stop.** Do not score, do not draft. Quote the exact wording back to the user. |
| Requires a **security clearance** at any level | **FAIL** in most countries, since clearance is normally gated on citizenship. Verify the specific scheme rather than assuming. |
| **Explicitly names** the candidate's permit class, or says "international applicants welcome", "visa holders considered", "we sponsor" | **PASS** — verified acceptance. Worth noting as a positive in the application. |
| **Silent** on citizenship or residency | **PROCEED, but mark unverified.** Check the employer's own careers or international-applicant page before drafting. |

**Two rules that are easy to get wrong:**

1. **Silence is not permission.** Large graduate programs frequently gate eligibility on their own website rather than in the job ad. Highest-risk categories: professional-services firms, government and defence, banking, telecommunications, and anything touching critical infrastructure.
2. **A company-wide "we accept international applicants" statement is not role-level permission.** The common pattern is a general welcome followed by a *named list* of the specific programs or service lines it covers. Confirm the **specific posting or stream** appears on that list before drafting.

**Report an eligibility failure to the user with the quoted source** rather than silently dropping the role. They may know something about their own status that the profile does not record.

If the candidate's permit also constrains *hours* or *start date* (a student visa with a term-time cap, a permit that begins on graduation), record that as a second gate under this section during `/setup`, with the specific dates. Do not merge it with the eligibility question above — they fail for different reasons and need different answers.

A role that fails this gate is not scored and not drafted. Everything below applies only to roles that pass it.

### Candidate-specific calibration (Europe-wide search)

Salman is a **non-EU national based in Budapest on a Hungarian residence/work permit** — confirmed by the candidate on 2026-08-18. He does **not** hold EU-wide work rights. Apply the gate as follows across the Europe-wide search:

- **Hungary-based roles:** eligibility is normally fine (he already lives and works there). PASS unless the posting names a citizenship/clearance requirement.
- **Other EU/EEA countries (DE, AT, FI, SE, NL, IE, etc.):** he would generally need **visa sponsorship / a new work/residence permit**. Treat "silent on sponsorship" as **PROCEED-but-unverified** and check the employer's page; treat "no visa sponsorship / must already have the right to work here" as a **FLAG to the user** (near-fail) rather than a silent pass. Prefer and positively note postings that say they sponsor or that international/EU applicants are welcome.
- **UK / Switzerland (non-EU):** separate work-authorization regimes; sponsorship/permit almost always required. Same FLAG-if-excluded treatment.
- **Fully-remote roles:** check where the employer requires the worker to be resident/eligible; "remote (EU)" or "remote (worldwide)" is a strong positive, "remote (must reside in <country X>)" re-triggers the permit question for that country.

This calibration is a convenience, not a substitute for reading the posting: still quote the exact eligibility wording back to the user, and let Salman correct his own status.

## Language Gate — run before scoring

Read the posting's language requirements as stated for **the role itself**. A job is never rejected because the employer is based in Germany or Spain — the question is what language the work requires. Two separate things can fail this gate, and they fail for different reasons:

1. **A stated job condition** — "fluent German required", "must communicate with the Madrid team in Spanish".
2. **The posting's own language** — a body written in German or Dutch throughout is itself evidence the role is worked in that language, but only when there is enough text for that to mean something.

`scripts/hard_gates.py` implements both deterministically at Phase 1b, before any model sees the job. This section is the human-readable spec for that code and for `/rank`, `/apply` and `/interview`, which apply the same rules by judgment.

### Verdicts

| Posting requirement vs. your Languages table | Verdict |
|---|---|
| Requires, as a hard job condition, a language you **cannot work in professionally** — either absent from your table entirely ("fluent Polish required") or present below a working level (**Hungarian at A2 is not a working level**) | **FAIL — hard stop.** Do not score, do not draft. Quote the exact requirement line. |
| The posting **body is written** in one of those languages, and the body is long enough to judge (see thresholds below) | **FAIL.** Quote the opening of the body as evidence. |
| Names one of those languages but marks it **optional** — "advantage", "nice to have", "preferred", "ideally", "a plus", "an asset", "welcome", "beneficial" | **PASS.** The optional reading always wins, even when the same sentence also contains "fluent". |
| Requires a language you **do** work in professionally, at a bar plausibly above your declared level (e.g. "native-level English" against professional working proficiency) | **FLAG, then proceed.** Score and draft normally, but quote both the posting's bar and your declared level so the user can judge. Bars like "fluent" vary by company and geography. Never silently drop it and never silently treat it as a clean pass. |
| Requires a language you work in, at or below your declared level, or names it with no level at all | **PASS.** No note needed. |
| **No description text to read a requirement from** | **UNKNOWN — not a pass and not a failure.** It means "ask for the text". This is the normal state of an unenriched LinkedIn card, and it is the signal that directs Phase 1c's enrichment budget. |

**`UNKNOWN` is a first-class verdict.** Treating no-evidence as PASS is what drafted DHL, whose "Fluent English and Hungarian" line was present in the 2026-08-18 fetch and absent from the 2026-08-19 corpus. Treating it as FAIL would drop most of a LinkedIn corpus unread.

### How the rules are applied

- **Sentence-scoped, never posting-wide.** A language condition and its qualifier live in the same sentence or bullet. Scanning the whole posting at once lets one bullet's "advantage" excuse another bullet's "fluent German required".
- **Optional markers are checked first and they win.** "Hungarian knowledge is an advantage" and "Hungarian (preferred)" are both passes.
- **Required markers**, for reference: "required", "mandatory", "must have", "must speak", "fluent", "fluency", "native", "proficient", "business level", "C1", "C2", "B2", "verhandlungssicher", "essential", "is a must", "necessary", "obligatory".
- **Posting-language detection is deliberately hard to trigger:** at least 400 characters of body, at least 8 stopword hits from one language, at least 6% of the text, *and* clearing English by a 1.5× margin. Below that it returns UNKNOWN with the counts attached rather than guessing — a thin SEO snippet must not bypass the filter, but it must not fabricate a verdict either. The English baseline exists because several stopword lists collide with ordinary English words ("die", "van", "per", "sie", "la").

### Why the FLAG rule was narrowed

An earlier version of this gate answered **FLAG, then proceed — not a fail** for *any* language on your table whose stated bar looked higher than your declared level. Hungarian sits on the table at A2, so "Fluent English and Hungarian" read as a flag rather than a discard, and DHL was drafted on 2026-08-19. A language you cannot work in professionally is a discard when the posting makes it a hard condition, regardless of whether it appears on your table. FLAG now covers only its genuine case: a language you *do* work in, where the bar is a stretch rather than a barrier.

**Report a language failure to the user with the quoted source** rather than silently dropping the role, the same as the Eligibility Gate above.

**Worked examples, against Salman's table** (English professional working, Urdu native, Punjabi native, Hungarian A2):

- "Fluent English and Hungarian" → **FAIL.** Hungarian is a hard condition at a level above A2.
- "Hungarian is an advantage" → **PASS.** Optional marker wins.
- "Verhandlungssicheres Deutsch erforderlich" → **FAIL.** German is not on the working table at all.
- A 4 000-character posting written entirely in Dutch → **FAIL** on posting language, even with no explicit requirement line.
- A 300-character Italian snippet → **UNKNOWN.** Too thin to judge; enrich it and re-read.
- "Native-level English" → **FLAG.** English is a working language, but the bar plausibly exceeds professional working proficiency — surface it and let Salman decide.
- A Munich-based role advertised in English requiring only English → **PASS.** The employer's country is not a language requirement.

## Seniority Gate — run before scoring

**A job title carrying any of these as a standalone word (case-insensitive) is a FAIL:** `Senior`, `Sr`, `Snr`, `Lead`, `Leader`, `Principal`, `Head`, `Director`, `Expert`. Not a flag, not a low score on the Experience dimension — a discard, decided before anything is scored. `scripts/hard_gates.py::seniority_verdict` implements it at Phase 1b; `/rank`, `/apply` and `/interview` apply the same rule.

Read it from the **title only**. A grade word in the body — "you will report to a Senior Manager", "our senior leadership team" — describes somebody else, and gating on it would discard postings for the level above the role being advertised.

This is deliberately *separate from* the years-of-experience check, and neither subsumes the other:

- "Senior Data Analyst" with no years figure anywhere in the body → the years gate reads nothing and cannot act. This gate discards it.
- "Business Analyst" requiring "4-6+ years of overall experience" → no grade word in the title, so this gate passes it. The experience gate discards it.

On the 2026-08-22 corpus 99 of 590 titles carried a senior-family marker and 127 carried one from the full list, most stating no years at all — which is why the lexical filter exists rather than being folded into the numeric one.

**"Lead" is matched as a word, not as a compound.** "Team Lead", "Program Lead", "Project Lead", "Workstream Lead", "Process Lead", "Country Lead", "Transformation Lead" and "(Lead) Project Manager" all fail on the same rule, so the list never needs extending when a new compound appears — the 2026-08-22 corpus alone carried seven distinct shapes.

The match is bounded to word and separator boundaries, which is what keeps these **passing**: "SRE Manager", "Sri Lanka Operations Analyst", "Serious Games Designer", "Leadership Development Program", "Overhead Cost Analyst", "Headcount Planning Analyst", "Expertise Centre Data Analyst". A dual-grade title ("Junior/Senior Analyst", "Manager/ Sr. Manager - Data Product Manager") fails: it contains the word.

**One documented exception.** "Lead" followed by `time`, `to`, `generation`, `gen`, `management`, `qualification`, `nurturing`, `scoring` or `conversion` is the noun, not the grade — "Lead Time Reduction Analyst" (supply chain, T4) and "Lead-to-Cash Process Manager" (T5) are target-track roles, not senior postings. No such title appeared in the 2026-08-22 corpus; the exception is a guard.

**Report a seniority failure with the title quoted**, the same as the two gates above.

> **Note on the Experience Match table below.** Its "6+ years / Senior / Staff / Principal / Head of → 25" row still stands, and the remaining overlap is intentional. This gate catches the *title*; that row catches a posting stating senior-level years without a grade word in its title, and it still prices "Staff", which this gate does not read (the one Staff title in the 2026-08-22 corpus, "Expert / Staff Data Scientist", was caught on "Expert"). A job reaching the scoring dimensions with any of the nine markers in its title means the gate was bypassed — say so rather than scoring it 25.

## Scoring Dimensions

Evaluate each job posting against these five dimensions:

### 1. Technical Skills Match (0-100)
How well do the required/preferred skills align with the candidate's capabilities?

**Score as a proportion, not a keyword count.** Ask: *of the technical requirements this
posting actually states as must-haves, what fraction does the profile genuinely satisfy?*
Counting keyword hits without a denominator makes scores unbounded and incomparable between
a 500-character snippet and a full job description — a long posting would beat a good one.
Weight must-haves above nice-to-haves, and ignore keywords that appear only in boilerplate.

| Score | Meaning |
|-------|---------|
| 80-100 | Core requirements are primary skills |
| 60-79 | Most requirements match, 1-2 gaps that are learnable |
| 40-59 | Partial match, significant upskilling needed |
| 0-39 | Fundamental mismatch |

**Strong match areas:** Python (pandas/Keras), SQL, data science & ML, Generative AI / LLM fine-tuning, data analytics, Power BI/DAX, process automation (Power Automate, Selenium), Azure ML Studio
**Moderate match areas:** AI product management, MLOps, Power Apps (low-code), supply chain & procurement analytics, operations analytics, business intelligence, financial modeling
**Weak match areas:** deep research / academic ML publishing, large-scale distributed systems / data engineering at scale, formal multi-year people management, business-level Hungarian-only roles

### 2. Experience Match (0-100)
Does work history align with what they're looking for?

**Score against the candidate's real Experience Baseline in `01-candidate-profile.md`, never
against the posting's own seniority label.** A posting labelled "Senior — 8+ years required"
is a genuine gap for this candidate, not a near-perfect match; grading the label instead of
the fit inverts this dimension.

| Posting's stated requirement | Score | Reasoning |
|------------------------------|-------|-----------|
| 0-2 years / graduate / junior / early-career | 100 | Direct fit |
| 2-4 years | 85 | ~3.7 years total professional experience covers this |
| 4-6 years | 55 | Real stretch — apply if domain fit is strong, and be honest about it |
| 6+ years / Senior / Staff / Principal / Head of | 25 | Genuine gap in years, regardless of domain fit |
| Requires formal people / line management | 30 | Squad leadership and portfolio ownership ≠ line management |
| Internship / student / working-student | 30 | Over-qualified; a backward step post-graduation |

Adjust *within* a band for domain fit: same-domain experience (telecom, aviation, supply
chain, process/performance management) pushes to the top of the band; an unrelated domain
pushes to the bottom. Never let domain fit move a score across bands — years are years.

**Strong:** AI/ML use-case development and delivery in a business/enterprise setting (telecom); supply chain & procurement analytics; process automation & performance/KPI analytics; Power BI dashboarding
**Moderate:** financial controlling & modeling; operations analytics; AI product/strategy ownership (squad-level); low-code AI enablement
**Entry-level:** formal people/line management; AI Product Management as a titled role (has adjacent, squad-leadership experience to make the case)

### 3. Behavioral/Culture Fit (0-100)
Does the role and company culture match the behavioral profile?

| Score | Meaning |
|-------|---------|
| 80-100 | Culture strongly matches behavioral preferences |
| 60-79 | Mixed signals but mostly compatible |
| 40-59 | Some friction areas |
| 0-39 | Significant culture mismatch |

**Red flags to research:** Department disorganization, work dominated by maintenance over development, poor chemistry with leadership, culture mismatches. Check reviews, media coverage, LinkedIn connections, and network contacts for insider perspective.

### 4. Location & Logistics (Pass/Fail + Notes)
- Budapest / Hungary-based: PASS
- Remote (EU / worldwide, or remote with occasional office in a target country): PASS
- Relocation to a target country (DE, AT, FI, SE, NL, IE, CH, UK) for the right role: PASS (note relocation support + the eligibility/sponsorship question from the gate above)
- Relocation outside the target-country list, or on-site-only far from Budapest with no relocation support and no remote option: FAIL (deal-breaker)
- Frequent international travel: FLAG (discuss with user)

### 5. Career Alignment & Motivation (0-100)
Does this role advance career goals and contain tasks that energize?

**Score against the five Profile Tracks in `01-candidate-profile.md` — T1 AI/ML, T2 Data
Science/Analytics/BI, T3 AI Product/Automation, T4 Supply Chain/Operations Analytics, T5
Process/Performance Management/Digital Transformation.** All five are valid targets. Do not
treat T1 as the only real match: T5 is the candidate's *current* job title, and T4 is where
several years of hands-on experience sit.

| Score | Meaning |
|-------|---------|
| 100 | Spans two or more tracks (e.g. "Supply Chain Data Scientist" = T2+T4; "AI Process Automation Lead" = T3+T5) |
| 90-99 | Squarely inside one track |
| 60-89 | Inside a track but with a caveat — narrower scope than wanted, thin AI/data content, or a sideways move |
| 40-59 | Adjacent but thin: generic software engineering, pure large-scale data engineering, pure DevOps |
| 0-20 | Genuinely unrelated: sales, HR, non-analytical finance, hardware |

Name the matched track(s) explicitly in the evaluation notes, so a low Career score is always
auditable against a stated reason rather than an unexplained number.

**Career goals:**
- Move into an AI-focused leadership / strategy role (AI Lead, AI Strategy Lead, Low-Code AI Lead, or a role shaping how an organization adopts and scales AI)
- Keep working at the intersection of technical AI/ML depth and business impact, owning use cases end-to-end
- Grow into AI Product Management or a team-lead track while staying hands-on with data science / ML

**Direction preference, not a veto:** T1 and T3 sit closest to the stated goal of AI
leadership, so between two otherwise equal postings prefer those. But a strong T4 or T5 role
that deepens domain expertise and pays for relocation is a legitimate career step, not a
detour — score it on its merits.

**Motivation filter:** Evaluate not just whether you *can* do the tasks, but whether the tasks will *energize* you. Consider:
- Tasks that energize: shipping AI/ML use cases with measurable business impact, cross-functional leadership, LLM/GenAI work, KPI/optimization frameworks, automating manual processes, the mathematics behind deep learning
- Tasks that drain: isolated pure-research with no product/business exposure, maintenance-heavy keep-the-lights-on work, rigid low-autonomy execution roles
- Non-task factors: leadership style, department culture, company values, degree of autonomy, whether the role is genuinely AI/data-centric

**Life situation alignment:** Consider personal constraints:
- **Security**: currently employed at Nokia (Budapest); moving for the right role, not out of necessity - can be selective
- **Flexibility**: open to relocation across target EU countries + UK/Switzerland, and to remote-EU; based in Budapest today
- **Professional development**: prioritizes roles that build toward AI leadership/strategy and keep him hands-on with modern AI/ML

### 6. Salary Benchmark (Optional)

If the salary lookup tool is configured (`salary_data.json` exists), look up the company:
```
python salary_lookup.py "<Company Name>" --json
```

If a city is known from the posting, add `--city "<City>"` to narrow results.

Present findings as:
```
### Salary Benchmark
| Metric | Value |
|--------|-------|
| [Category] index | XX.X (+/-X.X% vs baseline) |
| Overall index | XX.X (+/-X.X% vs baseline) |
```

Interpret results relative to the baseline defined in the data file's metadata. For index-based data, higher typically means above-market compensation.

If the salary tool is not configured, skip this section.

## Output Format

Present the evaluation as:

```
## Job Fit Evaluation: [Role] at [Company]

| Dimension | Score | Notes |
|-----------|-------|-------|
| Technical Skills | XX/100 | [brief note] |
| Experience Match | XX/100 | [posting asks N years; band applied] |
| Behavioral Fit | XX/100 | [brief note] |
| Location | PASS/FAIL | [brief note] |
| Career Alignment | XX/100 | [matched track(s), e.g. "T4+T2 — Supply Chain Analytics + BI"] |

**Overall Score: XX/100** (weighted average of scored dimensions)

### Verdict: [Strong Fit / Good Fit / Moderate Fit / Weak Fit / Poor Fit]

### Key Strengths for This Role
- [bullet points]

### Gaps to Address
- [bullet points]

### Recommendation
[1-2 sentences: apply/skip/apply with caveats]

### Company Research Checklist
- [ ] Checked company website (mission, values, recent news)
- [ ] Checked review sites (Glassdoor, Jobindex, etc.)
- [ ] Checked LinkedIn for team size, recent hires, connections
- [ ] Checked media for restructuring, growth, or workplace issues
- [ ] Identified network contacts who may know the team/manager
```

## Weighting
- Technical Skills: 30%
- Experience Match: 25%
- Behavioral Fit: 15%
- Career Alignment: 30%

(Location is pass/fail, not weighted)

## Thresholds
- **Strong Fit** (75+): Definitely apply, tailor everything
- **Good Fit** (60-74): Apply, address gaps in cover letter
- **Moderate Fit** (45-59): Consider carefully, discuss with user
- **Weak Fit** (30-44): Probably skip unless strategic reasons
- **Poor Fit** (<30): Skip

## Pre-Application: Call the Employer (Best Practice)

Before writing the application, consider whether the candidate should call the contact person listed in the posting. **Only call if there are substantive questions** - never call just to "be remembered."

### When to Suggest Calling
- The posting has unclear or ambiguous requirements
- It's unclear which competencies are essential vs. nice-to-have
- The role description is vague about day-to-day tasks
- There's a named contact person who invites questions

### Good Questions to Ask
- "What are the primary challenges in this role?"
- "How is time typically divided across the listed responsibilities?"
- "Which competencies are most critical for success in this position?"
- "What does success look like in the first 6-12 months?"

### Rules for the Call
- Prepare a 30-second "elevator pitch" about your background in case they ask
- The call's purpose is **gathering information**, not delivering a pitch
- Take notes - use what you learn to tailor the application
- Reference the conversation naturally in the cover letter ("After speaking with [name], I was especially drawn to...")
