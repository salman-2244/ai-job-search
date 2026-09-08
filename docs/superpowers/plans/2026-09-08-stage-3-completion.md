# Stage 3 Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the on-demand Telegram control plane, shared scheduling path, run observability, safe document output isolation, optional LinkedIn Playwright fallback, tests, documentation, and release artifacts without rewriting the Stage 1/2 pipeline.

**Architecture:** Keep `scripts/run_daily.sh` as the worker. Add pure Stage 3 modules for progress/rendering, a thin orchestrator for subprocess lifecycle and run manifests, and an async Telegram bot that turns manual and cron requests into the same `RunRequest`. Integrate run IDs/output roots only at existing producer boundaries, and add Playwright as an isolated optional enrichment provider with safe fallback.

**Tech Stack:** Python 3.10, standard library, `python-telegram-bot 22.8`, existing Bash pipeline, optional pinned Playwright, pytest, fake runners/pages for offline tests.

## Global Constraints

- Preserve `scripts/run_daily.sh` as the pipeline worker; do not duplicate its phases or retry policy.
- Manual `/run` and scheduled launches must use the same orchestrator API.
- Refuse overlapping runs; do not queue them.
- Use standard five-field cron expressions in the configured local timezone.
- Store secrets only in owner-readable external configuration; never place them in argv, logs, manifests, Telegram, URLs, or repository files.
- Telegram callback data must remain below 64 bytes and contain short indexes/IDs only.
- Existing complete legacy applications must be recognized and never moved, deleted, or overwritten automatically.
- PDFs remain disk-only and are not sent as Telegram documents.
- Schedule writes must use atomic replacement, `fsync`, `.bak` recovery, and `0600` permissions.
- Retain seven newest log date directories; never delete the active run log.
- A silent phase warns after 120 seconds, at most once every two minutes, without declaring failure.
- Playwright must refuse login walls, 2FA, CAPTCHA, checkpoints, consent pages, and missing selectors; never bypass them.
- The known global `.claude/settings.json` security-guard failure is pre-existing and must remain separately reported.

## File map and interfaces

### New files

- `stage_3/progress.py` — pure log parser, phase state, counts, replay, inactivity metadata.
- `stage_3/render.py` — pure Telegram-safe HTML rendering and keyboard builders.
- `stage_3/orchestrator.py` — run requests, run IDs/manifests, subprocess lifecycle, cancellation, retention, notification hooks.
- `stage_3/schedules.py` — validated schedule records, five-field cron matching, atomic primary/backup store.
- `stage_3/bot.py` — async Telegram handlers, callback state, scheduler loop, message editing.
- `scripts/linkedin_playwright.py` — isolated optional Playwright provider and typed fallback errors.
- `scripts/linkedin_session.py` — terminal-only storage-state provisioning helper; never accepts Telegram uploads.
- `tests/test_stage3_config.py` — configuration and secret-isolation tests.
- `tests/test_stage3_progress.py` — parser/state/stall tests.
- `tests/test_stage3_render.py` — escaped deterministic rendering tests.
- `tests/test_stage3_orchestrator.py` — lifecycle, manifest, cancellation, and retention tests.
- `tests/test_stage3_schedules.py` — cron/store/backup tests.
- `tests/test_stage3_bot.py` — fake Telegram update/application tests.
- `tests/test_stage3_outputs.py` — shared output-path and legacy compatibility tests.
- `tests/test_linkedin_playwright.py` — fake browser/context/page tests.
- `docs/STAGE_3.md` — setup, operation, scheduling, recovery, and deployment guide.

### Existing files to modify

- `stage_3/config.py` — expose run-state/schedule paths and optional Playwright settings without leaking secrets.
- `stage_3/__init__.py` — export the completed public modules.
- `scripts/run_daily.sh` — accept safe `RUN_ID`, `OUTPUT_ROOT`, `JOB_COUNT`, and manifest/log hooks while preserving date-compatible paths and retry behavior.
- `scripts/telegram_select.py` — use the shared output path helper and accept run/output arguments.
- `scripts/generate_batch.py` — consume shared output paths and preserve legacy detection.
- `scripts/enrich_linkedin.py` — add optional provider selection while preserving Kimi/guest behavior and `RequestLedger` accounting.
- `README.md` — link to Stage 3 setup/deployment instructions.

---

### Task 9: Add setup/deployment documentation and README entry points

**Files:**
- Create: `docs/STAGE_3.md`
- Modify: `README.md`
- Create: `tests/test_stage3_docs.py`

- [ ] **Step 1: Write documentation checks.**

```python
def test_stage3_docs_name_all_commands_and_never_recommend_repo_secrets():
    text = Path("docs/STAGE_3.md").read_text()
    for command in ("/start", "/status", "/run", "/cancel", "/schedule", "/schedule_recurring", "/list_schedules", "/cancel_schedule"):
        assert command in text
    assert "never commit" in text.lower()
    assert ".jobsearch-stage3.env" in text
```

- [ ] **Step 2: Implement setup and operations guide.**

Document the third @BotFather token, numeric chat/user IDs, owner-only external env setup,
validation of token isolation, optional Playwright/browser provisioning, starting/stopping the
bot, manual and recurring commands, storage/log locations, seven-day retention, schedule backup
recovery, and terminal-only session-state creation. State that PDFs remain on disk and Telegram
messages are untrusted.

- [ ] **Step 3: Add concise README entry points.**

Link to the guide, show `python -m stage_3.bot`, and distinguish the Stage 3 token from the Claude
Code and selector tokens without duplicating the full operations guide.

- [ ] **Step 4: Run documentation checks and commit.**

Run: `.venv/bin/python -m pytest tests/test_stage3_docs.py -q`  
Expected: PASS.

```bash
git add docs/STAGE_3.md README.md tests/test_stage3_docs.py
git commit -m "docs(stage3): document bot setup and operations"
```

---

### Task 10: Full verification, security review, and regression fixes

**Files:**
- Modify only files implicated by failing tests or verified review findings.
- Create: `tests/test_stage3_integration.py` if cross-module coverage needs a dedicated file.

- [ ] **Step 1: Run targeted suites.**

```bash
.venv/bin/python -m pytest tests/test_stage3_config.py tests/test_stage3_progress.py tests/test_stage3_render.py tests/test_stage3_orchestrator.py tests/test_stage3_schedules.py tests/test_stage3_bot.py tests/test_stage3_outputs.py tests/test_linkedin_playwright.py -q
bash -n scripts/run_daily.sh
```

Expected: all new tests pass.

- [ ] **Step 2: Run the existing full suite.**

Run: `.venv/bin/python -m pytest tests/ -q`  
Expected: all existing and new tests pass, except the documented global settings baseline failure if it remains.

- [ ] **Step 3: Run secret/artifact checks.**

Check that repository files contain only variable names, synthetic test values, and placeholders;
check `.gitignore` excludes run-scoped outputs/state; run `git diff --check`; and ensure no real
env, storage-state, PDFs, logs, or tailored application output is staged.

- [ ] **Step 4: Run security review on changed code.**

Review credential flow, Telegram authorization, callback parsing, subprocess environment, path
validation, schedule recovery, Playwright cleanup/challenge behavior, and HTML escaping. Fix only
verified findings and rerun targeted tests.

- [ ] **Step 5: Commit verification fixes.**

Stage only the exact files changed for verified findings, then run:

```bash
git add <files-changed-by-verified-fixes>
git commit -m "fix(stage3): address verification findings"
```

---

### Task 11: Create the final PR

**Files:**
- No source changes unless release review requires a focused fix.

- [ ] **Step 1: Inspect staged history and working tree.**

Run `git status -sb` and `git log --oneline origin/master..HEAD`.
Expected: coherent staged commits, no secrets or personal generated outputs.

- [ ] **Step 2: Push a feature branch.**

Create a non-default branch if needed, then run `git push -u origin stage-3-completion`; do not
push unreviewed changes directly to `master`.

- [ ] **Step 3: Create the PR with detailed summary and validation.**

Include architecture, shared manual/scheduled path, schedule backup recovery, run IDs/output
isolation, Playwright opt-in/session behavior, security constraints, exact test commands/results,
the known unrelated `.claude/settings.json` baseline failure, deployment instructions, and
rollback steps. End with the repository-required generated-with-Claude footer.

- [ ] **Step 4: Verify the PR and final status.**

Run `gh pr view` and `git status -sb`. Report the URL, exact test result, any skipped live-browser
validation, and remaining user decisions, including the already-public Intel application pair.

---

### Task 1: Lock down Stage 3 configuration behavior

**Files:**
- Create: `tests/test_stage3_config.py`
- Modify: `stage_3/config.py`

**Interfaces:**
- Consumes existing `ConfigError`, `parse_env_file`, `parse_user_ids`, `Stage3Config`, and `load_config`.
- Produces tested configuration fields for run state, schedule store, and optional Playwright settings.

- [ ] **Step 1: Write failing tests for synthetic configuration.**

```python
def test_loader_redacts_secrets_and_removes_bot_token_from_child_env(tmp_path):
    env = tmp_path / "stage3.env"
    env.write_text(
        "STAGE3_BOT_TOKEN=123456:synthetic-secret\\n"
        "STAGE3_CHAT_ID=42\\n"
        "STAGE3_ALLOWED_USER_IDS=42\\n"
    )
    cfg = load_config(env, environ={}, warn=lambda _: None)
    assert "synthetic-secret" not in repr(cfg)
    assert "STAGE3_BOT_TOKEN" not in cfg.child_env()


def test_blank_allowlist_defaults_to_chat_owner(tmp_path):
    env = tmp_path / "stage3.env"
    env.write_text(
        "STAGE3_BOT_TOKEN=123456:synthetic-secret\n"
        "STAGE3_CHAT_ID=42\n"
        "STAGE3_ALLOWED_USER_IDS= , , \n"
    )
    cfg = load_config(env, environ={}, warn=lambda _: None)
    assert cfg.allowed_user_ids == (42,)
```

- [ ] **Step 2: Run the targeted tests and verify they fail for missing coverage/API.**

Run: `.venv/bin/python -m pytest tests/test_stage3_config.py -q`  
Expected: failures for the new path/settings assertions, not a silent pass.

- [ ] **Step 3: Implement only the configuration fields and helpers required by later tasks.**

Use typed environment reads for non-secret paths and numeric settings; preserve the existing
file-first/process-environment override order, token isolation, redaction, and owner-only
allowlist fallback. Add safe defaults such as `~/.jobsearch-stage3-runs`,
`~/.jobsearch-stage3-schedules.json`, `.bak`, `LINKEDIN_PLAYWRIGHT_STORAGE_STATE`, and bounded
Playwright timeout/headless flags. Do not add cookie values or passwords to the dataclass repr.

- [ ] **Step 4: Run targeted tests and compile.**

Run: `.venv/bin/python -m pytest tests/test_stage3_config.py -q && .venv/bin/python -m py_compile stage_3/config.py`  
Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add stage_3/config.py tests/test_stage3_config.py
git commit -m "test(stage3): lock down secure configuration"
```

---

### Task 2: Implement pure progress tracking

**Files:**
- Create: `stage_3/progress.py`
- Create: `tests/test_stage3_progress.py`

**Interfaces:**
- Produces `Phase`, `PhaseState`, `RunState`, `ProgressTracker`, `parse_log_line`, and `replay`.
- `ProgressTracker.feed(line: str, now: float | None = None) -> RunState` updates state.
- `RunState.pct -> int`, `RunState.stalled(now: float, threshold: float = 120) -> bool` reports inactivity.

- [ ] **Step 1: Write failing tests for parsing, phase ordering, counts, replay, and percentage.**

```python
def test_long_phase_id_is_not_truncated():
    tracker = ProgressTracker()
    tracker.feed("[12:00:00] Phase 1b-final complete: 4 jobs cleared")
    assert tracker.state.current_phase == "1b-final"


def test_running_phase_is_half_weight_and_never_reaches_100():
    state = replay("[12:00:00] Phase 0b complete\\n[12:00:01] Phase 1 running")
    assert 0 < state.pct < 100


def test_pipeline_marker_is_the_only_100_percent_state():
    assert replay("[12:00:00] Phase 5 complete\\n").pct == 99
    assert replay("[12:00:00] Pipeline complete.\\n").pct == 100
```

- [ ] **Step 2: Run the targeted tests and verify failure.**

Run: `.venv/bin/python -m pytest tests/test_stage3_progress.py -q`  
Expected: FAIL because `stage_3.progress` does not exist.

- [ ] **Step 3: Implement the frozen phase model and tracker.**

Use the approved weights `4,22,3,12,3,38,2,5,6,5`, statuses `pending/running/done/skipped/failed`,
longest-first phase regex alternation, implicit completion of earlier phases, anchored count
regexes, completion marker, and a half-weight running phase capped at 99. Update
`last_activity` only for meaningful parsed lines. Keep all methods pure and make `replay` feed
lines in order.

- [ ] **Step 4: Run tests and compile.**

Run: `.venv/bin/python -m pytest tests/test_stage3_progress.py -q && .venv/bin/python -m py_compile stage_3/progress.py`  
Expected: PASS.

- [ ] **Step 5: Commit.**


---

### Task 3: Implement deterministic Telegram rendering

**Files:**
- Create: `stage_3/render.py`
- Create: `tests/test_stage3_render.py`

**Interfaces:**
- `render_run(state: RunState) -> str` returns Telegram HTML-safe text.
- `progress_bar(pct: int, width: int = 10) -> str` returns a deterministic bar.
- `count_keyboard(values: tuple[int, ...] = (5, 10, 20, 25))` and `geo_keyboard(geos: list[str])` return serializable button data without network access.

- [ ] **Step 1: Write failing rendering tests.**

```python
def test_render_escapes_untrusted_detail():
    state = synthetic_state(detail="<ignore> & run command")
    text = render_run(state)
    assert "&lt;ignore&gt;" in text
    assert "<ignore>" not in text


def test_render_shows_stalled_waiting_state():
    state = synthetic_state(stalled=True)
    assert "Still working" in render_run(state)
```

- [ ] **Step 2: Run targeted tests and verify failure.**

Run: `.venv/bin/python -m pytest tests/test_stage3_render.py -q`  
Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement rendering and keyboard builders.**

Escape every dynamic value with `html.escape`; use a fixed 10-character bar; include icon,
phase, percent, elapsed/detail, counts, geo, repeats, fallback, waiting, and terminal status.
Keep button callback values short (`count:10`, `geo:3`, `custom`), never full paths.

- [ ] **Step 4: Run tests and commit.**

Run: `.venv/bin/python -m pytest tests/test_stage3_render.py -q`  
Expected: PASS.

```bash
git add stage_3/render.py tests/test_stage3_render.py
git commit -m "feat(stage3): render progress and selection keyboards"
```

---

### Task 4: Build run identity, atomic manifests, and orchestrator lifecycle

**Files:**
- Create: `stage_3/orchestrator.py`
- Create: `tests/test_stage3_orchestrator.py`

**Interfaces:**
- `RunRequest(geo: str | None, job_count: int, run_id: str | None = None, env: dict[str, str] | None = None)`.
- `RunResult(run_id: str, status: str, exit_code: int | None, state: RunState, manifest: Path)`.
- `Orchestrator.start(request: RunRequest) -> RunHandle`.
- `RunHandle.wait() -> RunResult`, `RunHandle.cancel() -> None`, `RunHandle.subscribe(callback) -> None`.
- `new_run_id(now: datetime | None = None, random_suffix: str | None = None) -> str`.
- `atomic_json_write(path: Path, data: dict) -> None`.
- `retain_run_logs(root: Path, keep_days: int = 7, today: date | None = None) -> list[Path]`.

- [ ] **Step 1: Write failing tests using a fake runner.**

```python
def test_same_day_runs_get_distinct_ids_and_manifests(tmp_path):
    first = new_run_id(datetime(2026, 9, 8, tzinfo=timezone.utc), "aaaaaa")
    second = new_run_id(datetime(2026, 9, 8, tzinfo=timezone.utc), "bbbbbb")
    assert first != second
    assert first.startswith("20260908T")


def test_atomic_manifest_replace_recovers_without_partial_json(tmp_path):
    path = tmp_path / "manifest.json"
    atomic_json_write(path, {"run_id": "synthetic", "status": "running"})
    assert json.loads(path.read_text())["status"] == "running"
    assert not list(tmp_path.glob("*.tmp"))
```

- [ ] **Step 2: Run targeted tests and verify failure.**

Run: `.venv/bin/python -m pytest tests/test_stage3_orchestrator.py -q`  
Expected: FAIL because the module and interfaces do not exist.

- [ ] **Step 3: Implement atomic state and injected runner.**

Create a run directory under configured state root/date/run_id, write only non-secret manifest
fields, and update it with `NamedTemporaryFile`, flush, `os.fsync`, and `os.replace`. Use the
existing lock path semantics for overlap refusal. The production runner launches
`scripts/run_daily.sh` with `Stage3Config.child_env` plus `RUN_ID`, `OUTPUT_ROOT`, `JOB_COUNT`,
and `GEO_FILTER`; never pass credentials in argv.

- [ ] **Step 4: Implement log tailing, cancellation, timeout, and retention.**

Tail and replay logs into `ProgressTracker`, notify subscribers, emit one waiting event after
120 seconds and every 120 seconds thereafter, send SIGTERM then bounded SIGKILL, enforce max
runtime, and retain seven newest date directories while skipping the active one. Cleanup is
non-fatal. Deduplicate early and final terminal notifications through manifest metadata.

- [ ] **Step 5: Run tests and commit.**

Run: `.venv/bin/python -m pytest tests/test_stage3_orchestrator.py -q && .venv/bin/python -m py_compile stage_3/orchestrator.py`  
Expected: PASS.


---

### Task 5: Add crash-safe cron schedule storage

**Files:**
- Create: `stage_3/schedules.py`
- Create: `tests/test_stage3_schedules.py`

**Interfaces:**
- `Schedule(id: str, kind: str, expression: str, geo: str | None, job_count: int, timezone: str, enabled: bool, last_started_at: str | None, next_run_at: str | None)`.
- `validate_cron(expression: str) -> tuple[int, int, int, int, int]`.
- `cron_matches(expression: str, moment: datetime, timezone: str) -> bool`.
- `ScheduleStore.load() -> list[Schedule]`, `save(records: list[Schedule]) -> None`.
- `due(records: list[Schedule], now: datetime) -> list[Schedule]`.

- [ ] **Step 1: Write failing tests for cron, atomic primary/backup, and corruption recovery.**

```python
def test_weekday_cron_matches_local_time():
    assert cron_matches("0 8 * * 1-5", datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc), "UTC")


def test_corrupt_primary_recovers_backup(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    record = synthetic_schedule("s17")
    store.save([record])
    store.path.write_text("{broken")
    assert store.load() == [record]
```

- [ ] **Step 2: Run targeted tests and verify failure.**

Run: `.venv/bin/python -m pytest tests/test_stage3_schedules.py -q`  
Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement strict five-field cron parsing and matching.**

Support numeric values, `*`, comma lists, ranges, and step expressions. Reject malformed or
out-of-range fields before persistence. Use `zoneinfo.ZoneInfo` for the configured timezone.
Validate schedule IDs and count bounds; serialize only non-secret fields.

- [ ] **Step 4: Implement atomic store and backup recovery.**

Write a same-directory temp file, flush/fsync, set `0600`, atomically replace the primary, then
maintain the backup of the last known-good state. Validate primary and backup against the schedule
schema. Never guess when both are corrupt; raise a typed error and disable scheduling.

- [ ] **Step 5: Run tests and commit.**

Run: `.venv/bin/python -m pytest tests/test_stage3_schedules.py -q`  
Expected: PASS.

```bash
git add stage_3/schedules.py tests/test_stage3_schedules.py
git commit -m "feat(stage3): persist validated cron schedules safely"
```

---

### Task 6: Implement the Telegram bot and shared scheduler loop

**Files:**
- Create: `stage_3/bot.py`
- Create: `tests/test_stage3_bot.py`
- Modify: `stage_3/__init__.py`

**Interfaces:**
- `build_application(config: Stage3Config, orchestrator: Orchestrator, schedules: ScheduleStore) -> Application`.
- Handlers `start`, `status`, `run`, `cancel`, `schedule`, `schedule_recurring`, `list_schedules`, `cancel_schedule`.
- `scheduler_tick(context) -> None` calls `Orchestrator.start(RunRequest(geo, job_count, run_id=None))` for due records.

- [ ] **Step 1: Write failing fake-update tests.**

```python
@pytest.mark.asyncio
async def test_unauthorized_user_is_refused(fake_update, app):
    fake_update.effective_user.id = 999
    await start(fake_update, fake_context())
    assert "not authorized" in fake_update.reply_text.call_args[0][0].lower()

@pytest.mark.asyncio
async def test_run_command_starts_same_orchestrator_path(fake_update, fake_orchestrator):
    fake_update.message.text = "/run Germany 10"
    await run(fake_update, fake_context(orchestrator=fake_orchestrator))
    assert fake_orchestrator.requests[0] == RunRequest("Germany", 10)
```

- [ ] **Step 2: Run targeted tests and verify failure.**

Run: `.venv/bin/python -m pytest tests/test_stage3_bot.py -q`  
Expected: FAIL because the module/handlers do not exist.

- [ ] **Step 3: Implement authorization, `/start`, `/status`, and `/run`.**

Use `Stage3Config.is_authorised` before every command and callback. Validate optional geos against
`known_geos`, present count buttons for 5/10/20/25/custom, validate custom counts, and keep pending
selection only in memory. Reject active-run starts with the overlap error.

- [ ] **Step 4: Implement progress editing and cancellation.**

Send one initial message, subscribe to the `RunHandle`, render through `render_run`, and edit only
when text changed. Throttle edits to avoid rate limits. `/status` replays the active manifest/log;
`/cancel` calls `RunHandle.cancel`. Never send PDFs.

- [ ] **Step 5: Implement scheduling commands and scheduler task.**

Parse one-shot timestamps and recurring five-field cron, validate geos/counts, save records via
`ScheduleStore`, list sanitized fields, cancel by short ID, and run one scheduler tick per minute.
Mark due records before launch to prevent duplicate launches; invoke the same `Orchestrator.start`
used by manual runs. Notify the configured chat on recovery failure and schedule results.

- [ ] **Step 6: Run tests and commit.**

Run: `.venv/bin/python -m pytest tests/test_stage3_bot.py -q && .venv/bin/python -m py_compile stage_3/bot.py`  
Expected: PASS.


---

### Task 7: Add shared run-scoped document output paths

**Files:**
- Create: `tests/test_stage3_outputs.py`
- Modify: `scripts/telegram_select.py`
- Modify: `scripts/generate_batch.py`
- Modify: `scripts/run_daily.sh`

**Interfaces:**
- `output_paths(repo: Path, day: str, run_id: str, slug: str) -> dict[str, Path]` returns four artifact paths.
- `is_complete(slug: str, output_root: Path | None = None) -> bool` checks four non-empty artifacts.
- `legacy_is_complete(slug: str) -> bool` recognizes old paths without modifying them.

- [ ] **Step 1: Write failing path and legacy tests.**

```python
def test_same_day_runs_do_not_share_output_paths(tmp_path):
    one = output_paths(tmp_path, "2026-09-08", "run-a", "acme-role")
    two = output_paths(tmp_path, "2026-09-08", "run-b", "acme-role")
    assert one["cv_tex"] != two["cv_tex"]
    assert "2026-09-08/run-a" in str(one["cv_tex"])


def test_complete_legacy_output_is_detected_without_moving_it(tmp_path):
    legacy = tmp_path / "cv" / "acme-role"
    letters = tmp_path / "cover_letters" / "acme-role"
    legacy.mkdir(parents=True)
    letters.mkdir(parents=True)
    for path in (
        legacy / "Salman-Resume.tex",
        legacy / "Salman-Resume.pdf",
        letters / "Salman-Cover-Letter.tex",
        letters / "Salman-Cover-Letter.pdf",
    ):
        path.write_text("x")
    assert legacy_is_complete("acme-role")
    assert all(path.exists() for path in (legacy / "Salman-Resume.tex", letters / "Salman-Cover-Letter.pdf"))
```

- [ ] **Step 2: Run targeted tests and verify failure.**

Run: `.venv/bin/python -m pytest tests/test_stage3_outputs.py -q`  
Expected: FAIL because the shared helper does not exist.

- [ ] **Step 3: Implement shared path helper and thread it through selection/batch generation.**

Use `cv/YYYY-MM-DD/<run_id>/<slug>/Salman-Resume.tex`, `cv/YYYY-MM-DD/<run_id>/<slug>/Salman-Resume.pdf`,
`cover_letters/YYYY-MM-DD/<run_id>/<slug>/Salman-Cover-Letter.tex`, and
`cover_letters/YYYY-MM-DD/<run_id>/<slug>/Salman-Cover-Letter.pdf`. Pass `--run-id`/`--output-root`
to batch generation, preserve sequential/quota behavior, include output root in state, and return
actual relative paths. Keep complete legacy applications read-only skips; never delete partial
legacy outputs.

- [ ] **Step 4: Thread safe environment values through `run_daily.sh`.**

Add validated `RUN_ID`, `OUTPUT_ROOT`, and `JOB_COUNT` defaults. Preserve date-keyed rankset,
pending-selection, and legacy temp aliases where required. Include run ID in new log/report/manifest
metadata without putting posting text or secrets into paths.

- [ ] **Step 5: Run tests and shell syntax.**

Run: `.venv/bin/python -m pytest tests/test_stage3_outputs.py tests/test_telegram_select.py -q && bash -n scripts/run_daily.sh`  
Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add scripts/telegram_select.py scripts/generate_batch.py scripts/run_daily.sh tests/test_stage3_outputs.py
git commit -m "feat(pipeline): isolate generated applications by run"
```

---

### Task 8: Add optional Playwright provider and session-state helper

**Files:**
- Create: `scripts/linkedin_playwright.py`
- Create: `scripts/linkedin_session.py`
- Create: `tests/test_linkedin_playwright.py`
- Modify: `scripts/enrich_linkedin.py`
- Create: `requirements-stage3.txt` (optional pinned dependency)

**Interfaces:**
- `PlaywrightDetailProvider(storage_state: Path | None, email_env: str = "LINKEDIN_EMAIL", password_env: str = "LINKEDIN_PASSWORD", headless: bool = True, timeout: float = 30.0, browser_factory=None)`.
- `PlaywrightDetailProvider.fetch(job_id: str, ledger: RequestLedger) -> dict[str, str]`.
- Typed errors for login wall, challenge, missing content, timeout, and budget exhaustion.
- `scripts/linkedin_session.py` terminal helper creates owner-only external storage state without printing values.

- [ ] **Step 1: Write fake-browser failing tests.**

```python
def test_semantic_description_is_extracted_after_delayed_hydration(fake_browser):
    provider = PlaywrightDetailProvider(browser_factory=fake_browser)
    assert provider.fetch("123", ledger=FakeLedger(5))["description"] == "A sufficiently long synthetic job description for testing."


def test_captcha_causes_fallback_error_and_closes_resources(fake_browser):
    fake_browser.page_text = "Please complete CAPTCHA"
    with pytest.raises(PlaywrightChallengeError):
        PlaywrightDetailProvider(browser_factory=fake_browser).fetch("123", FakeLedger(5))
    assert fake_browser.closed
```

- [ ] **Step 2: Run tests and verify failure.**

Run: `.venv/bin/python -m pytest tests/test_linkedin_playwright.py -q`  
Expected: FAIL because the provider does not exist.

- [ ] **Step 3: Implement bounded provider with storage-state-first authentication.**

Read credentials only from `os.environ`; prefer configured storage state, then email/password,
then raise typed operator-action errors. Use owned temporary context by default, optional explicit
profile, bounded navigation/poll timeouts, numeric canonical job URL, semantic description
extraction, page-furniture/login/challenge detection, and `finally` cleanup. Increment
`RequestLedger` before every navigation attempt and stop when budget is exhausted.

- [ ] **Step 4: Add provider selection to existing enrichment.**

Keep Kimi WebBridge primary and guest fallback behavior. Enable Playwright only when configured;
pass the existing ledger and convert typed errors into the established `DetailError` fallback path.
Do not run both authenticated providers for every job.

- [ ] **Step 5: Add terminal-only session helper and optional dependency docs.**

The helper writes outside the repo, sets `0600`, avoids printing cookie/storage values, and never
accepts a Telegram upload. Pin a Playwright version in the optional requirements file and document
separate browser installation.

- [ ] **Step 6: Run tests and commit.**

Run: `.venv/bin/python -m pytest tests/test_linkedin_playwright.py tests/test_enrich_linkedin.py -q`  
Expected: PASS without a live browser or credentials.

```bash
git add scripts/linkedin_playwright.py scripts/linkedin_session.py scripts/enrich_linkedin.py tests/test_linkedin_playwright.py requirements-stage3.txt
git commit -m "feat(enrich): add optional session-aware Playwright fallback"
```
