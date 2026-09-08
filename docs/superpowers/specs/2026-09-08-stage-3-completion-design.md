# Stage 3 Completion Design — Telegram Control Plane and Pipeline Integration

**Date:** 2026-09-08  
**Status:** Approved design  
**Scope:** Complete the 12 pending items recorded in `docs/STAGE_3_HANDOFF.md`

## Goal

Complete the on-demand job-search control plane without rewriting the existing Stage 1/2
pipeline. Manual Telegram runs and scheduled runs must share one execution path, preserve
existing safety gates and retry behavior, and provide durable, testable progress and failure
reporting.

The work is delivered in six dependent slices:

1. pure progress tracking and Telegram rendering;
2. subprocess orchestration, run identity, manifests, and observability;
3. Telegram commands and persistent scheduling;
4. run-scoped document outputs and pipeline integration;
5. optional authenticated LinkedIn Playwright fallback;
6. verification, documentation, and release.

## Decisions and constraints

- `scripts/run_daily.sh` remains the pipeline worker. Stage 3 supervises it rather than
  duplicating its phases.
- Manual `/run` and scheduled launches call the same orchestrator API.
- Concurrent runs are refused with a clear message; there is no queue.
- Standard five-field cron expressions are used for recurring schedules, interpreted in the
  configured local timezone.
- Existing Stage 1/2 behavior remains compatible unless a change is required at an explicit
  integration seam.
- Telegram callback payloads remain under the 64-byte limit and contain short identifiers,
  never paths or secrets.
- Credentials, cookies, browser storage state, job descriptions, prompts, and full error dumps
  never enter argv, logs, manifests, Telegram messages, or repository files.
- Existing complete legacy CV/cover-letter outputs are detected and skipped, never moved,
  deleted, or overwritten automatically.
- PDFs remain disk-only and are not sent as Telegram documents.
- The known global `.claude/settings.json` security-guard failure remains a pre-existing test
  baseline and is not mixed into Stage 3 regression results.

## Slice 1: progress model and rendering

### `stage_3/progress.py`

Implement a pure log-to-state model. The parser recognizes the existing log format:

```python
LOG_LINE = re.compile(r"^\[(?P<clock>\d{2}:\d{2}:\d{2})\]\s*(?P<message>.*)$")
PHASE_ID = re.compile(
    r"^Phase\s+(?P<phase>1b-final|0b|1b|1c|2b|1|2|3|4|5)\b(?P<rest>.*)$"
)
```

The `1b-final` alternative precedes `1b` and `1`; otherwise distinct phases can collapse.
Use the approved phase model and weights:

| phase | weight | icon |
|---|---:|---|
| `0b` | 4 | 📬 |
| `1` | 22 | 🔍 |
| `1b` | 3 | ✂️ |
| `1c` | 12 | 📖 |
| `1b-final` | 3 | 🎯 |
| `2` | 38 | 🧠 |
| `2b` | 2 | 🚦 |
| `3` | 5 | 📱 |
| `4` | 6 | 🔬 |
| `5` | 5 | 📝 |

Weights sum to 100. A running phase contributes half its weight and the computed percentage
is capped at 99 until the exact `Pipeline complete.` marker is observed. Earlier pending
phases are implicitly completed when a later phase is entered because the shell log does not
always emit explicit finish lines.

Expose frozen `Phase`, `PhaseState`, and `RunState` values, status constants, phase lookup
constants, `ProgressTracker`, `parse_log_line`, and `replay(text)`. Track counts and metadata
for fetched, reused, shortlisted, deep-ranked, ranker-returned, drafting-cleared,
re-included, geo, fallback, skip, failure, timeout, and cancellation states.

Recognize the pipeline's actual count phrases with anchored regular expressions, and make
`_absorb_phase` classify skipped, failed, and timed-out phases before generic phase entry.

Track the last meaningful line timestamp. A caller can query whether a phase has had no new
activity for 120 seconds. The tracker itself remains pure: it emits no network messages and
only provides the data needed for a single warning event every two minutes while stalled.

### `stage_3/render.py`

Render a `RunState` into Telegram-safe HTML (using escaped dynamic values) with:

- phase icon and label;
- a fixed-width progress bar and percentage;
- current detail and elapsed time;
- fetched/shortlisted/ranked/selected counts;
- geo and repeat indicators;
- fallback, waiting, cancellation, failure, timeout, and completion states.

Expose a deterministic `render_run(state)` function and small helpers for keyboards and
status text. No Telegram client or network dependency belongs in this module.

## Slice 2: orchestration, identity, and observability

### Run identity and manifest

Generate one safe `run_id` per run, such as a UTC timestamp plus a random suffix. Accept an
explicit run ID only after rejecting unsafe path characters. Preserve the existing human-facing
`TODAY` date separately.

Use a run-scoped state layout outside generated application content:

```text
~/.jobsearch-stage3-runs/YYYY-MM-DD/<run_id>/
  manifest.json
  pipeline.log
```

The manifest is atomically replaced after each state update and contains only non-secret
metadata, for example:

```json
{
  "run_id": "20260908T120000Z-ab12cd",
  "date": "2026-09-08",
  "status": "running",
  "phase": "2",
  "geo": "Germany",
  "job_count": 10,
  "rank_attempt": 1,
  "rank_max_attempts": 3,
  "error_class": null,
  "resumable": true,
  "started_at": "2026-09-08T12:00:00Z",
  "finished_at": null
}
```

`RESUME=1` behavior is deterministic: an explicit run ID selects that manifest; without one,
exactly one unfinished run for the date may be selected; multiple candidates fail with their
IDs rather than choosing nondeterministically; no manifest uses the legacy path behavior.

### `stage_3/orchestrator.py`

Provide an injectable runner and clock for tests. The production runner:

- launches `scripts/run_daily.sh` from the configured repository;
- passes `GEO_FILTER`, job-count, run ID, output root, and other options through the environment;
- never passes secrets in argv;
- tails and replays the log into `ProgressTracker`;
- refuses an overlapping run using the existing lock semantics;
- sends SIGTERM for cancellation, then uses bounded escalation if necessary;
- enforces `STAGE3_MAX_RUNTIME`;
- emits progress events, including an at-most-once waiting warning after 120 seconds of no
  meaningful log activity and then no more than one warning every two minutes;
- classifies terminal outcomes without duplicating the existing Phase 2 retry loop;
- writes one final manifest and one final best-effort notification.

The existing retry loop remains authoritative. It continues retrying transient failures only,
while authentication, permission, quota, credit, and full ranker-timeout failures retain their
current non-retry behavior. The manifest records attempt number, maximum attempts, error class,
last exit status, and timestamps. Early terminal notifications are deduplicated against the
final exit notification.

### Log retention

At the orchestrator boundary, retain the seven newest date directories. Compress older logs
where practical before deleting them, never delete the active run's log, and keep manifests
according to a separate configured retention period. Cleanup failure is diagnostic and
non-fatal to the completed pipeline.

## Slice 3: Telegram bot and scheduling

### `stage_3/bot.py`

Use `python-telegram-bot`'s asynchronous `Application`. Implement:

- `/start`;
- `/status`;
- `/run [geo]`;
- `/cancel`;
- `/schedule <date/time> [geo] [count]`;
- `/schedule_recurring <cron> [geo] [count]`;
- `/list_schedules`;
- `/cancel_schedule <id>`.

Enforce `Stage3Config.is_authorised` for every command and callback. Never modify the allowlist
or token configuration from Telegram.

`/run` presents count options 5, 10, 20, 25, and custom. Custom counts are validated and
bounded before constructing a `RunRequest`. Geo choices come from
`scripts/build_search_plan.py --list-geos`; unknown explicit geos are rejected before a run is
started. Callback data uses short indexes or IDs.

The bot sends an initial status message, edits it as progress changes, and reports completion,
failure, cancellation, fallback, and waiting states. It does not send generated PDFs.

### Persistent schedules

Use a JSON schedule store outside the repository rather than adding a database dependency:

```text
~/.jobsearch-stage3-schedules.json
~/.jobsearch-stage3-schedules.json.bak
```

Writes are crash-safe: serialize validated records, write a same-directory temporary file,
flush and `fsync`, atomically replace the primary with `os.replace`, then maintain a backup of
the last known-good state. Apply owner-only permissions (`0600`) to both files. On startup,
validate the primary; if corrupt, recover the backup and notify the operator. If both are
invalid, disable scheduling safely and never guess. No credentials, cookies, job text, or
prompts are stored.

A schedule record contains an ID, one-shot or recurring kind, schedule expression/time,
geo, count, timezone, enabled state, and last/next execution timestamps. Five-field cron is
validated before persistence. A scheduler task checks due records once per minute, prevents
duplicate launches, and calls the same orchestrator method as `/run`. An active run causes a
clear refusal rather than a queue. Schedule callbacks use only short IDs.

## Slice 4: pipeline integration and outputs

### Run-scoped document paths

Use one shared path helper for producers and completion checks:

```text
cv/YYYY-MM-DD/<run_id>/<slug>/Salman-Resume.tex
cv/YYYY-MM-DD/<run_id>/<slug>/Salman-Resume.pdf
cover_letters/YYYY-MM-DD/<run_id>/<slug>/Salman-Cover-Letter.tex
cover_letters/YYYY-MM-DD/<run_id>/<slug>/Salman-Cover-Letter.pdf
```

Thread the output root/run ID through `run_daily.sh`, `telegram_select.py`, and
`generate_batch.py`. Keep existing date-keyed rankset and pending-selection compatibility files
where consumers require them. A document is complete only when all four artifacts exist and
are non-empty.

Legacy `cv/<slug>/...` and `cover_letters/<slug>/...` paths remain readable. A complete legacy
application is skipped and never overwritten. Partial legacy output is not deleted; a new
run-scoped output is generated instead. Tracker and Telegram summaries report relative actual
paths, not machine-specific absolute paths.

## Slice 5: authenticated LinkedIn fallback

Add a separate `scripts/linkedin_playwright.py` provider rather than rewriting the Kimi
WebBridge or guest implementations. The explicit chain is:

1. Kimi WebBridge;
2. configured Playwright storage state;
3. Playwright email/password login;
4. guest fallback.

Read `LINKEDIN_EMAIL` and `LINKEDIN_PASSWORD` only from `os.environ`. Optionally accept
`LINKEDIN_PLAYWRIGHT_STORAGE_STATE`, `LINKEDIN_PLAYWRIGHT`, headless, timeout, profile, and
related operational settings from the environment. Storage-state files are provisioned outside
the repository, owner-readable only, and never accepted through Telegram or printed.

Use temporary contexts by default. Use a persistent configured profile only by explicit operator
choice. Navigate to the canonical numeric job URL, wait for SPA hydration, poll for the semantic
job-description module, and validate the extracted text with the existing minimum-length and
page-furniture rules. Bound every operation and charge every navigation attempt to the existing
`RequestLedger`.

Login walls, 2FA prompts, CAPTCHA, checkpoints, consent pages, missing selectors, and expired
sessions are typed non-fatal errors that trigger fallback and an operator-action-required
status. The provider never bypasses challenges. Close only browser resources it owns in
`finally` blocks. Playwright installation and browser provisioning are explicit documented
steps and remain optional until configured.

## Slice 6: tests, docs, and release

Add isolated tests for:

- config parsing, permissions, token isolation, secret redaction, and child environments;
- progress parsing, phase transitions, counts, replay, percentage, and stalled warnings;
- rendering and HTML escaping;
- run IDs, manifests, atomic writes, resume ambiguity, lock cleanup, cancellation, timeout,
  retention, and notification deduplication;
- bot authorization, commands, callback selection, live edits, and scheduling;
- schedule backup/recovery and corrupt-store safety;
- shared output paths, legacy detection, partial artifacts, and same-day isolation;
- retry classification and state reporting;
- Playwright success, delayed hydration, login/2FA/CAPTCHA/checkpoint fallback, extraction
  rejection, request-budget accounting, credential secrecy, and resource cleanup using fake
  browser objects.

Run the full Python suite, targeted tests, `bash -n scripts/run_daily.sh`, and offline fake
browser/orchestrator smoke tests. Do not enable live LinkedIn automation without explicit local
credential and browser provisioning.

Update `README.md` and a deployment guide with third-token creation, owner ID discovery,
owner-only environment-file setup, Playwright optional installation/browser provisioning,
starting the bot, running the orchestrator, scheduling syntax, state/log locations, retention,
recovery, and security warnings. Create the final PR with staged commits, a detailed summary,
test results, known pre-existing baseline failure, and rollback notes.

## Acceptance criteria

- Manual and scheduled runs invoke the same orchestrator and pipeline worker.
- No concurrent run is started, no schedule is lost on a torn write, and corrupt schedule data
  never causes an unexpected launch.
- Progress reconstructs after restart and never reports 100% before completion.
- A silent phase produces a bounded waiting notification without falsely declaring failure.
- Run IDs distinguish same-day executions and prevent output collisions.
- Existing complete applications are preserved.
- Playwright is optional, bounded, credential-safe, challenge-respecting, and falls back cleanly.
- All new behavior is covered by offline tests; existing tests remain green except the documented
  unrelated global settings baseline finding.
