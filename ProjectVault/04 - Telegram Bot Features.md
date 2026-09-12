---
tags: [note, telegram]
---
# 04 - Telegram Bot Features

The project talks to Telegram through **three deliberately separate bots** (Telegram
allows one `getUpdates` consumer per token — sharing causes 409 collisions):
1. the **Claude Code bridge bot** (external repo, inbound chat with Claude),
2. the **job-selector bot** (this repo — the ranked list + Submit buttons),
3. the **Stage 3 control bot** (this repo — `/run`, `/status`, schedules).

Outbound notifications additionally use the `tg-notify` CLI (via the bridge bot's
token). Full design doc: `docs/TELEGRAM.md` and `docs/STAGE_3.md`.
Parent: [[00 - Project Overview]].

## Stage 3 bot commands (`stage_3/bot.py`, run with `python -m stage_3.bot`)

| Command | What it does |
|---|---|
| `/start` / `/help` | Greeting + full command summary |
| `/health` / `/ping` | Uptime, active-run state, schedule count — confirms reception |
| `/status` | Live progress of the active run; when idle, selected fields of the newest manifest on disk |
| `/run [geo] [count]` | Start a run now. Missing geo/count come from inline keyboards (geo picker → count 5/10/20/25/custom). Count bounded 1–50 |
| `/cancel` | SIGTERM the active run (escalates to SIGKILL after 10s grace); manifest kept |
| `/schedule <YYYY-MM-DDTHH:MM> [geo] [count] [tz=Zone]` | One-shot run at a wall-clock time (naive local; record's tz decides zone) |
| `/schedule_recurring <min hr dom mon dow> [...]` | Five-field Vixie-style cron, e.g. `/schedule_recurring 0 8 * * 1-5 Germany 10` |
| `/list_schedules` | Sanitized list of stored schedules with ids |
| `/cancel_schedule <id>` / `/unschedule [id]` | Remove a schedule (by `s`+4-hex id); no id → lists |

Callbacks: `geo:<index>`, `count:<n>`, `custom` — tiny payloads because Telegram caps
callback data at 64 bytes; geo names never enter callback data (index-keyed).

## Selector bot (job selection UI)

**Files:** `scripts/selector_listener.py` (launchd-driven, the production path) and
`scripts/telegram_select.py` (manual/same logic, `--dry-run` renders offline).

- Phase 3 kickstarts the listener; it reads `/tmp/jobsearch_pending_selection.json`
  (the **only** authorization to post — a bare KeepAlive restart without it exits 0).
- Sends one card per ranked job (up to 25): company, title, location, score, fit tier,
  source, language/experience gate verdicts (pass/fail/unverified), and a link — with
  per-card ☑️/☐ toggle buttons, Select all / Clear, and a "✅ Submit (n)" control
  message. `SEND_DELAY_SECONDS = 0.12` paces sends; RetryAfter honored with 3 attempts.
- **Crash-resume:** every message id and every pick is persisted per message to
  `/tmp/jobsearch_selection_<today>[_<run_id>].json`; a restart reattaches to the same
  messages instead of re-posting. Phases: fresh → resume → interrupted (died between
  Submit and summary — never auto-retried) → finished.
- Stale taps: `ack()` swallows Telegram's "query too old" so redraws still happen.
- Anti-cross-run taps: a tap is only honored if its message id belongs to *this* run's
  state (`is_current_message`) — yesterday's job #3 can't toggle today's.
- On Submit: locks the selection, strips keyboards, then runs one `claude -p` drafter
  per chosen job (sequential, `MAX_PARALLEL_JOBS = 1` — the API relay pre-consumes a
  per-request hold, so parallel drafters starve each other), appends tracker rows,
  marks completed, sends the ✅/❌ summary. PDFs are never sent as Telegram documents —
  only their disk locations.

## UI features

- **Live progress bar** (`stage_3/render.py`): fixed-width `████░░░░░░` bar + percent
  (capped at 99 until the pipeline's own completion marker), phase x/10, per-phase
  checklist with ⬜/🔄/✅/⏭️/❌ icons, headline counts (Fetched · Pre-filtered ·
  Shortlisted · Ranked · Cleared gate · Offered), 📍 geo, 🔁 repeats, ⚠️ fallback
  indicator, and a "💬 Still working" note when quiet > 120s.
- **One message per run, edited in place** via `editMessageText` (throttled to ≥3s
  or state change) rather than message spam.
- All dynamic values `html.escape`d — job postings are untrusted input and must render
  as text, never markup.

## Notifications

- `tg-notify` CLI (`~/.local/bin`, global): plain/markdown message, `--title`,
  `--chat`, silent-on-success, exit 0/1/2. Token read via stdin `--config` so it never
  appears in `argv` (ps-safe).
- `run_daily.sh`'s EXIT trap pings on **success and failure alike** — body includes
  jobs fetched / ranked / report path / log path / run id. `SKIP_NOTIFY=1` silences.
- Stage 3 orchestrator notifications: run started, "Still working" nudges, terminal
  outcome (✅ complete / ❌ failed with exit + error class / ⏹ cancelled / ⏱ timeout),
  delivered through a thread-safe callback into the bot's event loop
  (`_wire_notifications`); terminal notices are marked `terminal_notified` in the
  manifest only **after** confirmed delivery.
- Enrichment-fallback alert: one ping per run if Phase 1c lands on the guest CLI
  (`--alert-on-fallback`, unattended runs only).

## Authorization mechanism

- **Stage 3:** `STAGE3_ALLOWED_USER_IDS` (comma-separated numeric ids; falls back to
  `STAGE3_CHAT_ID` alone; missing/blank refuses to start — the bot is never open).
  `Stage3Config.is_authorised()` is the first thing every handler runs; unauthorized
  users get a refusal answer, never silence. Allowlist is never editable from Telegram.
- **Selector:** `SELECTOR_ALLOWED_USER_IDS` — the `guard()` in both selector entry
  points checks user id *and* current-run message identity.
- **Config hygiene:** tokens live in env files outside the repo (mode 600, warned if
  broader); token shape validated at startup; `validate_token_isolation` refuses to
  start if the Stage 3 token matches the selector or bridge bot's; a real `getMe`
  call validates the token before polling starts.
- **Secrets never in argv, logs, manifests, or exceptions** — `diagnostics.safe_preview`
  redacts Telegram-shaped tokens; `Stage3Config.__repr__` is redacted by construction.

## Implementation files

| Feature | File(s) |
|---|---|
| Stage 3 commands/keyboards/progress | `stage_3/bot.py`, `stage_3/render.py`, `stage_3/progress.py` |
| Run supervision & manifests | `stage_3/orchestrator.py` |
| Schedule storage & cron parser | `stage_3/schedules.py` |
| Secret-safe logging | `stage_3/diagnostics.py` |
| Config/token validation | `stage_3/config.py` |
| Selection list & drafting | `scripts/selector_listener.py`, `scripts/telegram_select.py` |
| Outbound pings | `tg-notify` (external) called from `run_daily.sh`, `enrich_linkedin.py` |
| Bot ops doc | `docs/TELEGRAM.md`, `docs/STAGE_3.md` |

Related: [[05 - Playwright and Headless Mode]] · [[02 - Configuration and Settings]].

## Vault graph

Bot files: [[stage3-bot]] · [[stage3-config]] · [[stage3-orchestrator]] · [[stage3-schedules]] · [[telegram-select]] · [[selector-listener]] · [[docs-telegram]] — hub: [[_Script Files]]
