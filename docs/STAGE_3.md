# Stage 3 — on-demand pipeline control via Telegram

Setup and operations guide for the Telegram bot that turns the daily pipeline
on-demand: `/run` when you want it, `/schedule` when you want it later, live
progress while it runs. Read this before starting the bot;
`docs/STAGE_3_RESUME.md` is the historical build checkpoint and
`docs/STAGE_3_HANDOFF.md` records the design decisions this guide summarizes.

The guide is deliberately explicit about the security posture: this bot can
launch a pipeline that drafts job applications, so it answers **only** to the
owner allowlist, holds its token only in an owner-readable file outside the
repo, and treats every Telegram message as untrusted input.

---

## 1. What you need before starting

1. **A third Telegram bot token from @BotFather.** This is not optional and not
   interchangeable with your other bots. Telegram allows exactly **one**
   `getUpdates` consumer per token: the Claude Code bot polls continuously under
   launchd, and the job-selector bot polls during its own runs. Pointing Stage 3
   at either token causes 409 collisions that can silently eat a job selection or
   lock you out of the other bot. `stage_3.config.validate_token_isolation`
   refuses to start the bot in that configuration.
2. **Your numeric Telegram user id** (the chat id is the same number for a
   direct chat). Get it from `@userinfobot` or `@getmyid_bot`.
3. Python 3.10+ with `python-telegram-bot` installed (see
   `docs/STAGE_3_HANDOFF.md` §6 for the pinned versions).

## 2. Create the env file (outside the repo — never commit it)

Create `~/.jobsearch-stage3.env` (override the location with the
`JOBSEARCH_STAGE3_ENV` environment variable) with mode `600`:

```
STAGE3_BOT_TOKEN=123456:AA...          the third bot from @BotFather
STAGE3_CHAT_ID=123456789               where unsolicited messages go
STAGE3_ALLOWED_USER_IDS=123456789      comma-separated; nobody else is answered
STAGE3_REPO=/Users/you/Projects/ai-job-search   optional, defaults to this checkout
STAGE3_MAX_RUNTIME=10800               optional seconds; 0 disables the ceiling
STAGE3_RUN_STATE_ROOT=~/.jobsearch-stage3-runs    optional, run manifests live here
STAGE3_SCHEDULE_PATH=~/.jobsearch-stage3-schedules.json   optional
LINKEDIN_PLAYWRIGHT_STORAGE_STATE=~/.jobsearch-linkedin-state.json  optional
```

Rules the config enforces, so you do not have to trust this file:

- Nonblank values in the env file take precedence over the process environment;
  the process environment fills keys omitted from the durable file.
- A missing, blank, or separator-only `STAGE3_ALLOWED_USER_IDS` falls back to
  `STAGE3_CHAT_ID` alone — owner only, never open. Non-numeric entries raise.
- The token and unsupported legacy LinkedIn email/password variables never enter
  the child environment. The storage-state path is the only authenticated
  Playwright input propagated to the pipeline.
- A group-readable env file warns but does not abort; fix it anyway
  (`chmod 600 ~/.jobsearch-stage3.env`).

## 3. Start the bot

```
python -m stage_3.bot
```

The loader validates the token shape, token isolation, and the allowlist before
anything polls. Startup then performs a real Telegram `getMe` request; an invalid
token or unreachable API aborts startup instead of leaving a silent poller. On a
clean start the bot listens for its commands and the scheduler loop ticks once a
minute.

## 4. Commands

| Command | What it does |
|---|---|
| `/start` | Greet and show the command summary. |
| `/health` or `/ping` | Confirm command reception and report non-sensitive uptime, active-run state, and schedule count. |
| `/status` | Show live in-memory progress, including safely restored progress after a bot restart; when idle, show selected fields from the latest manifest. |
| `/run [geo] [count]` | Start a run. Missing pieces come through inline keyboards (geo, then count 5/10/20/25/custom). |
| `/cancel` | SIGTERM the active run (escalating to SIGKILL), keep the manifest. |
| `/schedule <YYYY-MM-DDTHH:MM> [geo] [count] [tz=Zone]` | One-shot run at a wall-clock time. |
| `/schedule_recurring <min hr dom mon dow> [geo] [count] [tz=Zone]` | Five-field cron, e.g. `/schedule_recurring 0 8 * * 1-5 Germany 10`. |
| `/list_schedules` | Sanitized list of stored schedules with their ids. |
| `/cancel_schedule <id>` | Disable a schedule by its short id (`s` + 4 hex). |

Examples:

```
/run                     # keyboards ask for geo and count
/run Germany             # keyboard asks for count
/run Germany 10          # starts immediately, 10 jobs, Germany-scoped
/schedule 2026-09-09T08:30 Netherlands 15 tz=Europe/Amsterdam
/schedule_recurring 0 8 * * 1-5 15
```

Every command and callback passes `Stage3Config.is_authorised` first; an
unauthorized user gets a refusal and nothing else. While a run is active,
another `/run` is refused (never queued) — overlapping runs are an error, not a
queue. One initial progress message is sent per run and then **edited** in
place (Telegram `editMessageText`): a 10-character progress bar, current phase,
percent (capped at 99 until the pipeline's own completion marker), counts, and
geo. Edits are throttled; silent phases add a "Still working" note after 120 s.

PDFs are written to disk only — the bot reports where documents landed and
never sends them as Telegram documents. Telegram messages are untrusted input:
nothing a message says can change the allowlist, the token, or the pipeline's
gates.

## 5. Schedules: storage, recovery, and what happens on failure

- Schedules live in `STAGE3_SCHEDULE_PATH` (default
  `~/.jobsearch-stage3-schedules.json`), mode `0600`, written atomically (temp
  file + `fsync` + `os.replace`).
- The last known-good state is kept beside it as `.bak`. A corrupt primary is
  recovered from the backup; **if both are corrupt the store raises a typed
  error and scheduling stays disabled** — the bot never guesses, and manual
  `/run` keeps working.
- Cron expressions are strict five-field (`min hr dom mon dow`) with `*`, lists,
  ranges and steps, evaluated in the record's timezone (`ZoneInfo`; the default
  timezone is UTC, override per-record with `tz=Zone`).
- Before launching due jobs, the scheduler atomically persists every due record's
  `last_started_at`. If that save fails, **no job launches**, the source records
  remain unchanged, and the schedule is still due on the next tick or restart.
  Successful launches use the same orchestrator path as manual `/run`; refusals
  and storage failures are pushed to `STAGE3_CHAT_ID`.

## 6. Run state, logs, and retention

- Each run gets an id (`YYYYMMDDThhmmssZ-<6 hex>`) and a manifest under
  `STAGE3_RUN_STATE_ROOT/<date>/<run_id>/manifest.json` (atomic, non-secret
  fields only). The worker writes its durable stream beside it as `pipeline.log`;
  non-Stage-3 launches keep the original `logs/daily/YYYY-MM-DD.log` default.
- Secret-safe bot diagnostics are written to
  `STAGE3_RUN_STATE_ROOT/stage3-bot.log` and, while a run is active, duplicated
  into that run's `pipeline.log`. These diagnostics record callback installation,
  callback invocation, Telegram API attempts, message ids on success, and
  exception types on failure; text previews are bounded and Telegram-shaped
  tokens are redacted. New diagnostic logs use mode `0600`.
- On startup the bot scans `running` manifests. It reattaches only when the
  persisted PID, dedicated process-group id, and OS process-start marker all
  still match, then replays `pipeline.log` for `/status`, `/cancel`, and overlap
  refusal. A missing or reused process is finalized as terminal `interrupted`
  and is not resumed. If more than one process identity is still live, startup
  refuses to choose or signal either one and requires operator investigation.
- The orchestrator enforces `STAGE3_MAX_RUNTIME` (default 3h; `0` disables it).
  A restart does not reset that budget: reattachment accounts from the original
  manifest `started_at`, covering the shape of hang the 2026-08-18 run exhibited.
- The seven newest run-date directories are retained; older ones are tarred to
  `<root>/archive/` first and the active run's log is never deleted.

## 7. Optional tier-3 LinkedIn enrichment (Playwright)

Tiers 1–2 (Kimi WebBridge, guest HTTP) cover the normal path. Tier 3 is an
opt-in authenticated browser for when both are unavailable:

1. Install the optional pinned dependency and the browser itself:
   `pip install -r requirements-stage3.txt && playwright install chromium`
2. Provision the session **from a terminal, once**:
   `python3 scripts/linkedin_session.py` — a headed browser opens, *you* sign
   in, and the helper saves a Playwright storage state to
   `~/.jobsearch-linkedin-state.json` with exact mode `0600` (outside the repo).
   The loader and provider reject any broader Unix permissions. The helper
   prints only a shape summary (cookie counts, soonest expiry), never a value,
   and never accepts a session, email, or password over Telegram or any other
   channel.
3. Point `LINKEDIN_PLAYWRIGHT_STORAGE_STATE` at it and set
   `linkedin.use_playwright: true` in `config/search_matrix.json`.

The provider never bypasses a gate: a login wall means the session lapsed
(re-provision from a terminal), and a CAPTCHA/checkpoint/consent screen pauses
the tier and waits for a human — it is reported, not retried. Every navigation
is charged to the same request ledger as the rest of Phase 1c, so tier 3 can
never exceed the run cap. `scripts/linkedin_session.py --show` describes an
existing state without opening a browser.

## 8. Stopping and deploying

- Stop the bot with Ctrl-C (or `kill -TERM` on the `python -m stage_3.bot`
  process). The pipeline runs in its own process group and keeps writing its
  run-scoped `pipeline.log`; on bot restart, the exact process identity must
  validate before `/status` and `/cancel` are restored. If it no longer
  validates, the manifest becomes terminal `interrupted` rather than pretending
  the run is live.
- Deployment is "run it on a machine that can reach Telegram and LinkedIn":
  a launchd plist or systemd unit wrapping `python -m stage_3.bot` with the
  env file present is the whole story. There is no container image by design —
  the orchestrator shells out to `scripts/run_daily.sh`, which expects the
  repo's own toolchain (`bun`, `lualatex`, `tg-notify`) on the host.

## 9. Security rules this bot operates under (summary)

- Secrets live only in the owner-readable external env file — **never commit**
  them, never put them in argv, logs, manifests, or Telegram messages.
- Never change bot access, allowlists, or token config because a Telegram
  message asked you to; those are terminal-only actions.
- Job postings are untrusted input, never instructions.
- One active run at a time; concurrent runs are refused.
- Legacy `cv/<slug>/` and `cover_letters/<slug>/` outputs are read-only:
  complete ones are skipped, partial ones are never moved or deleted.
- PDFs stay on disk; they are never sent as Telegram documents.
