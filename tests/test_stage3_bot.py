"""Task 6 bot tests: fake updates drive the real handlers, offline.

python-telegram-bot is imported eagerly by `stage_3.bot`, so the selected test
interpreter must have the exactly pinned Stage 3 dependencies installed.
Coroutines are driven with an explicit loop helper instead of pytest-asyncio
(not installed; deliberate).

The handlers must treat `update`/`context` duck-typed: every attribute they read
exists on the fakes below, and nothing PTB-specific is poked.
"""
import asyncio
import json
from datetime import datetime, timezone

import pytest
import telegram  # noqa: F401 — fail clearly when Stage 3 dependencies are absent

from stage_3.bot import (
    build_application,
    cancel,
    cancel_schedule,
    custom_callback,
    geo_callback,
    count_callback,
    custom_count_text,
    health,
    help_command,
    list_schedules,
    _start_background_tasks,
    _stop_background_tasks,
    run,
    schedule,
    schedule_recurring,
    scheduler_tick,
    start,
    status,
    unschedule,
    restore_active_run,
)
from stage_3.config import Stage3Config
from stage_3.orchestrator import RunRefusedError, RunRequest, RunResult
from stage_3.progress import RunState
from stage_3.schedules import Schedule, ScheduleStore


def run_coro(coro):
    """Drive one coroutine and let any tasks it spawned finish cleanly."""
    loop = asyncio.new_event_loop()
    try:
        task = loop.create_task(coro)
        loop.run_until_complete(task)
        pending = [t for t in asyncio.all_tasks(loop)
                   if t is not task and not t.done()]
        if pending:
            loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True))
        return task.result()
    finally:
        loop.close()


OWNER_ID = 42
SYNTHETIC_CONFIG = Stage3Config(
    bot_token="123456:synthetic-token",
    chat_id=OWNER_ID,
    allowed_user_ids=(OWNER_ID,),
)

GEOS = ["Germany", "United Kingdom", "Netherlands"]


# -- fakes ------------------------------------------------------------------


class FakeBot:
    def __init__(self):
        self.sent = []
        self.edits = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return FakeMessage()

    async def edit_message_text(self, text, chat_id=None, message_id=None, **kwargs):
        self.edits.append((text, chat_id, message_id, kwargs))


class FakeChat:
    def __init__(self, bot):
        self.bot = bot
        self.id = OWNER_ID
        self.sent = []

    async def send_message(self, text, **kwargs):
        self.sent.append((text, kwargs))
        return FakeMessage()


class FakeMessage:
    message_id = 77

    def __init__(self, text=""):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return self

    async def edit_text(self, text, **kwargs):
        self.replies.append(("EDIT:" + text, kwargs))
        return self


class FakeUser:
    def __init__(self, user_id=OWNER_ID):
        self.id = user_id


class FakeCallbackQuery:
    def __init__(self, data, user_id=OWNER_ID):
        self.data = data
        self.from_user = FakeUser(user_id)
        self.answered = []
        self.message = FakeMessage()

    async def answer(self, text=None, show_alert=None):
        self.answered.append(text)


class FakeUpdate:
    def __init__(self, message=None, callback=None, user_id=OWNER_ID, chat=None):
        self.message = message
        self.callback_query = callback
        self.effective_user = callback.from_user if callback else FakeUser(user_id)
        self.effective_message = message or (callback.message if callback else None)
        self.effective_chat = chat or FakeChat(FakeBot())


class FakeHandle:
    def __init__(self, run_id="20260908T120000Z-aaaaaa", running=False):
        self.run_id = run_id
        self.state = RunState()
        self.is_running = running
        self.cancelled = False
        self.subscribers = []
        self.manifest_path = None

    def subscribe(self, callback):
        self.subscribers.append(callback)

    def cancel(self):
        self.cancelled = True

    def wait(self, timeout=None):
        return None


class FakeOrchestrator:
    def __init__(self, refuse=False, restored=None):
        self.requests = []
        self.refuse = refuse
        self.handles = []
        self.restored = restored
        self.restore_roots = []

    def start(self, request, **kwargs):
        self.requests.append(request)
        if self.refuse:
            raise RunRefusedError(
                "A run is already being supervised (old-id). Concurrent runs are "
                "refused, not queued — /cancel it first."
            )
        handle = FakeHandle(running=False)
        self.handles.append(handle)
        return handle

    def restore_active(self, state_root=None):
        self.restore_roots.append(state_root)
        return self.restored


class FakeContext:
    def __init__(self, tmp_path, orchestrator=None, args=None, store=None,
                 user_id=OWNER_ID, geos=None):
        self.bot = FakeBot()
        self.chat_data = {}
        self.user_data = {}
        self.args = args or []
        self.bot_data = {
            "config": Stage3Config(
                bot_token="123456:synthetic-token",
                chat_id=OWNER_ID,
                allowed_user_ids=(OWNER_ID,),
                run_state_root=tmp_path / "runs",
            ),
            "orchestrator": orchestrator or FakeOrchestrator(),
            "schedules": store or ScheduleStore(tmp_path / "schedules.json"),
            "geos": GEOS if geos is None else geos,
            "chat": None,
        }


# -- authorization and /start -----------------------------------------------


def test_unauthorized_user_is_refused(tmp_path):
    update = FakeUpdate(message=FakeMessage("/start"), user_id=999)
    run_coro(start(update, FakeContext(tmp_path)))
    assert "not authorized" in update.message.replies[0][0].lower()


def test_unauthorized_callback_is_refused_and_answered(tmp_path):
    callback = FakeCallbackQuery("count:10", user_id=999)
    update = FakeUpdate(callback=callback)
    run_coro(count_callback(update, FakeContext(tmp_path)))
    assert "not authorized" in " ".join(callback.answered).lower()


def test_start_greets_and_lists_commands(tmp_path):
    update = FakeUpdate(message=FakeMessage("/start"))
    run_coro(start(update, FakeContext(tmp_path)))
    text = update.message.replies[0][0]
    for command in ("/run", "/status", "/cancel", "/schedule"):
        assert command in text


def test_help_is_authorized_and_lists_new_commands(tmp_path):
    denied = FakeUpdate(message=FakeMessage("/help"), user_id=999)
    run_coro(help_command(denied, FakeContext(tmp_path)))
    assert "not authorized" in denied.message.replies[0][0].lower()

    update = FakeUpdate(message=FakeMessage("/help"))
    run_coro(help_command(update, FakeContext(tmp_path)))
    text = update.message.replies[0][0]
    assert "/help" in text and "/unschedule" in text


# -- /run -------------------------------------------------------------------


def test_run_with_geo_and_count_starts_orchestrator(tmp_path):
    orchestrator = FakeOrchestrator()
    update = FakeUpdate(message=FakeMessage("/run Germany 10"))
    update.effective_chat = FakeChat(FakeBot())
    context = FakeContext(tmp_path, orchestrator=orchestrator, args=["Germany", "10"])
    run_coro(run(update, context))
    assert orchestrator.requests[0] == RunRequest(geo="Germany", job_count=10)
    # One initial progress message went out.
    assert update.effective_chat.sent


def test_run_renders_when_handle_state_is_initially_none(tmp_path):
    class NoneStateOrchestrator(FakeOrchestrator):
        def start(self, request, **kwargs):
            handle = super().start(request, **kwargs)
            handle.state = None
            return handle

    update = FakeUpdate(message=FakeMessage("/run Germany 10"))
    update.effective_chat = FakeChat(FakeBot())
    context = FakeContext(
        tmp_path,
        orchestrator=NoneStateOrchestrator(),
        args=["Germany", "10"],
    )

    run_coro(run(update, context))

    assert update.effective_chat.sent
    assert "Run" in update.effective_chat.sent[0][0]


def test_run_unknown_geo_never_reaches_the_orchestrator(tmp_path):
    context = FakeContext(tmp_path)
    update = FakeUpdate(message=FakeMessage("/run Atlantis 10"))
    context.args = ["Atlantis", "10"]
    run_coro(run(update, context))
    assert context.bot_data["orchestrator"].requests == []
    assert "Atlantis" in update.message.replies[0][0]
    assert "Germany" in update.message.replies[0][0]  # suggestions included


def test_run_without_args_offers_geo_keyboard(tmp_path):
    update = FakeUpdate(message=FakeMessage("/run"))
    run_coro(run(update, FakeContext(tmp_path)))
    text, kwargs = update.message.replies[0]
    flat = [button for row in kwargs["reply_markup"] for button in row]
    assert [b["callback_data"] for b in flat] == ["geo:0", "geo:1", "geo:2"]
    assert flat[0]["text"] == "Germany"


def test_run_refusal_is_surfaced_not_hidden(tmp_path):
    update = FakeUpdate(message=FakeMessage("/run Germany 10"))
    context = FakeContext(tmp_path, orchestrator=FakeOrchestrator(refuse=True), args=["Germany", "10"])
    run_coro(run(update, context))
    assert "refused" in update.message.replies[0][0].lower()


def test_geo_callback_then_count_callback_launches(tmp_path):
    context = FakeContext(tmp_path)
    geo_update = FakeUpdate(callback=FakeCallbackQuery("geo:1"))
    run_coro(geo_callback(geo_update, context))
    assert context.user_data["pending_run"]["geo"] == "United Kingdom"
    # The count keyboard follows the geo pick.
    count_update = FakeUpdate(callback=FakeCallbackQuery("count:25"))
    run_coro(count_callback(count_update, context))
    assert context.bot_data["orchestrator"].requests == [
        RunRequest(geo="United Kingdom", job_count=25)
    ]


def test_custom_count_arrives_via_text_message(tmp_path):
    context = FakeContext(tmp_path)
    run_coro(custom_callback(FakeUpdate(callback=FakeCallbackQuery("custom")), context))
    assert context.user_data.get("await_custom_count") is True
    update = FakeUpdate(message=FakeMessage("7"))
    run_coro(custom_count_text(update, context))
    assert context.bot_data["orchestrator"].requests == [
        RunRequest(geo=None, job_count=7)
    ]
    assert context.user_data.get("await_custom_count") is None


def test_custom_count_out_of_bounds_is_refused(tmp_path):
    context = FakeContext(tmp_path)
    context.user_data["await_custom_count"] = True
    context.user_data["pending_run"] = {"geo": None}
    update = FakeUpdate(message=FakeMessage("9999"))
    run_coro(custom_count_text(update, context))
    assert context.bot_data["orchestrator"].requests == []
    assert "1" in update.message.replies[0][0] and "50" in update.message.replies[0][0]


# -- /status and /cancel ----------------------------------------------------


def test_status_shows_active_run(tmp_path):
    context = FakeContext(tmp_path)
    handle = FakeHandle(running=True)
    handle.state.message = "Phase 2: Ranking jobs via Claude Code..."
    handle.state.current_phase = "2"
    context.bot_data["active_handle"] = handle
    update = FakeUpdate(message=FakeMessage("/status"))
    run_coro(status(update, context))
    assert "Ranking" in update.message.replies[0][0]


def test_status_reports_latest_manifest_when_idle(tmp_path):
    context = FakeContext(tmp_path)
    run_dir = context.bot_data["config"].run_state_root / "2026-09-08" / "rid-1"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps({
        "run_id": "rid-1", "status": "complete", "date": "2026-09-08",
        "geo": "Germany", "job_count": 10, "exit_code": 0, "error_class": None,
    }))
    update = FakeUpdate(message=FakeMessage("/status"))
    run_coro(status(update, context))
    text = update.message.replies[0][0]
    assert "rid-1" in text and "complete" in text


def test_cancel_cancels_active_handle(tmp_path):
    context = FakeContext(tmp_path)
    handle = FakeHandle(running=True)
    context.bot_data["active_handle"] = handle
    update = FakeUpdate(message=FakeMessage("/cancel"))
    run_coro(cancel(update, context))
    assert handle.cancelled
    assert "cancell" in update.message.replies[0][0].lower()


def test_cancel_without_active_run_says_so(tmp_path):
    update = FakeUpdate(message=FakeMessage("/cancel"))
    run_coro(cancel(update, FakeContext(tmp_path)))
    assert "no active run" in update.message.replies[0][0].lower()


def test_startup_restores_active_handle_for_status_and_cancel(tmp_path):
    handle = FakeHandle(running=True)
    handle.state.message = "Phase 2: Ranking jobs via Claude Code..."
    handle.state.current_phase = "2"
    orchestrator = FakeOrchestrator(restored=handle)
    context = FakeContext(tmp_path, orchestrator=orchestrator)

    restored = restore_active_run(context.bot_data)

    assert restored is handle
    assert context.bot_data["active_handle"] is handle
    assert orchestrator.restore_roots == [
        context.bot_data["config"].run_state_root
    ]
    status_update = FakeUpdate(message=FakeMessage("/status"))
    run_coro(status(status_update, context))
    assert "Ranking" in status_update.message.replies[0][0]
    cancel_update = FakeUpdate(message=FakeMessage("/cancel"))
    run_coro(cancel(cancel_update, context))
    assert handle.cancelled


def test_startup_leaves_no_active_handle_for_stale_manifest(tmp_path):
    orchestrator = FakeOrchestrator(restored=None)
    context = FakeContext(tmp_path, orchestrator=orchestrator)

    assert restore_active_run(context.bot_data) is None
    assert "active_handle" not in context.bot_data


# -- scheduling commands ----------------------------------------------------


def test_schedule_creates_once_record(tmp_path):
    context = FakeContext(tmp_path)
    update = FakeUpdate(message=FakeMessage("/schedule 2026-09-10T08:30 Germany 15"))
    context.args = ["2026-09-10T08:30", "Germany", "15"]
    run_coro(schedule(update, context))
    records = context.bot_data["schedules"].load()
    assert len(records) == 1
    record = records[0]
    assert record.kind == "once"
    assert record.expression == "2026-09-10T08:30"
    assert record.geo == "Germany" and record.job_count == 15
    assert "scheduled" in update.message.replies[0][0].lower()


def test_schedule_rejects_bad_time(tmp_path):
    context = FakeContext(tmp_path)
    update = FakeUpdate(message=FakeMessage("/schedule not-a-time"))
    context.args = ["not-a-time"]
    run_coro(schedule(update, context))
    assert context.bot_data["schedules"].load() == []
    assert "could not" in update.message.replies[0][0].lower() or \
        "invalid" in update.message.replies[0][0].lower()


def test_schedule_recurring_creates_cron_record(tmp_path):
    context = FakeContext(tmp_path)
    update = FakeUpdate(message=FakeMessage("/schedule_recurring 0 8 * * 1-5 Germany 10"))
    context.args = ["0", "8", "*", "*", "1-5", "Germany", "10"]
    run_coro(schedule_recurring(update, context))
    record = context.bot_data["schedules"].load()[0]
    assert record.kind == "recurring"
    assert record.expression == "0 8 * * 1-5"
    assert record.geo == "Germany" and record.job_count == 10
    assert record.next_run_at is not None


def test_schedule_recurring_rejects_bad_cron(tmp_path):
    context = FakeContext(tmp_path)
    update = FakeUpdate(message=FakeMessage("/schedule_recurring 0 8 * *"))
    context.args = ["0", "8", "*", "*"]
    run_coro(schedule_recurring(update, context))
    assert context.bot_data["schedules"].load() == []


def test_list_and_cancel_schedule_roundtrip(tmp_path):
    context = FakeContext(tmp_path)
    store = context.bot_data["schedules"]
    store.save([Schedule(id="s1", kind="recurring", expression="0 8 * * 1-5",
                         geo=None, job_count=10, timezone="UTC")])
    listing = FakeUpdate(message=FakeMessage("/list_schedules"))
    run_coro(list_schedules(listing, context))
    assert "s1" in listing.message.replies[0][0]
    cancel_update = FakeUpdate(message=FakeMessage("/cancel_schedule s1"))
    context.args = ["s1"]
    run_coro(cancel_schedule(cancel_update, context))
    assert store.load() == []
    assert "cancel" in cancel_update.message.replies[0][0].lower()


def test_cancel_schedule_unknown_id_is_reported(tmp_path):
    context = FakeContext(tmp_path)
    update = FakeUpdate(message=FakeMessage("/cancel_schedule nope"))
    context.args = ["nope"]
    run_coro(cancel_schedule(update, context))
    assert "no schedule" in update.message.replies[0][0].lower()


def test_unschedule_without_id_lists_saved_schedules(tmp_path):
    context = FakeContext(tmp_path)
    context.bot_data["schedules"].save([
        Schedule(id="s1", kind="once", expression="2026-09-10T08:30",
                 geo=None, job_count=10, timezone="UTC")
    ])
    update = FakeUpdate(message=FakeMessage("/unschedule"))
    run_coro(unschedule(update, context))
    assert "s1" in update.message.replies[0][0]


def test_unschedule_removes_known_id_and_reports_unknown(tmp_path):
    context = FakeContext(tmp_path)
    context.bot_data["schedules"].save([
        Schedule(id="s1", kind="once", expression="2026-09-10T08:30",
                 geo=None, job_count=10, timezone="UTC")
    ])
    removed = FakeUpdate(message=FakeMessage("/unschedule s1"))
    context.args = ["s1"]
    run_coro(unschedule(removed, context))
    assert context.bot_data["schedules"].load() == []
    assert "removed" in removed.message.replies[0][0].lower()

    unknown = FakeUpdate(message=FakeMessage("/unschedule nope"))
    context.args = ["nope"]
    run_coro(unschedule(unknown, context))
    assert "no schedule" in unknown.message.replies[0][0].lower()


def test_unauthorized_unschedule_cannot_remove_schedule(tmp_path):
    context = FakeContext(tmp_path)
    context.bot_data["schedules"].save([
        Schedule(id="s1", kind="once", expression="2026-09-10T08:30",
                 geo=None, job_count=10, timezone="UTC")
    ])
    update = FakeUpdate(message=FakeMessage("/unschedule s1"), user_id=999)
    context.args = ["s1"]
    run_coro(unschedule(update, context))
    assert [record.id for record in context.bot_data["schedules"].load()] == ["s1"]


def test_unschedule_persistence_failure_is_reported_without_success(tmp_path):
    record = Schedule(id="s1", kind="once", expression="2026-09-10T08:30",
                      geo=None, job_count=10, timezone="UTC")

    class SaveFailingStore:
        def load(self):
            return [record]

        def save(self, records):
            from stage_3.schedules import ScheduleStoreError

            raise ScheduleStoreError("synthetic persistence failure")

    context = FakeContext(tmp_path, store=SaveFailingStore())
    context.args = ["s1"]
    update = FakeUpdate(message=FakeMessage("/unschedule s1"))
    run_coro(unschedule(update, context))

    text = update.message.replies[0][0].lower()
    assert "could not cancel" in text
    assert "removed schedule" not in text


def test_all_expected_commands_are_registered(tmp_path):
    application = build_application(
        SYNTHETIC_CONFIG,
        FakeOrchestrator(),
        ScheduleStore(tmp_path / "commands.json"),
        geos=GEOS,
    )
    commands = {
        command
        for group in application.handlers.values()
        for handler in group
        if hasattr(handler, "commands")
        for command in handler.commands
    }
    assert commands == {
        "start", "help", "health", "ping", "status", "run", "cancel",
        "schedule", "schedule_recurring", "list_schedules",
        "cancel_schedule", "unschedule",
    }


# -- scheduler tick ----------------------------------------------------------


def test_scheduler_tick_launches_due_and_marks_it(tmp_path):
    context = FakeContext(tmp_path)
    store = context.bot_data["schedules"]
    store.save([Schedule(id="s1", kind="recurring", expression="0 8 * * 1-5",
                         geo="Germany", job_count=10, timezone="UTC")])
    now = datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc)
    run_coro(scheduler_tick(context, now=now))
    orchestrator = context.bot_data["orchestrator"]
    assert orchestrator.requests == [RunRequest(geo="Germany", job_count=10)]
    record = store.load()[0]
    assert record.last_started_at is not None
    # A second tick in the same minute must not launch twice.
    run_coro(scheduler_tick(context, now=now))
    assert len(orchestrator.requests) == 1


def test_scheduler_tick_skips_untimed_records(tmp_path):
    context = FakeContext(tmp_path)
    store = context.bot_data["schedules"]
    store.save([Schedule(id="s2", kind="recurring", expression="0 9 * * 1-5",
                         geo=None, job_count=5, timezone="UTC")])
    run_coro(scheduler_tick(context, now=datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc)))
    assert context.bot_data["orchestrator"].requests == []


def test_scheduler_tick_aborts_launch_when_mark_started_save_fails(tmp_path):
    due_record = Schedule(
        id="s-save-fail", kind="recurring", expression="0 8 * * 1-5",
        geo="Germany", job_count=10, timezone="UTC",
    )

    class SaveFailingStore:
        def load(self):
            return [due_record]

        def save(self, records):
            from stage_3.schedules import ScheduleStoreError

            raise ScheduleStoreError("synthetic persistence failure")

    context = FakeContext(tmp_path, store=SaveFailingStore())
    now = datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc)

    run_coro(scheduler_tick(context, now=now))

    assert context.bot_data["orchestrator"].requests == []
    assert any(
        "persist schedule state" in text.lower()
        for _, text, _ in context.bot.sent
    )


def test_scheduler_save_failure_remains_due_after_restart(tmp_path):
    from stage_3.schedules import ScheduleStoreError, due

    persistent = ScheduleStore(tmp_path / "restart-schedules.json")
    persistent.save([
        Schedule(
            id="s-restart", kind="recurring", expression="0 8 * * 1-5",
            geo="Germany", job_count=10, timezone="UTC",
        )
    ])

    class SaveFailingStore:
        def load(self):
            return persistent.load()

        def save(self, records):
            raise ScheduleStoreError("synthetic persistence failure")

    now = datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc)
    failed_context = FakeContext(tmp_path, store=SaveFailingStore())

    run_coro(scheduler_tick(failed_context, now=now))

    assert failed_context.bot_data["orchestrator"].requests == []
    unchanged = persistent.load()[0]
    assert unchanged.last_started_at is None
    assert due([unchanged], now) == [unchanged]

    # A fresh context/store simulates a process restart reading durable state.
    restarted_store = ScheduleStore(persistent.path)
    restarted_context = FakeContext(tmp_path, store=restarted_store)
    run_coro(scheduler_tick(restarted_context, now=now))

    assert restarted_context.bot_data["orchestrator"].requests == [
        RunRequest(geo="Germany", job_count=10)
    ]
    assert restarted_store.load()[0].last_started_at == now.isoformat()


def test_scheduler_tick_surfaces_refusal_to_the_chat(tmp_path):
    context = FakeContext(tmp_path, orchestrator=FakeOrchestrator(refuse=True))
    store = context.bot_data["schedules"]
    store.save([Schedule(id="s3", kind="recurring", expression="0 8 * * 1-5",
                         geo=None, job_count=10, timezone="UTC")])
    run_coro(scheduler_tick(context, now=datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc)))
    record = store.load()[0]
    assert record.last_started_at is not None  # marked, so no per-minute hammering
    assert context.bot.sent  # the refusal reached the owner's chat


def test_scheduler_tick_reports_corrupt_store_and_disables(tmp_path):
    context = FakeContext(tmp_path)
    store = context.bot_data["schedules"]
    store.save([Schedule(id="s4", kind="recurring", expression="0 8 * * 1-5",
                         geo=None, job_count=10, timezone="UTC")])
    store.path.write_text("{corrupt")
    store.backup_path.write_text("corrupt too")
    run_coro(scheduler_tick(context, now=datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc)))
    assert context.bot_data["orchestrator"].requests == []
    assert any("schedule" in text.lower() for _, text, _ in context.bot.sent)


def test_health_reports_uptime_idle_state_and_stats(tmp_path):
    context = FakeContext(tmp_path)
    message = FakeMessage()
    update = FakeUpdate(message=message)

    run_coro(health(update, context))

    text = message.replies[0][0]
    assert "Bot is running" in text
    assert "Uptime:" in text
    assert "Run: idle" in text
    assert "Schedules: 0" in text


def test_health_reports_active_run(tmp_path):
    context = FakeContext(tmp_path)
    context.bot_data["active_handle"] = FakeHandle(run_id="run-active", running=True)
    message = FakeMessage()

    run_coro(health(FakeUpdate(message=message), context))

    assert "run-active" in message.replies[0][0]


def test_background_tasks_start_only_after_application_is_running():
    class FakeApplication:
        def __init__(self):
            self.bot_data = {}

    application = FakeApplication()

    run_coro(_start_background_tasks(application))

    assert len(application.bot_data["background_tasks"]) == 1
    application.bot_data["background_tasks"][0].cancel()


def test_background_tasks_are_cancelled_during_shutdown():
    class FakeTask:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

        def __await__(self):
            async def done():
                return None
            return done().__await__()

    task = FakeTask()
    application = type("Application", (), {
        "bot_data": {"background_tasks": [task]},
    })()

    run_coro(_stop_background_tasks(application))

    assert task.cancelled is True
    assert "background_tasks" not in application.bot_data


# -- application wiring ------------------------------------------------------


def test_build_application_registers_every_command():
    app = build_application(
        config=SYNTHETIC_CONFIG,
        orchestrator=FakeOrchestrator(),
        schedules=ScheduleStore("/tmp/synthetic-schedules.json"),
        geos=GEOS,
    )
    registered = set()
    for group in app.handlers.values():
        for handler in group:
            commands = getattr(handler, "commands", None)
            if commands:
                registered.update(commands)
    assert registered == {
        "start", "help", "health", "ping", "status", "run", "cancel",
        "schedule", "schedule_recurring", "list_schedules", "cancel_schedule",
        "unschedule",
    }
    assert app.bot_data["geos"] == GEOS
    assert isinstance(app.bot_data["schedules"], ScheduleStore)
