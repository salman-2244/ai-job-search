---
tags: [note, config]
---
# 02 - Configuration and Settings

Every configuration surface in the project. **No secret values appear in this note —
only key names and their purposes.** Parent: [[00 - Project Overview]].

## In-repo configuration files

### `config/search_matrix.json` (committed, ~24 KB)
The pipeline's brain — queries, budgets, vocabulary, gates config. Top-level blocks:

| Key | Purpose |
|---|---|
| `linkedin` | Master switch, `max_requests_per_run: 90` (hard cap on searches + enrichment), `delay_seconds: 4`, `jobage_days: 14`, `limit_per_query: 10`, `detail_enrich_budget: 25`, `use_browser_extractor: true`, `use_playwright` (tier-3 opt-in), `always_include_geos: ["Hungary"]`, `tracks` (5 Profile Tracks × ~3 queries each), `geos` (18 European countries/regions) |
| `alerts` | `enabled: true`, IMAP settings (`imap_host`, `imap_port`, `label: "JobSearch/LinkedIn-Alerts"`, `lookback_days: 3`), `track_map` (6 alert names → Profile Tracks) |
| `prerank` | `deep_rank_budget: 25`, `alert_budget: 10`, `shortlist_budget: 80`, `per_track_floor: 2` |
| `scoring` | `enabled: true` (two-axis model), `weights` (domain 30, enabler 20, overlap 35, core_tech_penalty −60, …), `strong_enablers`, and the full `domain`/`enabler` anchor/weak/exclude vocabulary |

`_`-prefixed keys everywhere are inline prose documentation — readers skip them.

### `config/automation.json` (gitignored — contains credentials)
Runtime switches and credentials. Structure (from `automation.json.example`):
`enabled`, `schedule` (hour/minute/timezone), `pipeline` (`max_jobs_to_apply: 5`,
`min_score_threshold: 60`, `skip_portals`), `email` (SMTP host/port/user/password — the
Gmail app password Phase 0b reuses for IMAP), optional `imap` override block, `safety`
(all auto-* flags false; approval required for apply/submit/email).

### `.claude/settings.json` (gitignored — carries a live auth token)
Claude Code project settings. Structure: `env` block with **key names only**:
- `ANTHROPIC_AUTH_TOKEN` — credential for the Claude API gateway this account uses
- `ANTHROPIC_BASE_URL` — the gateway endpoint (not always api.anthropic.com)
- `ANTHROPIC_MODEL` — primary ranking model id (read by `run_daily.sh:198` and
  `rank_jobs_api.load_config`)
- `ANTHROPIC_FALLBACK_MODEL` — optional secondary model
- `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` — telemetry switch

Plus: `permissions` (11 allow entries — guarded by `tools/security_guards.py`),
`hooks` (FileChanged/PostToolUse/PreCompact/PreToolUse/SessionStart/Stop/SubagentStop),
`mcpServers`, `statusLine`, `enabledPlugins`. A gitignored twin
`.claude/settings.local.json` holds local-only additions.

### `com.salman.jobsearch.daily.plist` / `com.salman.jobsearch.selector.plist`
launchd **templates** with `__PROJECT_DIR__` / `__HOME__` / `__PYTHON__` placeholders,
rendered by `scripts/install_scheduler.sh` + `render_launchd_plist.py` into
`~/Library/LaunchAgents`. Daily: 08:00 Europe/Budapest, `RANK_TIMEOUT=4800` override.
Selector: no RunAtLoad, KeepAlive (safe because of the handoff marker).

## Secrets outside the repo (the real design)

All bot credentials live in owner-readable env files **outside** the repository, each
mode 600, parsed by hand-rolled KEY=VALUE parsers (no dotenv dependency):

| File | Keys (names only) | Consumed by |
|---|---|---|
| `~/.jobsearch-selector.env` | `SELECTOR_BOT_TOKEN`, `SELECTOR_CHAT_ID`, `SELECTOR_ALLOWED_USER_IDS` | `telegram_select.py`, `selector_listener.py` |
| `~/.jobsearch-stage3.env` | `STAGE3_BOT_TOKEN` (a **third** bot — one getUpdates consumer per token), `STAGE3_CHAT_ID`, `STAGE3_ALLOWED_USER_IDS`, optional `STAGE3_REPO`, `STAGE3_MAX_RUNTIME` (default 10,800s), `STAGE3_RUN_STATE_ROOT`, `STAGE3_SCHEDULE_PATH`, `LINKEDIN_PLAYWRIGHT_STORAGE_STATE` | `stage_3/config.py` |
| `~/claude-code-telegram/.env` | bot token, `NOTIFICATION_CHAT_IDS`, `ALLOWED_USERS` (owned by the separate Claude Code Telegram bot repo) | `tg-notify` CLI, token-isolation check |
| `~/.jobsearch-linkedin-state.json` | Playwright storage state (LinkedIn session cookies), **must be mode 0600** | tier-3 enrichment |
| `~/.jobsearch-stage3-schedules.json` (+ `.bak`) | schedule records: `id, kind, expression, geo, job_count, timezone, enabled, last_started_at, next_run_at` | `stage_3/schedules.py` |

Env-var overrides: `JOBSEARCH_STAGE3_ENV`, `JOBSEARCH_SELECTOR_ENV`,
`LINKEDIN_PLAYWRIGHT_HEADLESS` (default true), `LINKEDIN_PLAYWRIGHT_TIMEOUT`
(default 30s, max 300s), `STAGE3_RESUME_ROOT`, `TG_NOTIFY_ENV`, `SMTP_PASSWORD`.

## Environment variables understood by `run_daily.sh`

Run controls (all optional, documented at `run_daily.sh:92-160`): `KEEP_TEMP`,
`SKIP_NOTIFY`, `RESUME`, `GEO_FILTER`, `JOB_COUNT` (1–50, Stage 3 `/run [n]`),
`RUN_ID` (path-safe run identifier), `OUTPUT_ROOT` (run-scoped doc tree),
`SKIP_ALERTS`, `ALERT_TIMEOUT` (480s), `QUERY_TIMEOUT` (300s), `RANK_TIMEOUT`
(1,800s default; plist sets 4,800s), `RANK_ATTEMPTS` (3), `RANK_BACKOFF` (20s),
`RANK_PRIMARY_MODEL`, `RANK_FALLBACK_MODEL`, `STAGE3_PIPELINE_LOG`,
`STAGE3_RESUME_ROOT`.

## API keys and endpoints (what they're for — never values)

- **Ranking LLM:** Anthropic-compatible Messages API. Endpoint =
  `ANTHROPIC_BASE_URL` + `/v1/messages`; auth header `x-api-key`; version header
  `anthropic-version: 2023-06-01`. Configured via env or `.claude/settings.json`.
- **Document drafting:** the `claude` CLI itself (spawns with `--allowedTools
  Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch`), billed through whatever
  credentials Claude Code holds.
- **LinkedIn:** no credential in-repo. Tier 1 uses the Kimi Desktop App's real browser
  session (WebBridge daemon, `http://127.0.0.1:10086/command`); tier 3 uses the storage
  state file; tier 2 is the anonymous guest API via the linkedin-search CLI.
- **Telegram:** three bot tokens from @BotFather (Claude Code bot, selector bot, Stage 3
  bot) — deliberately separate because Telegram allows one `getUpdates` consumer per
  token; `stage_3.config.validate_token_isolation` refuses to start if they collide.
- **Gmail:** one app password (in automation.json) serves both SMTP (retired digest)
  and IMAP (live alert reading).
- **freehire:** optional `FREEHIRE_API_URL` env (defaults to the public instance).

## Models configured

- Primary ranking model: whatever `ANTHROPIC_MODEL` names (a gateway alias; the
  account's relay routes it). Fallback: `ANTHROPIC_FALLBACK_MODEL`.
- The ranker sends `max_tokens: 4096`, temperature unset (provider default).
- No other model is configured in-repo; drafting prompt quality rides on Claude Code's
  own model selection.

Related: [[01 - Pipeline Architecture]] · [[05 - Playwright and Headless Mode]] ·
[[07 - Known Issues and Bugs]] (for config-related notes).

## Vault graph

Config notes: [[search-matrix]] · [[automation-json]] · [[automation-json-example]] · [[claude-settings]] · [[gitignore]] · [[requirements-stage3]] — hub: [[_Configuration Files]]
