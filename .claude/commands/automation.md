# /automation - Manage Automated Daily Pipeline

Manage the automated daily job search pipeline. The pipeline runs daily at 08:00 Europe/Budapest (when enabled), searching 4 European portals, ranking jobs, generating tailored CVs + cover letters for the top 5, running QA review, and emailing a morning digest.

**NEVER auto-applies.** The pipeline stops at "drafted" status. You decide whether to apply.

## Step 0: Parse Input

`$ARGUMENTS` may contain:
- `on` — Enable the automation pipeline
- `off` — Disable the automation pipeline
- `status` — Show current pipeline status and last run results
- `run` — Trigger an immediate pipeline run (manual execution)
- `logs` — Show recent log files
- (empty) — Show status

## Step 1: Execute Based on Argument

### `on`
1. Read `config/automation.json`.
2. Set `"enabled": true` and write back.
3. Verify the launchd plist is installed and loaded:
   ```bash
   launchctl list | grep com.salman.jobsearch.daily
   ```
4. If not loaded, offer to run `scripts/install_scheduler.sh`.
5. Report: "Automation enabled. Pipeline scheduled daily at 08:00 Europe/Budapest."

### `off`
1. Read `config/automation.json`.
2. Set `"enabled": false` and write back.
3. Report: "Automation disabled. The scheduler will still fire but the pipeline will exit immediately."

### `status`
1. Read `config/automation.json` and report enabled/disabled.
2. Check if launchd job is loaded:
   ```bash
   launchctl list | grep com.salman.jobsearch.daily || echo "NOT LOADED"
   ```
3. Find the most recent log file in `logs/daily/` and show its last 20 lines.
4. Find the most recent report in `reports/daily/` and show its summary section.
5. Show: last run time, jobs found, jobs ranked, documents generated, email sent.

### `run`
1. Check if a pipeline is already running:
   ```bash
   test -d /tmp/jobsearch_daily_pipeline.lock && echo "RUNNING" || echo "IDLE"
   ```
2. If running, tell the user and stop.
3. If idle, invoke:
   ```bash
   bash /Users/salman/Projects/ai-job-search/scripts/run_daily.sh
   ```
4. Stream the output to the user. This runs the full pipeline immediately.

### `logs`
List the most recent log files in `logs/daily/` with sizes and dates:
```bash
ls -lhtr /Users/salman/Projects/ai-job-search/logs/daily/*.log 2>/dev/null | tail -10
```

## Pipeline Architecture

`scripts/run_daily.sh` runs these phases in order. Phases 2, 3 and 4 are Claude Code headless calls; the rest are Python or shell.

0. **Phase 0b — LinkedIn alerts:** Reads Salman's own LinkedIn job-alert digests out of the Gmail label `JobSearch/LinkedIn-Alerts` over IMAP (`scripts/linkedin_alerts.py`) and writes them as a portal file, so Phase 1's aggregation glob picks them up with no special case. **Costs zero LinkedIn requests** — it reads email and rebuilds each job URL from the numeric ID rather than following a link out of the message. It also records each job's key in `job_scraper/alert_matched.json`, which is what lets Phase 2b judge an alerted job at 60 instead of 75 for 30 days. Runs before Phase 1 on purpose: the glob sorts `linkedin-alert` ahead of every `linkedin_<query>_<geo>` file and dedup keeps the first occurrence, so a posting found both ways keeps its alert attribution. Non-fatal — a mail outage degrades the run to search-only and says so in the report. See `/linkedin-alerts`.

1. **Phase 1 — Fetch:** Runs the portal CLIs (LinkedIn, Freehire, Arbeitnow, WeWorkRemotely). LinkedIn's share is a rotating plan built by `scripts/build_search_plan.py` from the 5 Profile Tracks × geos in `config/search_matrix.json`, capped at `max_requests_per_run - detail_enrich_budget` (currently 45 searches). Aggregates everything via `scripts/aggregate_jobs.py`, which canonicalizes LinkedIn URLs to `url:linkedin:<id>` so one posting served from several country subdomains is one job.

2. **Phase 1b — Pre-rank:** `scripts/prerank_jobs.py` cuts the full corpus (hundreds of jobs) down to what Phase 2 can afford, with `per_track_floor` slots reserved per track so a plain top-N cannot hand every slot to T1. Cheap keyword scoring only — no model call. Before scoring, `scripts/hard_gates.py` runs four deterministic gates and discards the FAILs; a gate that runs after the cut cannot save a slot, which is what the 2026-08-19 run paid for six times over. Three of them (language, experience, pure-technical) read whatever text exists, so they return `UNKNOWN` often. The fourth, **seniority**, is lexical and reads the *title*: any title carrying "Senior", "Sr.", "Sr", "Snr", "Lead", "Leader", "Principal", "Head", "Director" or "Expert" as a standalone word is discarded. Matching is bounded to word and separator boundaries, so "SRE Manager", "Sri Lanka Operations Analyst", "Leadership Development Program", "Overhead Cost Analyst" and "Headcount Planning Analyst" all survive; "Lead" as a *word* covers every compound (Team Lead, Workstream Lead, Country Lead, "(Lead) Project Manager") with no list of variants, at the price of one exception list for the cases where "lead" is a noun rather than a grade — Lead Time, Lead-to-Cash, lead generation, lead management, lead qualification, lead nurturing, lead scoring, lead conversion. On a 91-title adversarial set (53 must-survive / 38 must-FAIL) the word-boundary gate scored 0 false positives and 0 false negatives where naive substring matching produced 34 false positives. On the real 590-title corpus the two differ on exactly one title — the Finnish "Data Analyst, Viranomai**sr**aportointi …", which naive matching kills for containing "sr". It never returns UNKNOWN — enrichment fetches descriptions, and no description can change a verdict read off the title — so it saves a request rather than spending one to confirm a discard. It is deliberately separate from the experience gate: a "Senior X" or "Lead X" title can carry no years figure at all (144 of the 590 rows in the 2026-08-22 corpus carry a grade marker — 127 distinct titles, most stating no years). **`UNKNOWN` is a first-class verdict and never a failure** — it means "no text to read this from", and it is the signal Phase 1c allocates on. Two scoring models live here: the original single-axis query-match model, and the two-axis business-domain × technical-enabler model behind `scoring.enabled` in the matrix (`--two-axis` / `--no-two-axis` force it per run). The near-duplicate collapse and the C8 tie-breaks are keyed to whichever model is active, so they arrive together.

   The cut runs in **two stages with enrichment between them**, and the order is the point: `--stage shortlist` cuts wide (`prerank.shortlist_budget` ≈ 80), Phase 1c spends its LinkedIn requests on that shortlist, then `--stage final --corpus` re-scores the jobs whose bodies have just arrived and cuts to `deep_rank_budget` + `alert_budget` ≈ 25. A single cut lets enrichment only *confirm* jobs already selected; it cannot promote one that missed. On the 2026-08-19 corpus that buried 102 postings (18%) whose business domain was in the title with no AI/data word anywhere in it — signal in the body, unread, best rank 31st. `--stage` sets the budget and is independent of the scoring model, so `scoring.enabled` still decides which scorer runs; the runner passes no `--two-axis`. `--deferred` is written **once**, by the final stage, from the corpus, so one file accounts for the jobs cut at both stages.

   The shortlist caps alerts at `shortlist_budget - (deep_rank_budget - alert_budget)` — 65 of 80 — and the cap is **derived, not configured**. Every alert slot above it is one the final cut cannot fill: that stage caps alerts at `alert_budget` and then wants `deep_rank_budget - alert_budget` non-alert jobs, so the shortlist has to hand it at least that many. Uncapped on 2026-08-22 this cost 10 of 25 rankset slots. The cap sits as high as the final cut tolerates rather than at `alert_budget`, which keeps alerts between the two numbers reaching enrichment and re-scoring on their bodies. Alerts cut for exceeding it are logged with their score range and the best one's title, so a lost high scorer is visible; the shortlist summary also reports `non_alert_selected` against `non_alert_needed_downstream`, which predicts an underfilled rankset before enrichment spends a request.

3. **Phase 1c — Enrich:** `scripts/enrich_linkedin.py` fetches the full posting text for the **shortlisted** LinkedIn cards, up to `linkedin.detail_enrich_budget`. Reading the shortlist rather than the final rankset is what makes the requests able to change an outcome instead of merely confirming one. Requests go where reading the body can *change* the answer, in this order:

   1. **Verification before discovery** — a shortlisted job with an `UNKNOWN` hard-gate verdict that the **final cut would currently reach** is one Phase 2 will score, and `gate_jobs.py` may draft documents for, on a language or tenure risk nobody has read. One request settles it. This tier is spent in pre-rank score order, and it is bounded to the rank cut on purpose: unverified alone was 65 of the 80 shortlisted on 2026-08-22, so an unbounded verification tier would consume every request and starve discovery exactly as the alert tier once did. Unverified *and* in the cut was 13 — a budget of 15 covers all of them with 2 requests left over.

      "In the cut" is modelled in **three passes, not as a top-N by score**, because the final cut is not one: `prerank_jobs.py` takes the alert pool on attribution up to `alert_budget` *before* score is consulted, then holds `per_track_floor` slots per attributed track, then lets score fill the rest. Modelling it as a top-N looks harmless and is not. Replayed on the 2026-08-22 corpus, a top-25-by-score model hit 14 of the real 25 rows and reached **3** of its 13 UNKNOWNs — every alert row there scored 0-30 while non-alert rows reached 135, so score order puts all ten outside the cut and skips precisely the rows most likely to be UNKNOWN. The three-pass model hits 25 of 25 and all 13. Two details carry that result: alert membership is read off `prerank.reason`, not `portal` (3 of the 42 alert rows were found by a search and merely matched by an alert, so `portal` misses them), and `track_guess: null` means *no track matched* and so earns no floor slot — defaulting it to a track is the 2026-08-18 failure where "Procurement Counsel" and "Account Executive, LATAM" took reserved slots.
   2. **Half-hybrid cards next** — the title shows a business domain but no AI/data enabler, or an enabler but no domain. The missing half is either in the body or nowhere, and one request settles it. Both directions count. This is the *discovery* goal, and it keeps the whole budget the verification tier does not need.
   3. **Alert-sourced within that band**, as a tie-break rather than a tier. This is a correction. An earlier version put every alert card ahead of every half-hybrid card, on the theory that alert emails carry no description while search cards carry a 500-char snippet. The snippet half is false: the `linkedin-search` CLI returns a description only from its `detail` command — the very call being allocated here — so on the 2026-08-19 corpus all 371 LinkedIn search cards had a null snippet, and the 121 snippets came from freehire and weworkremotely, portals this loop never touches. Alert and search cards are equally blind. Ordering on that assumed gap cost the whole budget: 26 alert cards took all 15 requests, five for cards showing both halves or neither, and the half-hybrid band got nothing.
   4. **Then an `UNKNOWN` hard-gate verdict** *outside* the cut, as a tie-break inside the band rather than a tier, for the same reason it is a tier inside the cut: one request answers two questions at once — whether the hybrid completes, and whether the posting demands a language or a tenure that discards it. A gate-FAILed job never reaches here; Phase 1b dropped it before the budget was divided.
   5. **Then title match, then aggregation order**, so the same corpus always produces the same fetch list.

   Pre-rank score orders the verification tier and nothing below it. Letting it reach into the discovery tiers would re-open the 2026-08-19 failure from the other side: an alert card scoring 90 that already shows both halves would outrank a half-hybrid card scoring 30 whose missing half only a request can find, which is the trade the band exists to refuse. The score read is `prerank.score` — the number the final cut acts on — not the title-match heuristic; the two disagree sharply, and "Advanced PMO Specialist - Sourcing & Procurement Excellence" scores 0 on the 13 track queries and well above the cut on the two-axis model.

   When the budget cannot verify the whole cut, the phase says so with the specific number to raise `linkedin.detail_enrich_budget` to, counting only the jobs it can actually reach: an in-cut UNKNOWN on a non-LinkedIn portal, or a LinkedIn card whose body the pre-ranker already read, stays UNKNOWN whatever the budget, and is reported separately rather than folded into a budget ask that would not change the outcome.

   Alert-sourced, half-hybrid and verification-tier cards are all exempt from the title filter, and the exemption is about evidence, not merit: alerts use vocabulary the track queries do not ("business excellence manager"), so scoring their titles against those queries mostly returns 0. Priority is attention, never approval — Phase 2 still scores on merit and `gate_jobs.py` still decides on documents. This is why LinkedIn jobs rank better: better evidence, never a score bonus.

4. **Phase 2 — Rank:** Claude Code scores the pre-ranked jobs with the fit framework (Technical 30%, Experience 25%, Behavioral 15%, Career 30%) and applies the Eligibility and Language gates. Bounded by `RANK_TIMEOUT` (default 1800s; ~24s per job).

5. **Phase 2b — Gate:** `scripts/gate_jobs.py` decides in code, not in the prompt, which jobs get documents: score ≥ 75, or ≥ 60 if `alert_matched.json` still has a live (≤ 30-day) key for them. **The gate is allowed to pass nothing** — a thin day produces a report and no drafts, by design.

6. **Phase 3 — Offer for selection:** Writes `/tmp/jobsearch_pending_selection.json` naming the rankset, then starts `com.salman.jobsearch.selector` and exits. The listener sends one message per job with a toggle button. **This run writes no CVs and no cover letters.** Documents are generated later, only for the jobs you tap Submit on — see "Selection and generation" below.

7. **Phase 4 — QA:** Reviews generated documents against the verification checklist in `CLAUDE.md`. In practice this is now a no-op at pipeline time, because nothing has been generated yet; the equivalent checks (lualatex compile, exact page count, `pdftotext` ATS extraction) run inside `prompts/selected_job_draft.md` at generation time instead.

8. **Phase 5 — Report:** Writes `reports/daily/YYYY-MM-DD.md` — summary, the pre-rank funnel, ranked table, documents table, warnings, errors.

9. **Phase 6 — Retired.** The digest used to be mailed here with the PDFs attached. Both halves stopped applying once selection moved to Telegram: the documents do not exist when the run ends, and notification is now the `tg-notify` ping plus the selection list itself. `scripts/send_email.py` and `automation.json`'s email block both stay — Phase 0b reads the LinkedIn alerts over IMAP with that same Gmail app password.

10. **Phase 7 — Cleanup:** Removes the `/tmp` artifacts unless `KEEP_TEMP=1`. **Except the rankset** (`/tmp/jobsearch_rankset_<date>.json`) and the handoff marker, which the selector still needs while the selection is open; copies older than a day are aged out instead.

## Selection and generation

Phase 3 does not block. `com.salman.jobsearch.selector` is a separate launchd job that holds a Telegram long-poll for up to 20 hours, so Submit can be pressed hours later.

- **Nothing runs permanently.** The listener is started by Phase 3 and exits as soon as generation finishes or the window closes.
- **It only ever offers what Phase 3 handed it.** The rankset path and date travel in `/tmp/jobsearch_pending_selection.json`, because `launchctl kickstart` cannot pass arguments. With no marker the listener exits 0 without sending anything. This matters more than it looks: `KeepAlive` starts a launchd job the moment it is bootstrapped — **omitting `RunAtLoad` does not prevent that** — so installing the plist runs the listener. An earlier version inferred the date from the clock instead, and installing it posted a stale 15-job list to the chat.
- **A reboot does not lose your picks.** Telegram queues undelivered updates for 24h; the listener reloads its state file, reattaches to the messages already sent, and sends only the tail it never reached.
- **`KeepAlive` restarts it on a crash only.** Every deliberate outcome exits 0, so launchd cannot loop on a decision. A selection already generated is marked `completed` and refuses to regenerate.
- **Buttons from an earlier list are inert.** `callback_data` carries a row index, so a tap on yesterday's job #3 would otherwise toggle today's job #3. Taps on messages absent from the current state file are refused with a notice instead.
- **The whole ranked list is offered, not just what cleared the gate** — the point of choosing by hand is to see the near-misses too.
- **Run it by hand** against a rankset with `python3 scripts/telegram_select.py --rankset <file>` (add `--dry-run` to print the list without sending). Only one poller may hold the bot token at a time, so stop the launchd job first: `launchctl kill TERM gui/$(id -u)/com.salman.jobsearch.selector`.

## Run controls (env vars)

For ad-hoc runs. None of them affect the scheduled 08:00 run.

| Variable | Effect |
|---|---|
| `KEEP_TEMP=1` | Keep the `/tmp` artifacts (needed later by `RESUME=1`). |
| `RANK_TIMEOUT=<s>` | Seconds Phase 2 may spend scoring. Raise only alongside `prerank.deep_rank_budget`. |
| `RESUME=1` | Reuse today's fetched corpus instead of re-querying the portals, and skip enrichment. Refuses to invent a corpus if the Phase 1 output is gone. |
| `SKIP_ALERTS=1` | Skip Phase 0b's mailbox read. Keys already in `alert_matched.json` keep their 30-day window; anything alerted only today is judged at 75. |
| `SELECTOR_WINDOW=<s>` | How long the listener waits for Submit. Default 72000 (20h) — long enough to answer next morning, short enough to release the bot token before the next 08:00 run. |

`SKIP_DRAFT` and `SKIP_EMAIL` are gone. `SKIP_DRAFT` withheld documents for a single review run; withholding is now the permanent default and selecting is the opt-in, so there is nothing left for it to suppress. `SKIP_EMAIL` guarded a digest that no longer sends.

## Configuration

`config/automation.json` (gitignored — it holds the Gmail app password):
- `enabled`: ON/OFF switch
- `pipeline.max_jobs_to_apply`: How many jobs to draft for (default: 5)
- `pipeline.min_score_threshold`: Minimum score to include (default: 60)
- `pipeline.skip_portals`: Portals to skip (e.g. `["arbeitnow-search"]`)
- `email.*`: SMTP settings for digest emails. **Phase 0b reuses `email.smtp_user` / `email.smtp_password` for IMAP** — a Gmail app password grants both — so reading the alerts adds no new credential. An optional `imap` block overrides host/port/user/password if you want a separate mailbox.

`config/search_matrix.json` (tracked — query-side config, no credentials):
- `linkedin.max_requests_per_run`: hard stop on total LinkedIn traffic, searches + enrichment (60)
- `linkedin.detail_enrich_budget`: how many of those are Phase 1c detail calls (15); raising it lowers the search count one-for-one
- `prerank.deep_rank_budget` / `alert_budget` / `per_track_floor`: how many jobs reach Phase 2 and how the slots are shared
- `alerts.*`: Phase 0b's label, `lookback_days`, and `track_map` — one entry per alert you created in LinkedIn, spelled as LinkedIn spells it

## Safety

Three-layer guarantee:
1. Config: `safety.auto_apply: false`
2. Prompts: Explicitly instruct "NEVER submit applications"
3. Shell script: Email goes to you only, never to employers

## Logs and Reports

- **Logs:** `logs/daily/YYYY-MM-DD.log` — timestamped entries per phase
- **Reports:** `reports/daily/YYYY-MM-DD.md` — summary of each run
- **launchd output:** `logs/daily/launchd-stdout.log` and `launchd-stderr.log`
