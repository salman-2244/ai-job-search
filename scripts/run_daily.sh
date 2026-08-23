#!/usr/bin/env bash
set -euo pipefail

# === Ensure claude CLI is in PATH ===
# ~/.local/bin carries tg-notify, used by notify_result below. launchd hands this
# script a minimal PATH, so it has to be named here or the ping silently no-ops.
export PATH="$HOME/.local/bin:/Users/salman/.nvm/versions/node/v24.19.0/bin:/Users/salman/.bun/bin:/Library/TeX/texbin:/Library/Frameworks/Python.framework/Versions/3.10/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# === Configuration ===
PROJECT_DIR="/Users/salman/Projects/ai-job-search"
CONFIG="$PROJECT_DIR/config/automation.json"
MATRIX="$PROJECT_DIR/config/search_matrix.json"
LOG_DIR="$PROJECT_DIR/logs/daily"
REPORT_DIR="$PROJECT_DIR/reports/daily"
LOCK_DIR="/tmp/jobsearch_daily_pipeline.lock"
TODAY=$(date +%Y-%m-%d)
LOG_FILE="$LOG_DIR/${TODAY}.log"
REPORT_FILE="$REPORT_DIR/${TODAY}.md"
PLAN_FILE="/tmp/jobsearch_plan_${TODAY}.tsv"
JOBS_FILE="/tmp/jobsearch_fetched_jobs_${TODAY}.json"
# The wide intermediate cut, between the two pre-rank stages. Enrichment reads it,
# so it has to be a file rather than a pipe: Phase 1c writes descriptions back into
# it and Phase 1b-final scores what Phase 1c left behind.
SHORTLIST_FILE="/tmp/jobsearch_shortlist_${TODAY}.json"
SHORTLIST_SUMMARY_FILE="/tmp/jobsearch_shortlist_summary_${TODAY}.json"
RANKSET_FILE="/tmp/jobsearch_rankset_${TODAY}.json"
DEFERRED_FILE="/tmp/jobsearch_deferred_${TODAY}.json"
PRERANK_FILE="/tmp/jobsearch_prerank_summary_${TODAY}.json"
ENRICH_FILE="/tmp/jobsearch_enrich_summary_${TODAY}.json"
TOP5_FILE="/tmp/jobsearch_top5_${TODAY}.json"
# Phase 2's stderr, kept out of the log so a failed attempt can be classified on its
# own text rather than on the whole run's. Appended to the log either way, and the
# stdout of a failed attempt is preserved beside it — the 2026-08-23 run overwrote
# the only copy of whatever the ranker said with "[]" before anyone could read it.
RANK_ERR_FILE="/tmp/jobsearch_rank_stderr_${TODAY}.log"
RANK_FAIL_FILE="/tmp/jobsearch_rank_failed_stdout_${TODAY}.txt"
NOT_DRAFTED_FILE="/tmp/jobsearch_not_drafted_${TODAY}.json"
WARN_FILE="/tmp/jobsearch_warnings_${TODAY}.txt"
APPLICABLE_FILE="/tmp/jobsearch_applicable_${TODAY}.json"
QA_FILE="/tmp/jobsearch_qa_${TODAY}.json"
APP_PACKAGES_DIR="/tmp/jobsearch_app_packages_${TODAY}"

# Phase 0b's portal output. The name matters twice: the `jobsearch_portal_*_${TODAY}`
# glob below picks it up with no change to the aggregation call, and `linkedin-alert`
# is the string aggregate_jobs.detect_portal() maps to the linkedin-alert portal —
# rename either half and alert jobs silently become linkedin-search.
ALERT_JOBS_FILE="/tmp/jobsearch_portal_linkedin-alert_${TODAY}.json"
# Tracked, not /tmp: gate_jobs.py reads this for the 30-day alert-matched window, so
# it has to outlive the run that wrote it.
ALERT_STORE="$PROJECT_DIR/job_scraper/alert_matched.json"

# === Run controls (both default to off; the scheduled 08:00 run is unaffected) ===
#   KEEP_TEMP=1   keep the /tmp artifacts for inspection instead of deleting them in
#                 Phase 7, so the full fetched-jobs list survives the run.
#   SKIP_NOTIFY=1 don't send the Telegram ping. The ping fires from the EXIT trap
#                 on success and on failure alike, so leave it on for the 08:00 run.
#   RANK_TIMEOUT  seconds Phase 2 may spend scoring before it is stopped. Phase 2
#                 no longer sees the whole corpus: Phase 1b pre-ranks everything
#                 fetched and hands the ranker only prerank.deep_rank_budget +
#                 alert_budget jobs. At the measured ~24s/job that is ~10 minutes
#                 for 25 jobs, so 1800s leaves generous headroom. Scoring all 504
#                 jobs of the 2026-08-18 run needed ~3.5h and timed out twice.
#                 Raise this only alongside prerank.deep_rank_budget.
#   RANK_ATTEMPTS how many times Phase 2 may run before giving up. The ranker goes
#                 out through the agentrouter gateway, and a gateway hiccup used to
#                 cost the whole day: the 2026-08-23 08:11 run exited 1 after 269s
#                 with an empty stderr, scored 0 of 25 jobs and published an empty
#                 report. Attempts are bounded because the failure may be a rejected
#                 credential, which no number of retries fixes. A timeout is never
#                 retried — see the Phase 2 loop.
#   RANK_BACKOFF seconds before the second attempt, doubling for each one after it
#                 (20s, 40s at the default). Worst case adds ~60s to a phase that
#                 normally runs ~10 minutes.
#   RESUME=1      reuse today's already-fetched job corpus instead of re-querying the
#                 portals, and skip enrichment. For recovering a run that fetched
#                 successfully but died later: a second full run would fire another
#                 ~45 LinkedIn searches on top of the ones already made today, which
#                 breaks the approved per-day volume. Requires the Phase 1 output from
#                 a KEEP_TEMP=1 run to still exist; refuses to invent one.
#   SKIP_ALERTS=1 don't read the LinkedIn job-alert mailbox in Phase 0b. The corpus
#                 then contains only what the portal queries found, and no job can
#                 reach the gate's alert-matched 60 tier. Costs no LinkedIn requests
#                 either way — Phase 0b reads email, not linkedin.com.
#   ALERT_TIMEOUT seconds Phase 0b may spend on the mailbox before it is stopped.
#                 linkedin_alerts.py sets its own 60s socket timeout, so this is the
#                 second line of defense rather than the first: it bounds the phase
#                 whichever call blocks, including one that never touches that socket.
#                 Normal duration is ~11s, so 300s cannot fire on a merely slow
#                 mailbox. The 2026-08-22 08:00 run needed this and had neither: it
#                 sat in a single IMAP fetch for six hours, holding the lock and
#                 producing no corpus, no ranking and no digest.
KEEP_TEMP="${KEEP_TEMP:-0}"
RANK_TIMEOUT="${RANK_TIMEOUT:-1800}"
RANK_ATTEMPTS="${RANK_ATTEMPTS:-3}"
RANK_BACKOFF="${RANK_BACKOFF:-20}"
RESUME="${RESUME:-0}"
SKIP_ALERTS="${SKIP_ALERTS:-0}"
SKIP_NOTIFY="${SKIP_NOTIFY:-0}"
ALERT_TIMEOUT="${ALERT_TIMEOUT:-300}"
# Read out of the module rather than restated here, so the Phase 0b warning cannot
# quote a socket timeout the code no longer uses. Grepped rather than imported: this
# runs on every invocation and wants no side effects.
IMAP_TIMEOUT_HINT=$(grep -oE '^IMAP_TIMEOUT_SECONDS = [0-9]+' \
    "$PROJECT_DIR/scripts/linkedin_alerts.py" 2>/dev/null | grep -oE '[0-9]+$' || true)
IMAP_TIMEOUT_HINT="${IMAP_TIMEOUT_HINT:-60}"

# === Logging ===
log() {
    local ts
    ts=$(date +"%H:%M:%S")
    echo "[$ts] $*" | tee -a "$LOG_FILE"
}

# === Telegram ping ===
# Called from the EXIT trap rather than after "Pipeline complete", so a hard
# failure reports itself too. The 08:00 run on 2026-08-22 sat six hours in a
# single IMAP fetch holding the lock and produced no digest; nothing said so
# until it was noticed by hand. Delivery is via ~/.local/bin/tg-notify, which
# talks to the Telegram bot already running under launchd.
notify_result() {
    local ec="$1" status
    if [[ "$SKIP_NOTIFY" == "1" ]]; then
        return 0
    fi
    if ! command -v tg-notify >/dev/null 2>&1; then
        return 0
    fi
    if [[ "$ec" == "0" ]]; then
        status="completed"
    else
        status="FAILED (exit $ec)"
    fi
    # ${VAR:-?} throughout: set -u is on and an early-phase abort gets here with
    # the counts never assigned.
    tg-notify --title "ai-job-search ${TODAY}: ${status}" \
        "jobs fetched: ${TOTAL_JOBS:-?}
ranked: ${RANKED_COUNT:-?}
report: ${REPORT_FILE}
log: ${LOG_FILE}" \
        || echo "[notify] tg-notify failed" >&2
}

# === Guard: Config check ===
if [[ ! -f "$CONFIG" ]]; then
    echo "Config not found at $CONFIG — copy automation.json.example and configure." >&2
    exit 1
fi

ENABLED=$(python3 -c "import json; print(json.load(open('$CONFIG')).get('enabled', False))" 2>/dev/null || echo "False")
if [[ "$ENABLED" != "True" ]]; then
    echo "Automation disabled in config. Exiting silently."
    exit 0
fi

# === Guard: Search matrix check ===
# The matrix carries the queries AND the LinkedIn request cap. Without it there is no
# safe request volume to fall back on, so this is fatal rather than defaulted.
if [[ ! -f "$MATRIX" ]]; then
    echo "Search matrix not found at $MATRIX — the pipeline has no queries to run." >&2
    exit 1
fi

# === Guard: Lock (atomic mkdir) ===
if mkdir "$LOCK_DIR" 2>/dev/null; then
    : # trap installed after this block, so it covers the stale-lock path too
else
    # Check if stale (older than 2 hours)
    if [[ -d "$LOCK_DIR" ]]; then
        lock_age=$(( $(date +%s) - $(stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0) ))
        if (( lock_age > 7200 )); then
            rm -rf "$LOCK_DIR"
            mkdir "$LOCK_DIR"
            log "Stale lock removed (age: ${lock_age}s)"
        else
            echo "Pipeline already running (lock age: ${lock_age}s). Exiting."
            exit 1
        fi
    fi
fi

# Installed here, not inside the mkdir branch above, so the stale-lock recovery
# path releases its lock as well -- it took the lock and never registered a trap.
# Safe at this point: the "already running" branch has exited already, so this
# only ever removes a lock the current run owns.
trap 'ec=$?; rm -rf "$LOCK_DIR"; notify_result "$ec"' EXIT
trap 'exit 143' TERM   # so the EXIT trap still runs when launchd stops the run
trap 'exit 130' INT

# === Setup directories ===
mkdir -p "$LOG_DIR" "$REPORT_DIR" "$APP_PACKAGES_DIR"

log "=== Pipeline Run: $TODAY $(date +"%H:%M:%S") ==="
log "Config: enabled=true, max_jobs=$(python3 -c "import json; print(json.load(open('$CONFIG'))['pipeline']['max_jobs_to_apply'])")"

# === Read pipeline config ===
MAX_JOBS=$(python3 -c "import json; print(json.load(open('$CONFIG'))['pipeline']['max_jobs_to_apply'])")
MIN_SCORE=$(python3 -c "import json; print(json.load(open('$CONFIG'))['pipeline']['min_score_threshold'])")
SKIP_PORTALS=$(python3 -c "import json; print(' '.join(json.load(open('$CONFIG'))['pipeline'].get('skip_portals', [])))")
# No EMAIL_ENABLED read here any more: Phase 6 is retired, so nothing in this run
# consults email.enabled. The block itself stays in automation.json — Phase 0b
# needs the credential inside it for IMAP.
# Defaults to False, so a matrix predating the alerts block skips Phase 0b instead of
# failing on it. `alerts` lives in the matrix, not automation.json, because it carries
# the query-side config (which alert maps to which Profile Track); the credentials it
# needs come from automation.json's email block.
ALERTS_ENABLED=$(python3 -c "import json; print(json.load(open('$MATRIX')).get('alerts', {}).get('enabled', False))" 2>/dev/null || echo "False")

# === Phase 0b: Read Salman's own LinkedIn job-alert emails ===
# Runs BEFORE Phase 1 on purpose. It writes a portal file into the same
# /tmp/jobsearch_portal_*_${TODAY}.json namespace Phase 1 aggregates, so alert jobs
# enter the corpus as a portal rather than as an afterthought — and because the glob
# sorts `linkedin-alert` ahead of every `linkedin_<query>_<geo>` file, a posting found
# by both an alert and a search keeps its alert attribution through dedup.
#
# This is not redundant with the LinkedIn searches. The alerts use vocabulary the 13
# track queries do not ("business excellence manager", "PMO & Automation Analyst"),
# which is exactly why they are worth reading. It also costs zero LinkedIn requests:
# it reads Gmail over IMAP and rebuilds each job URL from the numeric ID instead of
# following a link out of the email.
#
# Non-fatal by design. A mail outage, an expired app password or a LinkedIn markup
# change must not cost the run its ranking — the pipeline degrades to search-only
# and the reason goes in the report rather than into silence.
cd "$PROJECT_DIR"

if [[ "$RESUME" == "1" ]]; then
    log "Phase 0b: SKIPPED (RESUME=1) — reusing the corpus that already includes whatever alerts the earlier run read"
elif [[ "$SKIP_ALERTS" == "1" ]]; then
    # Only new alerts are skipped. Keys already in $ALERT_STORE stay live for their
    # 30-day window, so jobs alerted on earlier days can still reach the gate's 60.
    log "Phase 0b: SKIPPED (SKIP_ALERTS=1) — no newly alerted jobs; keys already in the store keep their 30-day window"
elif [[ "$ALERTS_ENABLED" != "True" ]]; then
    log "Phase 0b: skipped (alerts.enabled is false in $MATRIX)"
else
    log "Phase 0b: reading LinkedIn job alerts from the mailbox..."
    # Backgrounded with a watchdog rather than run in the foreground, because macOS
    # ships no `timeout`. Same shape as Phase 2's loop below, and the same two `set -e`
    # hazards apply: the timeout branch must not fall through to `wait` on a PID it has
    # already reaped, and the normal-path `wait` must be guarded with `|| EXIT=$?` so a
    # non-zero child does not abort the whole script before the failure branch runs.
    #
    # This is the second line of defense. linkedin_alerts.py's own socket timeout is
    # the first and should always fire earlier; this one exists for the blocking call
    # that is not a socket read — a DNS lookup, a TLS handshake in a library that
    # ignores the socket deadline, an unbounded loop over a malformed digest.
    python3 scripts/linkedin_alerts.py \
            --config "$CONFIG" --matrix "$MATRIX" \
            --jobs-out "$ALERT_JOBS_FILE" --store "$ALERT_STORE" \
            --today "$TODAY" >>"$LOG_FILE" 2>&1 &
    ALERT_PID=$!
    ALERT_WAIT=0
    ALERT_TIMED_OUT=0
    while kill -0 $ALERT_PID 2>/dev/null; do
        sleep 5
        ALERT_WAIT=$((ALERT_WAIT + 5))
        if (( ALERT_WAIT >= ALERT_TIMEOUT )); then
            log "Phase 0b TIMEOUT after ${ALERT_WAIT}s — killing the mailbox read"
            kill $ALERT_PID 2>/dev/null || true
            sleep 2
            kill -9 $ALERT_PID 2>/dev/null || true
            wait $ALERT_PID 2>/dev/null || true
            ALERT_TIMED_OUT=1
            break
        fi
    done
    ALERT_EXIT=0
    if (( ALERT_TIMED_OUT == 1 )); then
        ALERT_EXIT=124
    else
        wait $ALERT_PID || ALERT_EXIT=$?
    fi

    if (( ALERT_EXIT == 0 )); then
        ALERT_COUNT=$(python3 -c "import json; print(len(json.load(open('$ALERT_JOBS_FILE'))['results']))" 2>/dev/null || echo "0")
        log "Phase 0b complete: $ALERT_COUNT job(s) from alerts (0 LinkedIn requests)"
        if (( ALERT_COUNT == 0 )); then
            log "  note: no alert jobs today — check the JobSearch/LinkedIn-Alerts label if that looks wrong"
        fi
    else
        # The script exits non-zero both for "could not reach the mailbox" and for
        # "read messages but parsed no job cards", the second being a probable
        # markup change. Either way the run continues without alerts. 124 is this
        # watchdog's own code, kept distinct so the report can say "stopped" rather
        # than "failed" — a phase that was killed mid-read is not a verdict about
        # the mailbox's contents.
        if (( ALERT_EXIT == 124 )); then
            log "WARNING: Phase 0b was stopped after ${ALERT_TIMEOUT}s — continuing with search results only. See $LOG_FILE."
            echo "The LinkedIn alert read (Phase 0b) did not finish within ${ALERT_TIMEOUT}s and was stopped, so today's alert emails went unread and the corpus is search-only. This is the phase-level watchdog, which means linkedin_alerts.py's own ${IMAP_TIMEOUT_HINT}s socket timeout did not catch whatever blocked — worth looking at rather than just raising ALERT_TIMEOUT. Jobs alerted on earlier days keep their 30-day window; anything alerted only today is judged at the standard 75." >> "$WARN_FILE"
        else
            log "WARNING: Phase 0b failed — continuing with search results only. Today's alerts went unread, so any job alerted only today misses the gate's 60 tier; see $LOG_FILE."
            echo "LinkedIn alert ingestion (Phase 0b) failed, so today's alert emails went unread and the corpus is search-only. Jobs alerted on earlier days keep their 30-day window; anything alerted only today is judged at the standard 75. Check the log for a mailbox error or a LinkedIn markup change." >> "$WARN_FILE"
        fi
        # Half-written output must not reach the aggregator as if it were complete.
        rm -f "$ALERT_JOBS_FILE"
    fi
fi

# === Phase 1: Fetch jobs from all portals ===
cd "$PROJECT_DIR"

if [[ "$RESUME" == "1" ]]; then
    # Reuse the corpus a previous run already paid portal requests for. Fail loudly
    # rather than silently falling through to a fetch: someone asking to resume is
    # asking specifically NOT to spend more requests, so quietly spending them would
    # be the worst outcome.
    if [[ ! -s "$JOBS_FILE" ]]; then
        log "FATAL: RESUME=1 but $JOBS_FILE is missing or empty — nothing to resume from."
        log "       Re-run without RESUME=1 to fetch (costs a fresh round of portal requests)."
        exit 1
    fi
    TOTAL_JOBS=$(python3 -c "import json; print(json.load(open('$JOBS_FILE'))['meta']['unique'])" 2>/dev/null || echo "0")
    if (( TOTAL_JOBS == 0 )); then
        log "FATAL: RESUME=1 but $JOBS_FILE reports 0 unique jobs — refusing to rank an empty corpus"
        exit 1
    fi
    log "Phase 1: SKIPPED (RESUME=1) — reusing $TOTAL_JOBS jobs from $JOBS_FILE, 0 new portal requests"
else
log "Phase 1: Fetching jobs from portals..."

# Portal CLI invocations (sequential to avoid rate limits).
# Queries are NOT hardcoded here — they come from config/search_matrix.json via
# build_search_plan.py. The LinkedIn matrix is 13 track queries x 10 geos against a
# hard cap of 60 requests per run, so choosing the day's subset needs rotation
# arithmetic that bash 3.x (what macOS ships, no associative arrays) can't express.
#
# Sets PORTAL_COUNT as a side effect: the caller needs the result count, and `log`
# already writes to stdout, so a return value can't be echoed.
run_portal() {
    local name="$1" portal="$2"
    shift 2
    local outfile="/tmp/jobsearch_portal_${name}_${TODAY}.json"
    PORTAL_COUNT=0

    # Skip list matches either a whole portal ("linkedin") or one plan entry
    for skip in $SKIP_PORTALS; do
        if [[ "$skip" == "$portal" || "$skip" == "$name" ]]; then
            log "  $name: skipped (disabled in config)"
            echo '{"meta":{"count":0},"results":[]}' > "$outfile"
            return 0
        fi
    done

    log "  $name: searching..."
    if bun run ".agents/skills/${portal}-search/cli/src/cli.ts" "$@" > "$outfile" 2>>"$LOG_FILE"; then
        PORTAL_COUNT=$(python3 -c "import json; print(json.load(open('$outfile')).get('meta',{}).get('count', 0))" 2>/dev/null || echo "0")
        log "  $name: $PORTAL_COUNT results"
    else
        local rc=$?
        log "  $name: CLI failed (exit $rc)"
        echo '{"meta":{"count":0},"results":[]}' > "$outfile"
    fi
}

# Build the day's plan: which query x geo pairs run, within the request cap
if ! python3 scripts/build_search_plan.py --date "$TODAY" > "$PLAN_FILE" 2>>"$LOG_FILE"; then
    log "FATAL: could not build a search plan from config/search_matrix.json — see log"
    exit 1
fi

PLANNED=$(wc -l < "$PLAN_FILE" | tr -d ' ')
if [[ "$PLANNED" -eq 0 ]]; then
    log "FATAL: search plan is empty — every portal is disabled in config/search_matrix.json"
    exit 1
fi

LINKEDIN_DELAY=$(python3 -c "import json; print(json.load(open('$MATRIX'))['linkedin'].get('delay_seconds', 4))" 2>/dev/null || echo "4")
LINKEDIN_CAP=$(python3 -c "import json; print(json.load(open('$MATRIX'))['linkedin'].get('max_requests_per_run', 60))" 2>/dev/null || echo "60")
ENRICH_BUDGET=$(python3 -c "import json; print(json.load(open('$MATRIX'))['linkedin'].get('detail_enrich_budget', 0))" 2>/dev/null || echo "0")
LINKEDIN_IN_PLAN=$(grep -c $'\tlinkedin\t' "$PLAN_FILE" || true)
log "Search plan: $PLANNED requests ($LINKEDIN_IN_PLAN LinkedIn, cap $LINKEDIN_CAP total incl. $ENRICH_BUDGET reserved for enrichment, ${LINKEDIN_DELAY}s apart)"

# Defence in depth: the plan builder enforces the cap, but a hand-edited plan file
# must not be able to quietly multiply the request volume. The cap covers searches
# AND the Phase 1c detail calls that follow — both hit linkedin.com minutes apart —
# so the searches are checked against the cap minus the enrichment reserve.
LINKEDIN_SEARCH_CAP=$((LINKEDIN_CAP - ENRICH_BUDGET))
if (( LINKEDIN_SEARCH_CAP < 1 )); then
    LINKEDIN_SEARCH_CAP=1
fi
if (( LINKEDIN_IN_PLAN > LINKEDIN_SEARCH_CAP )); then
    log "FATAL: plan asks for $LINKEDIN_IN_PLAN LinkedIn searches, above the search cap of $LINKEDIN_SEARCH_CAP (total cap $LINKEDIN_CAP minus $ENRICH_BUDGET reserved for Phase 1c enrichment)"
    exit 1
fi

LINKEDIN_ATTEMPTED=0
LINKEDIN_RESULTS=0
while IFS=$'\t' read -r -a plan_row; do
    (( ${#plan_row[@]} >= 3 )) || continue
    p_name="${plan_row[0]}"
    p_portal="${plan_row[1]}"
    run_portal "$p_name" "$p_portal" "${plan_row[@]:2}"
    if [[ "$p_portal" == "linkedin" ]]; then
        LINKEDIN_ATTEMPTED=$((LINKEDIN_ATTEMPTED + 1))
        LINKEDIN_RESULTS=$((LINKEDIN_RESULTS + PORTAL_COUNT))
        sleep "$LINKEDIN_DELAY"
    fi
done < "$PLAN_FILE"

# A total LinkedIn shutout means a fetch problem (block page, changed markup, no
# network), not an empty European job market. Say so instead of reporting "0 jobs"
# as if it were a finding.
if (( LINKEDIN_ATTEMPTED > 0 && LINKEDIN_RESULTS == 0 )); then
    log "WARNING: all $LINKEDIN_ATTEMPTED LinkedIn queries returned 0 results — treating as a fetch failure, not an empty market. Check the log for block pages or CLI errors."
    echo "LinkedIn returned 0 results across all $LINKEDIN_ATTEMPTED queries — likely a fetch failure (block page, markup change, or no network), not an empty market." >> "$WARN_FILE"
fi

# Aggregate every portal output this run produced. Globbed, not listed: the matrix
# decides how many files exist, so an explicit list would silently drop the rest.
log "Aggregating results..."
PORTAL_FILES=(/tmp/jobsearch_portal_*_${TODAY}.json)
if [[ ! -e "${PORTAL_FILES[0]}" ]]; then
    log "FATAL: no portal output files found — Phase 1 produced nothing"
    exit 1
fi
log "  aggregating ${#PORTAL_FILES[@]} portal output files"
python3 scripts/aggregate_jobs.py "${PORTAL_FILES[@]}" > "$JOBS_FILE" 2>>"$LOG_FILE"

TOTAL_JOBS=$(python3 -c "import json; print(json.load(open('$JOBS_FILE'))['meta']['unique'])" 2>/dev/null || echo "0")
log "Phase 1 complete: $TOTAL_JOBS unique jobs fetched"

fi  # end of the RESUME=1 branch opened before Phase 1

# === Phase 1b: choose which fetched jobs are worth Phase 2's model pass ===
# Fetching stays broad — coverage is unchanged — but the model ranker costs ~24s per
# job, so scoring all 504 jobs of the 2026-08-18 run needed ~3.5h and timed out twice.
# Phase 1b is free and deterministic: it sees every fetched job and hands the ranker
# only prerank.deep_rank_budget + alert_budget of them.
#
# It runs in TWO stages with enrichment between them, and the order is the fix rather
# than a refactor. Stage 1 cuts wide (prerank.shortlist_budget, ~80), Phase 1c spends
# its LinkedIn requests on that shortlist, and Phase 1b-final re-scores the jobs whose
# bodies have just arrived. A single cut scores every LinkedIn card on the 500-char
# snippet it happened to ship with, and on the 2026-08-19 corpus that buried 102
# postings (18%) whose business domain was in the title with no AI/data word anywhere
# in it: their signal sat unread in the body and their best rank was 31st, so a 25-slot
# cut reached none of them.
#
# Nothing is dropped. Every excluded job is annotated in place and written to
# $DEFERRED_FILE with the reason it was cut, so Phase 5 can still account for all of
# them. A pipeline that quietly narrowed 504 to 15 would be indistinguishable from a
# thin market.
#
# This is a SELECTION score, never a fit score: it awards no points and decides
# nothing about documents. That stays with gate_jobs.py at 75, or 60 when the job was
# LinkedIn alert-matched.
#
# Which scorer runs is the matrix's call, not the runner's: `scoring.enabled` picks the
# two-axis domain/enabler model or the original query-match one. No --two-axis here on
# purpose — hardcoding it would make that switch unreachable, and --stage is
# independent of it (it sets the budget, not the model).
#
# Runs on both paths, including RESUME=1 — Phase 2 reads the rankset, so a rankset
# built only inside the fetch branch would not exist on a resumed run. Pre-ranking
# costs no portal requests, so this does not violate the resume contract. An existing
# rankset is reused rather than rebuilt: it carries the enrichment the earlier run
# already paid LinkedIn requests for, and rebuilding from $JOBS_FILE would lose it.
if [[ "$RESUME" == "1" && -s "$RANKSET_FILE" ]]; then
    log "Phase 1b: SKIPPED (RESUME=1) — reusing the rankset at $RANKSET_FILE"
else
    log "Phase 1b (shortlist): cutting $TOTAL_JOBS fetched jobs down to a wide shortlist..."
    if ! python3 scripts/prerank_jobs.py --jobs "$JOBS_FILE" \
            --rankset "$SHORTLIST_FILE" \
            --matrix "$MATRIX" --today "$TODAY" \
            --alerts "$ALERT_STORE" \
            --stage shortlist \
            > "$SHORTLIST_SUMMARY_FILE" 2>>"$LOG_FILE"; then
        # Fatal on purpose. The tempting fallback — rank the whole corpus — is the
        # 3.5h timeout this phase exists to prevent, and it would burn the run.
        log "FATAL: Phase 1b (shortlist) failed — refusing to fall back to ranking all $TOTAL_JOBS jobs (see log)"
        exit 1
    fi
fi

# No --deferred on the shortlist stage. The deferred list is written once, by the final
# stage, from the corpus — so it accounts for the jobs cut at *both* stages. A deferred
# file written here would be overwritten by a shorter, wronger one anyway.
if [[ "$RESUME" != "1" || ! -s "$RANKSET_FILE" ]]; then
    if [[ ! -s "$SHORTLIST_FILE" ]]; then
        log "FATAL: Phase 1b (shortlist) left no shortlist at $SHORTLIST_FILE"
        exit 1
    fi
    SHORTLIST_JOBS=$(python3 -c "import json; print(len(json.load(open('$SHORTLIST_FILE'))['results']))" 2>/dev/null || echo "0")
    log "Phase 1b (shortlist) complete: $SHORTLIST_JOBS of $TOTAL_JOBS jobs shortlisted for enrichment"
else
    # Resumed onto an existing rankset: no shortlist was built, and Phase 1c must not
    # spend LinkedIn requests. Zero is what keeps it skipped.
    SHORTLIST_JOBS=0
fi

# === Phase 1c: Enrich the shortlisted LinkedIn cards with their full descriptions ===
# Search results carry a 500-char snippet at most; ranking on that measures how much
# of the posting happened to fit in 500 characters. LinkedIn's `detail` returns the
# real text, so the top ENRICH_BUDGET shortlisted cards get it before the final cut.
# Better evidence, not a score bonus — nothing here awards points.
#
# Scoped to the SHORTLIST, and that ordering is the point. Enriching the rankset —
# what this phase used to do — spent the budget on jobs that had already survived on
# their titles, and so could not rescue the ones that needed it. Requests now go to
# alert cards first, then to the domain-only cards described above, then to the best
# title matches.
#
# Non-fatal by design: the ranker prompt already handles a missing description by
# working from the snippet and noting the thin evidence in `gaps`. A failure must not
# cost the whole run its ranking.
#
# ENRICH_BUDGET and LINKEDIN_DELAY are read inside the fetch branch, so they are unset
# on a resumed run. The RESUME test comes first for that reason, and the fallbacks
# repeat the defaults used where they are assigned.
if [[ "$RESUME" == "1" ]]; then
    log "Phase 1c: SKIPPED (RESUME=1) — no new LinkedIn requests; the rankset keeps whatever the earlier run enriched"
elif (( ${ENRICH_BUDGET:-0} > 0 )) && (( SHORTLIST_JOBS > 0 )); then
    log "Phase 1c: enriching up to $ENRICH_BUDGET of the $SHORTLIST_JOBS shortlisted LinkedIn cards with full descriptions..."
    if python3 scripts/enrich_linkedin.py --jobs "$SHORTLIST_FILE" \
            --matrix "$MATRIX" --delay "$LINKEDIN_DELAY" \
            > "$ENRICH_FILE" 2>>"$LOG_FILE"; then
        # Read the summary through a file and a quoted heredoc, never by
        # interpolating the script's output into a Python literal: the summary is
        # program output, and inlining it would let a stray quote break the parse.
        ENRICH_SUMMARY=$(python3 - "$ENRICH_FILE" "$WARN_FILE" <<'PYTHON_SCRIPT'
import json, sys
from pathlib import Path

try:
    s = json.loads(Path(sys.argv[1]).read_text())
except Exception as exc:
    print(f"summary unreadable ({exc})")
    sys.exit(0)

print(f"{s.get('enriched', 0)}/{s.get('targeted', 0)} enriched, "
      f"{s.get('alert_targets', 0)} alert-first, "
      f"{s.get('domain_only_targets', 0)} domain-only, "
      f"{s.get('failed', 0)} failed, {s.get('empty', 0)} empty, "
      f"{s.get('over_budget', 0)} over budget, "
      f"{s.get('already_seen', 0)} already known")

# A total wipeout means the detail endpoint is not answering. Surface it in the
# report: silence would read as "LinkedIn postings are short today".
if s.get("targeted", 0) > 0 and s.get("enriched", 0) == 0:
    with open(sys.argv[2], "a") as warn:
        warn.write(
            f"LinkedIn detail enrichment returned nothing for all "
            f"{s['targeted']} candidate cards — likely a block page or markup "
            "change. LinkedIn scores today rest on 500-char snippets, which is "
            "thinner evidence than usual.\n")
PYTHON_SCRIPT
)
        log "Phase 1c complete: $ENRICH_SUMMARY"
    else
        log "Phase 1c FAILED — every LinkedIn job will be ranked from its snippet (see log)"
        echo "LinkedIn detail enrichment failed to run; LinkedIn jobs were ranked from 500-char snippets only, so their scores rest on thinner evidence than usual." >> "$WARN_FILE"
    fi
else
    log "Phase 1c: skipped (enrichment budget ${ENRICH_BUDGET:-0}, $SHORTLIST_JOBS shortlisted jobs)"
fi

# === Phase 1b-final: Re-score the enriched shortlist and cut to the deep-rank budget ===
# The same scorer, run a second time on the same jobs — except that Phase 1c has now
# written real descriptions onto some of them, so the description axis finally has text
# to read. This is where a domain-only job earns its match from its body and overtakes
# a title that merely looked good.
#
# --corpus is what keeps the funnel honest. Stage 1 stamped `selected: true, "top of
# the shortlist"` onto ~80 corpus entries; this stage keeps ~25 of them and the report
# reads the corpus, so the ones cut here have to be told. With the join, one file
# accounts for all $TOTAL_JOBS fetched jobs and every deferral carries its reason.
# --deferred is written only here, for the same reason.
#
# Skipped wholesale on a resumed run: the rankset already exists, and the enrichment
# it carries was paid for in LinkedIn requests that must not be spent twice.
if [[ "$RESUME" == "1" && -s "$RANKSET_FILE" ]]; then
    log "Phase 1b-final: SKIPPED (RESUME=1) — reusing the rankset at $RANKSET_FILE"
else
    log "Phase 1b-final: re-scoring the $SHORTLIST_JOBS enriched shortlisted jobs and cutting to the deep-rank budget..."
    if ! python3 scripts/prerank_jobs.py --jobs "$SHORTLIST_FILE" \
            --corpus "$JOBS_FILE" \
            --rankset "$RANKSET_FILE" --deferred "$DEFERRED_FILE" \
            --matrix "$MATRIX" --today "$TODAY" \
            --alerts "$ALERT_STORE" \
            --stage final \
            > "$PRERANK_FILE" 2>>"$LOG_FILE"; then
        log "FATAL: Phase 1b-final failed — refusing to fall back to ranking all $SHORTLIST_JOBS shortlisted jobs (see log)"
        exit 1
    fi
fi

if [[ ! -s "$RANKSET_FILE" ]]; then
    log "FATAL: Phase 1b-final left no rankset at $RANKSET_FILE"
    exit 1
fi

SELECTED_JOBS=$(python3 -c "import json; print(len(json.load(open('$RANKSET_FILE'))['results']))" 2>/dev/null || echo "0")
log "Phase 1b-final complete: $SELECTED_JOBS of $TOTAL_JOBS jobs selected for deep ranking"
if (( SELECTED_JOBS == 0 )); then
    # Not fatal: zero selected is a legitimate answer when every fetched job was
    # already ranked on an earlier day. The run continues so the report still gets
    # written and still lists why each job was cut.
    log "Phase 1b-final: nothing selected — Phase 2 has no jobs to score"
    echo "Pre-ranking selected 0 of the $TOTAL_JOBS fetched jobs for deep ranking, so no job was scored today. The deferral reasons are listed below — if they are all \"already ranked in a previous run\" this is normal; anything else points at a vocabulary or config problem." >> "$WARN_FILE"
fi

# === Phase 2: Rank jobs via Claude Code ===
log "Phase 2: Ranking jobs via Claude Code..."

# Build the prompt with the file paths injected. Both placeholders must be
# substituted: the prompt writes its "matched but below the drafting gate" list to
# <NOT_DRAFTED_FILE_PATH>, so leaving it unsubstituted would create a file with that
# literal name and lose the list from the report.
RANK_PROMPT=$(cat prompts/pipeline_phase1_rank.md)
RANK_PROMPT="${RANK_PROMPT//<JOBS_FILE_PATH>/$RANKSET_FILE}"
RANK_PROMPT="${RANK_PROMPT//<NOT_DRAFTED_FILE_PATH>/$NOT_DRAFTED_FILE}"

if [[ "$RANK_PROMPT" == *"<JOBS_FILE_PATH>"* || "$RANK_PROMPT" == *"<NOT_DRAFTED_FILE_PATH>"* ]]; then
    log "FATAL: a placeholder in prompts/pipeline_phase1_rank.md was not substituted"
    exit 1
fi

# Run with timeout via background process (macOS has no timeout command)
#
# Two things here are load-bearing and easy to break:
#
#  1. The timeout branch sets a flag and returns; it must NOT fall through to the
#     `wait` below. It already reaped the PID, and `wait` on a reaped PID returns
#     non-zero — which under `set -e` aborts the entire script, skipping Phase 2b
#     through Phase 5 and leaving no report at all. That is exactly what happened
#     on the 2026-08-18 19:31 run.
#  2. The `wait` in the normal path is guarded with `|| RANK_EXIT=$?` for the same
#     reason: an unguarded `wait` on a child that exited non-zero aborts the script
#     before the "Phase 2 FAILED" branch below can run, making that branch dead code.
#     So this function reports through variables and always returns 0.
rank_attempt() {
    RANK_EXIT=0
    RANK_TIMED_OUT=0
    claude -p "$RANK_PROMPT" \
        --allowedTools "Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,Agent" \
        --output-format text < /dev/null 2>>"$RANK_ERR_FILE" > "$TOP5_FILE" &
    RANK_PID=$!

    local waited=0
    while kill -0 $RANK_PID 2>/dev/null; do
        sleep 5
        waited=$((waited + 5))
        if (( waited % 60 == 0 )); then
            log "  Phase 2 still running... (${waited}s elapsed, attempt ${RANK_ATTEMPT})"
        fi
        if (( waited >= RANK_TIMEOUT )); then
            log "Phase 2 TIMEOUT after ${RANK_TIMEOUT}s — killing process"
            kill $RANK_PID 2>/dev/null || true
            wait $RANK_PID 2>/dev/null || true
            RANK_TIMED_OUT=1
            return 0
        fi
    done
    wait $RANK_PID || RANK_EXIT=$?
    return 0
}

# Is another attempt worth the wait, or is this a wall?
#
# Retries fix transient gateway conditions — 429s, 5xx, overload, dropped sockets.
# They cannot fix a rejected credential or an exhausted balance; retrying those just
# delays the same empty report by a minute and buries the real cause under two more
# identical failures. So those are called out and *not* retried.
#
# An unrecognised failure is treated as transient on purpose. The 2026-08-23 failure
# this retry exists for wrote nothing to stderr at all, so a classifier that only
# retried known-transient markers would not have retried the one case that motivated
# it. Reads stdout too, since the CLI does not always put the error on stderr.
rank_error_class() {
    local text=""
    [[ -f "$RANK_ERR_FILE" ]] && text+=$(cat "$RANK_ERR_FILE")
    [[ -f "$TOP5_FILE" ]] && text+=$(cat "$TOP5_FILE")
    text=$(printf '%s' "$text" | tr '[:upper:]' '[:lower:]')
    case "$text" in
        *"401"*|*"403"*|*"invalid api key"*|*"invalid x-api-key"*|\
        *"authentication_error"*|*"unauthorized"*|*"permission_error"*|\
        *"credit balance"*|*"insufficient"*)
            echo "non-retryable (auth/quota)" ;;
        *"429"*|*"rate limit"*|*"overloaded"*|*"500"*|*"502"*|*"503"*|*"504"*|\
        *"econnreset"*|*"socket hang up"*|*"fetch failed"*|*"upstream"*)
            echo "transient" ;;
        *)  echo "unclassified" ;;
    esac
}

RANK_ATTEMPT=0
RANK_EXIT=0
RANK_TIMED_OUT=0
RANK_ERROR_CLASS=""

while (( RANK_ATTEMPT < RANK_ATTEMPTS )); do
    RANK_ATTEMPT=$((RANK_ATTEMPT + 1))
    if (( RANK_ATTEMPT > 1 )); then
        # Exponential from RANK_BACKOFF: 20s, 40s, 80s...
        RANK_SLEEP=$(( RANK_BACKOFF * (1 << (RANK_ATTEMPT - 2)) ))
        log "Phase 2: waiting ${RANK_SLEEP}s before attempt ${RANK_ATTEMPT} of ${RANK_ATTEMPTS}"
        sleep "$RANK_SLEEP"
        log "Phase 2: retrying (attempt ${RANK_ATTEMPT} of ${RANK_ATTEMPTS})"
    fi

    : > "$RANK_ERR_FILE"
    rank_attempt
    # Into the log whichever way it went, so no attempt's output is lost when the
    # next one truncates the file it was classified from.
    [[ -s "$RANK_ERR_FILE" ]] && cat "$RANK_ERR_FILE" >> "$LOG_FILE"

    if (( RANK_TIMED_OUT == 1 )); then
        # Deliberately not retried: the attempt already spent the full RANK_TIMEOUT
        # budget (1800s by default) and a second one would spend it again on the same
        # corpus. That calls for a smaller corpus or a bigger budget, not another try.
        log "Phase 2: not retrying a timeout — attempt ${RANK_ATTEMPT} used the full ${RANK_TIMEOUT}s budget"
        break
    fi
    if (( RANK_EXIT == 0 )); then
        break
    fi

    RANK_ERROR_CLASS=$(rank_error_class)
    log "Phase 2 attempt ${RANK_ATTEMPT} of ${RANK_ATTEMPTS} failed (exit ${RANK_EXIT}, ${RANK_ERROR_CLASS})"
    if [[ -s "$RANK_ERR_FILE" ]]; then
        log "  ranker stderr, last 3 lines: $(tail -3 "$RANK_ERR_FILE" | tr '\n' ' ')"
    else
        log "  ranker wrote nothing to stderr — the same signature as the 2026-08-23 08:11 failure"
    fi
    if [[ "$RANK_ERROR_CLASS" == non-retryable* ]]; then
        log "Phase 2: not retrying — this error class does not clear on its own"
        break
    fi
done

if (( RANK_TIMED_OUT == 1 )); then
    # Whatever reached $TOP5_FILE is a truncated stream, not JSON. Overwrite it so the
    # gate evaluates nothing rather than half a record — and warn, because a ranker
    # that was killed must never read as "nothing qualified today".
    RANKED_COUNT=0
    echo "[]" > "$TOP5_FILE"
    echo "Ranking did not finish within ${RANK_TIMEOUT}s and was stopped, so no jobs were scored and no CVs or cover letters were generated. It was not retried: a second attempt would spend the same ${RANK_TIMEOUT}s on the same corpus. The $TOTAL_JOBS fetched jobs are unaffected. Either shrink the corpus handed to the ranker or raise RANK_TIMEOUT." >> "$WARN_FILE"
elif (( RANK_EXIT == 0 )); then
    RANKED_COUNT=$(python3 -c "
import json, sys
sys.path.insert(0, 'scripts')
from gate_jobs import extract_array
data = extract_array(open('$TOP5_FILE').read())
print(len(data) if data is not None else 0)
" 2>>"$LOG_FILE" || echo "0")
    if (( RANK_ATTEMPT > 1 )); then
        log "Phase 2 complete on attempt ${RANK_ATTEMPT} of ${RANK_ATTEMPTS} after $((RANK_ATTEMPT - 1)) failed attempt(s): $RANKED_COUNT jobs returned by the ranker"
        echo "Ranking failed $((RANK_ATTEMPT - 1)) time(s) and then succeeded on attempt ${RANK_ATTEMPT}, so the $RANKED_COUNT scores below are complete and nothing was lost. Noted because a gateway that needs retries today may need more than ${RANK_ATTEMPTS} tomorrow — see $RANK_ERR_FILE for what the failed attempts said." >> "$WARN_FILE"
    else
        log "Phase 2 complete: $RANKED_COUNT jobs returned by the ranker"
    fi
else
    RANKED_COUNT=0
    # Keep the failing stdout before overwriting it. The 2026-08-23 run replaced the
    # only copy of the ranker's output with "[]", which is why that failure had to be
    # diagnosed from a timestamp and an exit code.
    cp "$TOP5_FILE" "$RANK_FAIL_FILE" 2>/dev/null || true
    echo "[]" > "$TOP5_FILE"
    if [[ "$RANK_ERROR_CLASS" == non-retryable* ]]; then
        log "Phase 2 FAILED (exit $RANK_EXIT) on attempt ${RANK_ATTEMPT} — ${RANK_ERROR_CLASS}, not retried — skipping drafting"
        echo "The ranking step failed with an error retries cannot fix: exit $RANK_EXIT, classified ${RANK_ERROR_CLASS}. It was stopped after attempt ${RANK_ATTEMPT} of ${RANK_ATTEMPTS} on purpose rather than retried, because a rejected credential or an exhausted balance does not clear on its own. No jobs were scored and no documents were generated; the $TOTAL_JOBS fetched jobs are unaffected. Check the gateway credential, then re-run with RESUME=1 to rank without re-querying the portals. Ranker output: $RANK_ERR_FILE and $RANK_FAIL_FILE." >> "$WARN_FILE"
    else
        log "Phase 2 FAILED (exit $RANK_EXIT) after all ${RANK_ATTEMPT} attempts (last classified ${RANK_ERROR_CLASS:-unclassified}) — skipping drafting"
        echo "The ranking step failed on all ${RANK_ATTEMPT} attempts, the last with exit $RANK_EXIT classified ${RANK_ERROR_CLASS:-unclassified}, so no jobs were scored and no documents were generated. Retries were spent, not skipped — this was not a one-off hiccup. The $TOTAL_JOBS fetched jobs are unaffected; re-run with RESUME=1 to retry the ranking without re-querying the portals. Ranker output: $RANK_ERR_FILE and $RANK_FAIL_FILE." >> "$WARN_FILE"
    fi
fi

# === Phase 2b: Enforce the document-generation gate ===
# The ranker is *told* the gate (score >= 75, or >= 60 when LinkedIn's own alert
# surfaced the job) but a prompt is an instruction, not an enforcement. Re-applying
# it in code means a miscount or a hallucinated `alert_matched: true` cannot cause a
# CV to be written for a job that never qualified. alert_matched.json is the
# authority, and its 30-day expiry is applied here — an alert from three months ago
# must stop widening the gate.
#
# Rejections are appended to $NOT_DRAFTED_FILE, so a near-miss still shows up in the
# report's "Matched but Not Drafted" table instead of vanishing.
GATE_FILE="/tmp/jobsearch_gate_summary_${TODAY}.json"
if python3 scripts/gate_jobs.py \
        --jobs "$TOP5_FILE" \
        --not-drafted "$NOT_DRAFTED_FILE" \
        --today "$TODAY" \
        > "$GATE_FILE" 2>>"$LOG_FILE"; then
    TOP5_COUNT=$(python3 -c "
import json
print(json.load(open('$GATE_FILE')).get('cleared', 0))
" 2>>"$LOG_FILE" || echo "0")
    python3 - "$GATE_FILE" <<'PYTHON_SCRIPT' >> "$LOG_FILE" 2>&1
import json, sys
s = json.load(open(sys.argv[1]))
print(f"[gate] {s['cleared']}/{s['ranker_returned']} cleared "
      f"(>= {s['strong_score']}, or >= {s['min_score']} if alert-matched); "
      f"{s['gate_rejected']} rejected, {s['over_cap']} over the cap of {s['cap']}; "
      f"alerts: {s['alert_live']} live, {s['alert_expired']} expired, "
      f"{s['alert_undated']} undated; "
      f"{s['alert_claim_corrected']} alert claims corrected")
PYTHON_SCRIPT
    log "Phase 2b complete: $TOP5_COUNT jobs cleared the drafting gate"
else
    # The gate exits non-zero only when the ranker's output could not be parsed at
    # all. That is not the same event as "nothing qualified", so it must be visible
    # in the report rather than reading as a quiet zero.
    TOP5_COUNT=0
    log "Phase 2b: the ranker's output was not parseable — no documents will be drafted"
    echo "The ranking step did not return usable JSON, so the drafting gate had nothing to evaluate and no CVs or cover letters were generated. The fetched-jobs list is unaffected; see the log for the raw output." >> "$WARN_FILE"
fi

# === Phase 3: Hand the ranked list to the Telegram selector ===
# This run does NOT write CVs or cover letters. It offers the ranked list on
# Telegram and stops; documents are generated later, for the jobs Salman actually
# picks, by scripts/selector_listener.py.
#
# Why the split. Auto-drafting produced documents for whatever cleared the gate,
# which is a decision the gate is not qualified to make — "score >= 75" is a
# shortlist heuristic, not an intent to apply. So the gate's verdicts stay in the
# report as ranking information, and the choice of what to apply for becomes an
# explicit human one.
#
# Why launchctl rather than running the listener here. The listener has to be
# alive when the button is pressed, and that can be eight hours from now. Holding
# this process open for it would keep the lock dir, the log and the launchd job
# itself occupied all day, and would put a 20h timeout inside a run that is
# supposed to finish in minutes. So the listener is a separate launchd job that
# this phase starts and hands the rankset to.
#
# The offered list is the full rankset, not $TOP5_FILE: the point of choosing by
# hand is to see the jobs the gate rejected too.
if (( SELECTED_JOBS > 0 )); then
    log "Phase 3: offering $SELECTED_JOBS ranked job(s) on Telegram for selection"

    # The handoff. `launchctl kickstart` takes no arguments, so the rankset path
    # and the date travel in this file instead of on a command line. The listener
    # posts nothing without it, which is what keeps the selector job safe to have
    # loaded: KeepAlive starts it whenever launchd feels like it (a bootstrap, a
    # reboot, a crash restart), and every one of those starts must be a no-op
    # unless a selection is genuinely pending.
    PENDING_FILE="/tmp/jobsearch_pending_selection.json"
    printf '{"today": "%s", "rankset": "%s"}\n' "$TODAY" "$RANKSET_FILE" > "$PENDING_FILE"

    # kickstart -k, not `launchctl start`: -k kills an already-running instance
    # first. Without it, yesterday's listener still holding the token inside its
    # 20h window would keep today's list from ever being sent, and the two would
    # fight over getUpdates. Exit is tolerated because a missing or unloaded
    # selector job must cost the run its report, not its ranking.
    SELECTOR_LABEL="gui/$(id -u)/com.salman.jobsearch.selector"
    if launchctl kickstart -k "$SELECTOR_LABEL" 2>>"$LOG_FILE"; then
        log "Phase 3 complete: selector started; awaiting selection on Telegram"
    else
        log "Phase 3 FAILED: could not start $SELECTOR_LABEL"
        echo "The ranked list could not be sent for selection: launchctl could not start $SELECTOR_LABEL. The ranking is intact in this report, and \`launchctl kickstart -k $SELECTOR_LABEL\` will send the list once the job is loaded. Until then no CVs or cover letters will be produced." >> "$WARN_FILE"
    fi
else
    log "Phase 3: skipped (no ranked jobs to offer)"
    # No pending selection, so retract any marker still naming a previous day's
    # rankset. Otherwise a crash restart of the selector would resume a list that
    # today's run has already decided not to offer.
    rm -f /tmp/jobsearch_pending_selection.json
fi

# Phases 4-6 read this. The selector writes documents on its own schedule, hours
# after this process is gone, so at this point the document set is always empty —
# the same shape these phases already handle on a day when nothing clears.
echo '{"jobs":[],"errors":[]}' > "$APPLICABLE_FILE"

# === Phase 4: QA Review via Claude Code ===
DOCS_GENERATED=$(python3 -c "
import json
try:
    data = json.load(open('$APPLICABLE_FILE'))
    jobs = data.get('jobs', [])
    count = sum(1 for j in jobs if j.get('cv_file') or j.get('cover_letter_file'))
    print(count)
except:
    print(0)
" 2>/dev/null || echo "0")

if (( DOCS_GENERATED > 0 )); then
    log "Phase 4: QA review of $DOCS_GENERATED document sets..."

    QA_PROMPT=$(cat prompts/pipeline_phase3_qa.md)
    QA_PROMPT="${QA_PROMPT//<APPLICABLE_FILE_PATH>/$APPLICABLE_FILE}"

    if claude -p "$QA_PROMPT" \
        --allowedTools "Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,Agent" \
        --output-format text < /dev/null 2>>"$LOG_FILE" > "$QA_FILE"; then
        log "Phase 4 complete"
    else
        log "Phase 4 FAILED (exit $?)"
        echo '{"reviews":[],"errors":["Phase 4 failed"]}' > "$QA_FILE"
    fi
else
    log "Phase 4: skipped (no documents to review)"
    echo '{"reviews":[],"errors":[]}' > "$QA_FILE"
fi

# === Phase 5: Generate Report ===
log "Phase 5: Generating report..."

python3 - "$TODAY" "$JOBS_FILE" "$TOP5_FILE" "$APPLICABLE_FILE" "$QA_FILE" "$REPORT_FILE" \
    "$NOT_DRAFTED_FILE" "$WARN_FILE" "$PRERANK_FILE" "$ALERT_JOBS_FILE" <<'PYTHON_SCRIPT'
import json
import re
import sys
from pathlib import Path
from datetime import date

def load_json_from_file(path):
    """Load JSON from a file that may contain text output with code fences."""
    p = Path(path)
    if not p.exists():
        print(f"DEBUG: {path} does not exist", file=sys.stderr)
        return None
    raw = p.read_text().strip()
    print(f"DEBUG: {path} size={len(raw)} chars", file=sys.stderr)
    if len(raw) < 500:
        print(f"DEBUG: {path} content: {raw[:500]}", file=sys.stderr)
    # Try direct JSON parse first
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, FileNotFoundError):
        pass
    # Extract JSON from text (may be wrapped in ```json ... ``` code fences)
    try:
        match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', raw)
        if match:
            return json.loads(match.group(1))
    except (json.JSONDecodeError, Exception):
        pass
    # Try to find any JSON array or object
    try:
        match = re.search(r'(\[[\s\S]*\]|\{[\s\S]*\})', raw)
        if match:
            return json.loads(match.group(1))
    except (json.JSONDecodeError, Exception):
        pass
    return None

today = sys.argv[1]
jobs_file = Path(sys.argv[2])
top5_file = Path(sys.argv[3])
applicable_file = Path(sys.argv[4])
qa_file = Path(sys.argv[5])
report_file = Path(sys.argv[6])
not_drafted_file = Path(sys.argv[7])
warn_file = Path(sys.argv[8])
# Phase 1b's summary. Optional on purpose: sys.argv[9] is absent if an older caller
# invokes this block, and the funnel section is simply omitted rather than crashing
# the only step that tells Salman what happened today.
prerank_file = Path(sys.argv[9]) if len(sys.argv) > 9 else None
# Phase 0b's portal output, read only for the per-alert breakdown below. Optional for
# the same reason, and absent whenever Phase 0b was skipped or failed.
alert_jobs_file = Path(sys.argv[10]) if len(sys.argv) > 10 else None

# Load data
try:
    jobs_data = json.load(open(jobs_file))
except:
    jobs_data = {"meta": {"unique": 0}, "results": []}

top5_data = load_json_from_file(top5_file)
if isinstance(top5_data, list):
    top5_jobs = top5_data
elif isinstance(top5_data, dict):
    top5_jobs = top5_data.get("results", top5_data.get("jobs", []))
else:
    top5_jobs = []

app_data = load_json_from_file(applicable_file)
app_jobs = app_data.get("jobs", []) if app_data else []

qa_data = load_json_from_file(qa_file)
qa_reviews = qa_data.get("reviews", []) if qa_data else []

# Jobs that scored >= 60 but missed the drafting gate. Reported, never silently
# dropped: Salman decides whether to draft one by hand with /apply.
not_drafted = load_json_from_file(not_drafted_file)
if not isinstance(not_drafted, list):
    not_drafted = []

# Phase 1 fetch warnings (e.g. a total LinkedIn shutout). A quiet "0 jobs" would
# read as an empty market rather than a broken fetch, so these must reach the report.
pipeline_warnings = []
if warn_file.exists():
    pipeline_warnings = [ln.strip() for ln in warn_file.read_text().splitlines() if ln.strip()]

# Phase 1b's funnel. The pipeline fetches broadly and deep-ranks a small slice, so
# without this the report would show "504 fetched, 3 drafted" and give no way to tell
# a thin market from a pre-rank that cut the wrong jobs.
prerank = {}
if prerank_file is not None and prerank_file.exists():
    try:
        prerank = json.loads(prerank_file.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"DEBUG: prerank summary unreadable ({exc})", file=sys.stderr)

# Every fetched job carries its own prerank annotation, so the deferral reasons come
# from the corpus rather than a second file — one source, no chance of disagreement.
deferred = [j for j in jobs_data.get("results", [])
            if isinstance(j, dict) and not (j.get("prerank") or {}).get("selected")]

# Phase 0b's cards, grouped by which of Salman's alerts produced them. The per-portal
# breakdown below already shows a `linkedin-alert` total, but not *which* alert earned
# it — and that is the actionable part: an alert returning nothing for weeks wants
# rewording in LinkedIn, and one flooding the corpus with off-target roles wants
# narrowing. alert_name/alert_track live only in this file; aggregate_jobs.py's
# normalize_job() drops them when it rebuilds each result into the unified schema.
alert_results = []
if alert_jobs_file is not None and alert_jobs_file.exists():
    alert_payload = load_json_from_file(alert_jobs_file)
    if isinstance(alert_payload, dict) and isinstance(alert_payload.get("results"), list):
        alert_results = [r for r in alert_payload["results"] if isinstance(r, dict)]

alert_by_name = {}
for card in alert_results:
    name = card.get("alert_name") or "(unattributed)"
    entry = alert_by_name.setdefault(name, {"count": 0, "track": card.get("alert_track")})
    entry["count"] += 1
    if entry["track"] is None:
        entry["track"] = card.get("alert_track")

# Build report
lines = []
lines.append(f"# Daily Pipeline Report - {today}")
lines.append("")

# Warnings go first: a fetch failure changes how every number below should be read.
if pipeline_warnings:
    lines.append("## ⚠ Warnings")
    for w in pipeline_warnings:
        lines.append(f"- {w}")
    lines.append("")

lines.append("## Summary")
lines.append(f"- **Jobs fetched:** {jobs_data['meta'].get('unique', 'unknown')} unique from {len(jobs_data.get('meta', {}).get('portals', {}))} portals")
if prerank:
    lines.append(f"- **Selected for deep ranking:** {prerank.get('selected', 0)} "
                 f"(pre-rank budget {prerank.get('budget', '?')} + "
                 f"{prerank.get('alert_budget', '?')} alert slots)")
lines.append(f"- **Cleared the drafting gate:** {len(top5_jobs)} (score ≥ 75, or ≥ 60 if LinkedIn alert-matched)")
lines.append(f"- **Matched but not drafted:** {len(not_drafted)} (scored ≥ 60, below the gate)")
lines.append(f"- **Applications generated:** {len([j for j in app_jobs if j.get('cv_file')])}")
lines.append(f"- **QA reviews:** {len(qa_reviews)}")

# Per-portal breakdown, so a portal that quietly stopped returning results is visible
portals = jobs_data.get("meta", {}).get("portals", {})
if portals:
    lines.append("")
    lines.append("### Where they came from")
    for portal, count in sorted(portals.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {portal}: {count}")

# Which alert earned its keep. Priority here is attention, not approval: these jobs
# claim reserved pre-rank slots and get their descriptions fetched first, then they are
# scored on merit like everything else.
if alert_by_name:
    alert_corpus = [j for j in jobs_data.get("results", [])
                    if isinstance(j, dict) and j.get("portal") == "linkedin-alert"]
    alert_ranked = [j for j in alert_corpus if (j.get("prerank") or {}).get("selected")]
    lines.append("")
    lines.append("### From your LinkedIn alerts")
    lines.append(f"{len(alert_results)} card(s) read from the alert mailbox — "
                 f"{len(alert_corpus)} in the corpus, {len(alert_ranked)} deep-ranked.")
    for name, entry in sorted(alert_by_name.items(), key=lambda kv: -kv[1]["count"]):
        if name == "(unattributed)":
            track = "matched no configured alert name"
        else:
            track = entry["track"] or "no track — add it to alerts.track_map"
        lines.append(f"- {name} ({track}): {entry['count']}")
    # Promised in config/search_matrix.json's _track_map_comment: an unmapped alert
    # still has its jobs ingested, but the config gap is surfaced rather than hidden.
    unmapped = [n for n, e in alert_by_name.items()
                if not e["track"] and n != "(unattributed)"]
    if unmapped:
        lines.append(f"- ⚠ Not in `alerts.track_map`, so these earn no track credit "
                     f"in pre-ranking: {', '.join(sorted(unmapped))}")
    if "(unattributed)" in alert_by_name:
        lines.append("- ⚠ Some cards matched no configured alert name. If this is not "
                     "a new alert you created in LinkedIn, it may mean the email "
                     "layout changed — check `alerts.track_map` against LinkedIn.")

# === Pre-rank funnel ===
# Deferred, never dropped. The model ranker costs ~24s per job, so only a slice of the
# corpus is scored — but every job that was not scored is accounted for here with the
# reason, and the near-misses are named so a budget that is set too low is obvious.
if deferred:
    reasons = {}
    for job in deferred:
        reason = (job.get("prerank") or {}).get("reason") or "no reason recorded"
        reasons[reason] = reasons.get(reason, 0) + 1

    lines.append("")
    lines.append(f"## Not Deep-Ranked ({len(deferred)})")
    lines.append("")
    lines.append("Fetched but not scored by the model. This is a *selection* result, "
                 "not a fit judgement — none of these were rejected on merit.")
    lines.append("")
    for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
        lines.append(f"- **{count}** — {reason}")

    # The highest-scoring deferrals: if a genuinely strong role sits here, that is the
    # signal to raise prerank.deep_rank_budget. Two reasons are excluded because
    # raising the budget would not help either one — an already-ranked job was
    # evaluated on an earlier day, and a near-duplicate's role is already in today's
    # rankset under a different URL. Both stay counted in the histogram above.
    skip = ("already ranked", "near-duplicate")
    near = [j for j in deferred
            if (j.get("prerank") or {}).get("score")
            and not any(s in ((j.get("prerank") or {}).get("reason") or "")
                        for s in skip)]
    near.sort(key=lambda j: -(j["prerank"].get("score") or 0))
    if near:
        lines.append("")
        lines.append("### Closest misses")
        lines.append("")
        lines.append("| Pre-rank | Track | Title | Company | Location |")
        lines.append("|----------|-------|-------|---------|----------|")
        for job in near[:10]:
            p = job["prerank"]
            lines.append(f"| {p.get('score', '—')} | {p.get('track_guess') or '—'} | "
                         f"{job.get('title', '—')} | {job.get('company', '—')} | "
                         f"{job.get('location') or '—'} |")
        lines.append("")
        lines.append("*Raise `prerank.deep_rank_budget` in `config/search_matrix.json` "
                     "to send more of these to the ranker (~24s each).*")

# === Hard-gate verification of the deep-ranked set ===
# The fail-open this section exists to expose: hard_gates.evaluate() used to accept a
# ~500-character search-card snippet as evidence, and the two gates that can pass on
# silence — language and experience — only tested that string for truthiness. So a job
# nobody had read came back PASS, indistinguishable in every surface from one whose
# body had actually been fetched. On the 2026-08-23 rankset that was 11 of 25 rows.
#
# Unverified rows are NOT excluded: they keep their slot here and in the Telegram list,
# because absence of evidence is not evidence of a problem. But the label has to be
# visible, since "cleared the gates" and "was never checked" are different claims and
# only one of them justifies spending a CV on trust.
selected = [j for j in jobs_data.get("results", [])
            if isinstance(j, dict) and (j.get("prerank") or {}).get("selected")]


def gate_label(job):
    """PASS / FAIL / UNVERIFIED for one row, naming the evidence behind it."""
    g = (job.get("prerank") or {}).get("gates") or {}
    overall = g.get("overall")
    if overall == "PASS":
        return "✅ pass"
    if overall == "FAIL":
        return f"❌ fail ({', '.join(g.get('failed') or []) or 'unspecified'})"
    if overall != "UNKNOWN":
        return "— not gated"
    # evidence_source postdates artifacts written before the fail-open fix; for those,
    # evidence_chars is the only provenance signal there is.
    source = g.get("evidence_source")
    if source is None:
        source = "description_snippet" if g.get("evidence_chars") else "none"
    if source == "description":
        return "⚠️ unverified"
    if source == "description_truncated":
        # A fetched body cut at its length limit. Long, but it ends mid-sentence and
        # a posting's requirements section is routinely below the cut, so it cannot
        # acquit. Naming the cut keeps this apart from "no posting text" below.
        return f"⚠️ unverified (body cut at {g.get('evidence_chars', 0)} chars)"
    if source == "description_snippet":
        return f"⚠️ unverified (snippet only, {g.get('evidence_chars', 0)} chars)"
    return "⚠️ unverified (no posting text)"


if selected:
    verified = [j for j in selected
                if ((j.get("prerank") or {}).get("gates") or {}).get("overall") == "PASS"]
    unverified = [j for j in selected
                  if ((j.get("prerank") or {}).get("gates") or {}).get("overall") == "UNKNOWN"]
    lines.append("")
    lines.append(f"## Gate Verification of the Deep-Ranked Set ({len(selected)})")
    lines.append("")
    lines.append(f"**{len(verified)} of {len(selected)} verified.** Verified means a "
                 "posting body was fetched and read and it stated no blocker. The other "
                 f"{len(unverified)} are *unverified*: nothing disqualifying was found, "
                 "but nothing was read either. They are ranked and offered as normal — "
                 "treat their gate status as unknown, not as cleared.")
    lines.append("")
    lines.append("| Pre-rank | Gate | Track | Title | Company | Location |")
    lines.append("|----------|------|-------|-------|---------|----------|")
    for job in sorted(selected,
                      key=lambda j: -((j.get("prerank") or {}).get("score") or 0)):
        p = job.get("prerank") or {}
        lines.append(f"| {p.get('score', '—')} | {gate_label(job)} | "
                     f"{p.get('track_guess') or '—'} | {job.get('title', '—')} | "
                     f"{job.get('company', '—')} | {job.get('location') or '—'} |")
    if unverified:
        lines.append("")
        lines.append(f"*{len(unverified)} row(s) could not be verified. Enrichment is "
                     "capped by `linkedin.detail_enrich_budget` and can only fetch "
                     "LinkedIn cards, so non-LinkedIn rows stay unverified however high "
                     "that budget goes.*")

# Top 5 table
if top5_jobs:
    lines.append("")
    lines.append("## Cleared the Drafting Gate")
    lines.append("")
    lines.append("| # | Score | Verdict | Track | Title | Company | Location | Gate | Status |")
    lines.append("|---|-------|---------|-------|-------|---------|----------|------|--------|")
    for i, job in enumerate(top5_jobs[:5], 1):
        score = job.get("score", job.get("rank_score", "—"))
        verdict = job.get("verdict", job.get("rank_verdict", "—"))
        track = job.get("track", "—")
        title = job.get("title", "—")
        company = job.get("company", "—")
        location = job.get("location", "—")
        gate = "alert 🔔" if job.get("alert_matched") else job.get("gate_reason", "—")
        status = "Drafted" if any(j.get("company") == company and j.get("title") == title for j in app_jobs) else "Pending"
        lines.append(f"| {i} | {score} | {verdict} | {track} | {title} | {company} | {location} | {gate} | {status} |")

# Matched but deliberately not drafted — visible so a near-miss isn't lost silently
if not_drafted:
    lines.append("")
    lines.append("## Matched but Not Drafted")
    lines.append("")
    lines.append("Scored 60 or above but below the drafting gate. Draft one by hand with `/apply <url>`.")
    lines.append("")
    lines.append("| Score | Verdict | Track | Title | Company | Location | URL |")
    lines.append("|-------|---------|-------|-------|---------|----------|-----|")
    for job in sorted(not_drafted, key=lambda j: -(j.get("score") or 0)):
        url = job.get("url", "")
        link = f"[Link]({url})" if url else "—"
        lines.append(
            f"| {job.get('score', '—')} | {job.get('verdict', '—')} | {job.get('track', '—')} "
            f"| {job.get('title', '—')} | {job.get('company', '—')} "
            f"| {job.get('location', '—')} | {link} |"
        )

# Documents table
if app_jobs:
    lines.append("")
    lines.append("## Documents Generated")
    lines.append("")
    lines.append("| Company | Role | CV | Cover Letter | QA |")
    lines.append("|---------|------|-----|-------------|-----|")
    for job in app_jobs:
        company = job.get("company", "—")
        role = job.get("title", "—")
        cv = "✓" if job.get("cv_file") else "✗"
        cl = "✓" if job.get("cover_letter_file") else "✗"
        qa = "Pass" if job.get("qa_pass", False) else "—" if not qa_reviews else "Check"
        lines.append(f"| {company} | {role} | {cv} | {cl} | {qa} |")

# Errors
errors = []
try:
    app_errors = app_data.get("errors", [])
    qa_errors = qa_data.get("errors", [])
    errors.extend(app_errors)
    errors.extend(qa_errors)
except:
    pass

if errors:
    lines.append("")
    lines.append("## Errors / Warnings")
    for e in errors:
        lines.append(f"- {e}")

lines.append("")
lines.append("---")
lines.append(f"*Generated at {date.today().isoformat()} by the automated job search pipeline.*")

with open(report_file, "w") as f:
    f.write("\n".join(lines))

print(f"Report written to {report_file}")
PYTHON_SCRIPT

log "Phase 5 complete: $REPORT_FILE"

# === Phase 6: retired ===
# The digest used to be mailed here, with the generated PDFs attached. Both halves
# of that stopped making sense once selection moved to Telegram: the documents no
# longer exist when this process ends (the selector writes them later), so the
# attachment list would always be empty, and the notification path is now the
# Telegram ping from the EXIT trap plus the selection list itself.
#
# scripts/send_email.py is kept, and so is the email block in
# config/automation.json — not as a fallback for the digest, but because Phase 0b
# reads the LinkedIn job alerts over IMAP with that same Gmail app password
# (scripts/linkedin_alerts.py resolves email.smtp_user / email.smtp_password).
# Removing the credential would cost the run its alert-sourced jobs.

# === Phase 7: Cleanup ===
if [[ "$KEEP_TEMP" == "1" ]]; then
    log "Cleanup: skipped (KEEP_TEMP=1). Artifacts kept for inspection:"
    log "  fetched jobs:  $JOBS_FILE"
    log "  shortlist:     $SHORTLIST_FILE"
    log "  shortlist stats: $SHORTLIST_SUMMARY_FILE"
    log "  deep-rank set: $RANKSET_FILE"
    log "  deferred:      $DEFERRED_FILE"
    log "  prerank stats: $PRERANK_FILE"
    log "  cleared gate:  $TOP5_FILE"
    log "  not drafted:   $NOT_DRAFTED_FILE"
    log "  gate summary:  $GATE_FILE"
    log "  search plan:   $PLAN_FILE"
else
    log "Cleaning up temp files..."
    rm -f /tmp/jobsearch_portal_*_${TODAY}.json
    rm -f "$PLAN_FILE" "$JOBS_FILE" "$TOP5_FILE" "$NOT_DRAFTED_FILE" "$WARN_FILE" "$ENRICH_FILE" "$GATE_FILE"
    rm -f "$SHORTLIST_FILE" "$SHORTLIST_SUMMARY_FILE"
    # $RANKSET_FILE is deliberately NOT deleted here. Phase 3 handed it to the
    # selector, which reads it when the buttons are pressed — possibly hours from
    # now, and again after a crash restart. Deleting it would leave the listener
    # with nothing to offer and no way to recover.
    rm -f "$DEFERRED_FILE" "$PRERANK_FILE"
    rm -f "$APPLICABLE_FILE" "$QA_FILE"
    rm -rf "$APP_PACKAGES_DIR"
    # The ranker's stderr and its failed stdout are kept when Phase 2 did not
    # succeed: the warning in today's report names both files, so deleting them here
    # would publish a pointer to nothing. ${VAR:-0} because set -u is on and an
    # early-phase abort can reach cleanup without Phase 2 ever assigning them.
    if (( ${RANK_EXIT:-0} == 0 )) && (( ${RANK_TIMED_OUT:-0} == 0 )); then
        rm -f "$RANK_ERR_FILE" "$RANK_FAIL_FILE"
    else
        log "  ranker output kept for diagnosis: $RANK_ERR_FILE $RANK_FAIL_FILE"
    fi
    # Instead, retire the ones from previous days: their windows are long closed,
    # and without this the rankset and selection files would accumulate in /tmp
    # forever now that today's survives the run.
    find /tmp -maxdepth 1 -name 'jobsearch_rankset_*.json' \
        -not -name "jobsearch_rankset_${TODAY}.json" -mtime +1 -delete 2>/dev/null || true
    find /tmp -maxdepth 1 -name 'jobsearch_selection_*.json' \
        -not -name "jobsearch_selection_${TODAY}.json" -mtime +1 -delete 2>/dev/null || true
    find /tmp -maxdepth 1 -name 'jobsearch_selected_*.json' -mtime +1 -delete 2>/dev/null || true
    # A kept ranker log outlives its run by design; age out the older ones so they do
    # not accumulate one per failed day.
    find /tmp -maxdepth 1 -name 'jobsearch_rank_stderr_*.log' -mtime +3 -delete 2>/dev/null || true
    find /tmp -maxdepth 1 -name 'jobsearch_rank_failed_stdout_*.txt' -mtime +3 -delete 2>/dev/null || true
fi

log "Pipeline complete."
log "=== End of Run ==="
