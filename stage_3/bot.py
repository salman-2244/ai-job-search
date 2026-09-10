"""The Stage 3 Telegram bot: commands, keyboards, live progress, schedules.

Authorization is the first thing every handler does: `Stage3Config.is_authorised`
gates all commands and callbacks, and the allowlist is never modified from
Telegram — access changes happen at the terminal, in the env file.

Everything the handlers need arrives through `context.bot_data` (config,
orchestrator, schedule store, geo list), so the same functions run against the
real PTB `Application` and against the fake contexts in the tests.

Callback payloads stay tiny (`count:25`, `geo:3`, `custom`) — Telegram caps
callback data at 64 bytes and no user-facing string belongs in a payload.

PDFs are never sent here: the bot reports where documents landed on disk and
stops. Telegram messages are untrusted input; nothing a message says can change
the allowlist, the token, or the pipeline's gates.
"""

from __future__ import annotations

import asyncio
import dataclasses
import html
import json
import logging
import re
import secrets
import subprocess
import sys
import time
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from .config import Stage3Config, load_config
from .diagnostics import (
    callable_reference,
    configure_diagnostics,
    prepare_telegram_text,
    safe_preview,
)
from .orchestrator import Orchestrator, RunRefusedError, RunRequest
from .progress import RunState
from .render import count_keyboard, geo_keyboard, render_run
from .schedules import (
    MAX_JOB_COUNT,
    Schedule,
    ScheduleStore,
    ScheduleStoreError,
    due,
    next_run,
    validate_cron,
)

#: How often the progress editor re-renders between subscriber wake-ups. State
#: only changes on meaningful log lines, so this bounds edit rate without a
#: dedicated token bucket.
EDITOR_POLL_SECONDS = 3.0
SCHEDULER_INTERVAL_SECONDS = 60.0

LOGGER = logging.getLogger("stage_3.bot")

_DATE_DIR_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


# -- authorization -----------------------------------------------------------


async def _authorised(update, context) -> bool:
    """Gate every entry point; refuse with an answer, never with silence."""
    config: Stage3Config = context.bot_data["config"]
    user = update.effective_user
    if user is not None and config.is_authorised(user.id):
        return True
    refusal = "⛔ You are not authorized to use this bot. Your id was not answered."
    callback = update.callback_query
    if callback is not None:
        await callback.answer("⛔ Not authorized.", show_alert=False)
    elif update.effective_message is not None:
        await update.effective_message.reply_text(refusal)
    return False


# -- geo helpers --------------------------------------------------------------


def _normalize_geo(name: str) -> str:
    return " ".join(name.casefold().replace("_", " ").split())


def _resolve_geo(requested: str, geos: list[str]) -> str | None:
    """Slug-match a requested geo against the matrix's list (case/underscore safe)."""
    wanted = _normalize_geo(requested)
    for geo in geos:
        if _normalize_geo(geo) == wanted:
            return geo
    return None


def _load_geos(repo: Path) -> list[str]:
    """Geos from `build_search_plan.py --list-geos`, so buttons can never name a
    geo the matrix cannot search. Degrades to an empty list on any failure."""
    script = Path(repo) / "scripts" / "build_search_plan.py"
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--list-geos"],
            capture_output=True, text=True, timeout=30, check=False, cwd=str(repo),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


# -- commands -----------------------------------------------------------------


async def start(update, context) -> None:
    if not await _authorised(update, context):
        return
    await update.effective_message.reply_text(
        "👋 <b>Job search control</b>\n\n"
        "/run [geo] [count] — start a run now (e.g. <code>/run Germany 10</code>)\n"
        "/status — live progress or the last run's outcome\n"
        "/health — bot uptime, active run, and schedule count\n"
        "/cancel — stop the active run\n\n"
        "<b>Scheduling</b>\n"
        "/schedule &lt;YYYY-MM-DDTHH:MM&gt; [geo] [count] [tz=Zone] — one-shot\n"
        "/schedule_recurring &lt;min hr dom mon dow&gt; [geo] [count] [tz=Zone]\n"
        "/list_schedules — show saved schedules\n"
        "/cancel_schedule &lt;id&gt; — remove one\n\n"
        "Documents are written to disk only — nothing is sent here as a file.\n"
        "Times default to UTC; set <code>tz=Europe/Berlin</code> to change that.",
        parse_mode=ParseMode.HTML,
    )


async def _refuse_geo(update, requested: str, geos: list[str]) -> None:
    known = ", ".join(geos[:6]) + ("…" if len(geos) > 6 else "")
    await update.effective_message.reply_text(
        f"❓ Unknown geo <b>{html.escape(requested)}</b>. Known geos: {known}.\n"
        "Send /run with no arguments to pick from buttons instead.",
        parse_mode=ParseMode.HTML)


async def run(update, context) -> None:
    """/run [geo] [count] — both optional; missing pieces come via keyboards."""
    if not await _authorised(update, context):
        return
    geos: list[str] = context.bot_data["geos"]
    count: int | None = None
    geo_tokens: list[str] = []
    for token in list(context.args):
        if token.isdigit():
            count = int(token)
        else:
            geo_tokens.append(token)
    requested_geo = " ".join(geo_tokens).strip() or None

    geo: str | None = None
    if requested_geo is not None:
        geo = _resolve_geo(requested_geo, geos)
        if geo is None:
            await _refuse_geo(update, requested_geo, geos)
            return
    if count is None and geo is not None:
        # Geo known, count missing: ask for the count with buttons.
        context.user_data["pending_run"] = {"geo": geo}
        await update.effective_message.reply_text(
            f"🔎 Region: <b>{html.escape(geo)}</b>. How many jobs?",
            reply_markup=count_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return
    if count is None and geo is None:
        # Nothing given: offer the full picker.
        context.user_data["pending_run"] = {"geo": None}
        await update.effective_message.reply_text(
            "🔎 Which region should I search?",
            reply_markup=geo_keyboard(geos),
            parse_mode=ParseMode.HTML,
        )
        return
    await _launch(update, context, geo, count)


async def _launch(update, context, geo: str | None, count: int) -> None:
    """Start the run through the orchestrator and open the live progress view."""
    orchestrator: Orchestrator = context.bot_data["orchestrator"]
    try:
        handle = orchestrator.start(RunRequest(geo=geo, job_count=count))
    except RunRefusedError as exc:
        await update.effective_message.reply_text(
            f"⛔ Run refused: {html.escape(str(exc))}", parse_mode=ParseMode.HTML)
        return
    context.user_data.pop("pending_run", None)
    context.user_data.pop("await_custom_count", None)
    context.bot_data["active_handle"] = handle
    chat = update.effective_chat
    state = handle.state or RunState()
    header = f"🚀 Run <code>{html.escape(handle.run_id)}</code>\n"
    message = await chat.send_message(
        header + render_run(state, now=time.monotonic()), parse_mode=ParseMode.HTML)
    asyncio.create_task(_progress_editor(
        context.bot, chat.id, message.message_id, handle, context))


async def _progress_editor(bot, chat_id, message_id, handle, context) -> None:
    """Edit one message as the run progresses; owns its run's terminal update.

    Polls the handle (state only changes on meaningful log lines) and is woken
    early by the handle's subscription. Edit failures are swallowed and retried
    on the next tick — a transient Telegram hiccup must not kill the monitor.
    """
    loop = asyncio.get_running_loop()
    wake = asyncio.Event()

    def _subscriber(kind, _state):
        loop.call_soon_threadsafe(wake.set)

    handle.subscribe(_subscriber)
    last_text: str | None = None

    async def _edit(text: str) -> None:
        LOGGER.info(
            "telegram api call method=edit_message_text chat_id=%s message_id=%s preview=%s",
            chat_id, message_id, safe_preview(text),
        )
        try:
            result = await bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id,
                parse_mode=ParseMode.HTML,
            )
            LOGGER.info(
                "telegram api success method=edit_message_text chat_id=%s message_id=%s result=%s",
                chat_id, message_id, type(result).__name__,
            )
        except Exception as exc:
            LOGGER.warning(
                "telegram api failure method=edit_message_text chat_id=%s message_id=%s "
                "exception_type=%s preview=%s",
                chat_id, message_id, type(exc).__name__, safe_preview(text),
                exc_info=True,
            )

    try:
        while True:
            state = handle.state
            if state is not None:
                text = render_run(state, now=time.monotonic())
                if text != last_text:
                    await _edit(text)
                    last_text = text
            if not handle.is_running:
                break
            try:
                await asyncio.wait_for(wake.wait(), timeout=EDITOR_POLL_SECONDS)
                wake.clear()
            except asyncio.TimeoutError:
                pass
        # Final render, so the last meaningful line is never lost to the throttle.
        if handle.state is not None:
            text = render_run(handle.state, now=time.monotonic())
            if text != last_text:
                await _edit(text)
    except asyncio.CancelledError:
        raise
    finally:
        if context.bot_data.get("active_handle") is handle:
            context.bot_data.pop("active_handle", None)


async def health(update, context) -> None:
    """Confirm command reception and report only non-sensitive process health."""
    if not await _authorised(update, context):
        return
    started = context.bot_data.get("started_monotonic", time.monotonic())
    uptime = max(0, int(time.monotonic() - started))
    handle = context.bot_data.get("active_handle")
    if handle is not None and handle.is_running:
        active = f"active ({html.escape(handle.run_id)})"
    else:
        active = "idle"
    try:
        schedule_count = len(context.bot_data["schedules"].load())
        schedules = str(schedule_count)
    except ScheduleStoreError:
        schedules = "unavailable"
    await update.effective_message.reply_text(
        "✅ <b>Bot is running.</b>\n"
        f"Uptime: {uptime}s\n"
        f"Run: {active}\n"
        f"Schedules: {schedules}",
        parse_mode=ParseMode.HTML,
    )


async def status(update, context) -> None:
    if not await _authorised(update, context):
        return
    handle = context.bot_data.get("active_handle")
    if handle is not None and handle.is_running:
        state = handle.state or RunState()
        header = f"🚀 Run <code>{html.escape(handle.run_id)}</code>\n"
        await update.effective_message.reply_text(
            header + render_run(state, now=time.monotonic()),
            parse_mode=ParseMode.HTML)
        return
    manifest = _latest_manifest(context.bot_data["config"].run_state_root)
    if manifest is None:
        await update.effective_message.reply_text(
            "📭 No runs yet. Start one with /run.")
        return
    lines = [f"🗂 <b>Last run</b> <code>{html.escape(str(manifest.get('run_id')))}</code>"]
    for key, label in (("status", "Status"), ("geo", "Geo"), ("job_count", "Count"),
                       ("exit_code", "Exit"), ("error_class", "Class")):
        if manifest.get(key) is not None:
            lines.append(f"{label}: {html.escape(str(manifest[key]))}")
    finished = manifest.get("finished_at")
    if finished:
        lines.append(f"Finished: {html.escape(str(finished))}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


def _latest_manifest(root: Path) -> dict | None:
    """The newest run manifest on disk, or None. Best-effort by design."""
    root = Path(root)
    try:
        candidates = sorted(
            (d for d in root.iterdir() if d.is_dir() and _DATE_DIR_RE.fullmatch(d.name)),
            key=lambda d: d.name, reverse=True,
        )
    except OSError:
        return None
    for day_dir in candidates:
        try:
            run_dirs = sorted(day_dir.iterdir(), key=lambda d: d.name, reverse=True)
        except OSError:
            continue
        for run_dir in run_dirs:
            try:
                return json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
    return None


async def cancel(update, context) -> None:
    if not await _authorised(update, context):
        return
    handle = context.bot_data.get("active_handle")
    if handle is None or not handle.is_running:
        await update.effective_message.reply_text(
            "There is no active run to cancel.")
        return
    handle.cancel()
    await update.effective_message.reply_text(
        f"🛑 Cancelling run <code>{html.escape(handle.run_id)}</code> — the pipeline "
        "is asked to stop and the result is reported when it exits.",
        parse_mode=ParseMode.HTML)


# -- count / geo callbacks ----------------------------------------------------


def _pending_geo(context) -> str | None:
    pending = context.user_data.get("pending_run") or {}
    return pending.get("geo")


async def geo_callback(update, context) -> None:
    if not await _authorised(update, context):
        return
    await update.callback_query.answer()
    index = int(update.callback_query.data.split(":", 1)[1])
    geos: list[str] = context.bot_data["geos"]
    if index >= len(geos):
        await update.effective_message.reply_text("That geo option expired — /run again.")
        return
    context.user_data["pending_run"] = {"geo": geos[index]}
    await update.effective_message.reply_text(
        f"🔎 Region: <b>{html.escape(geos[index])}</b>. How many jobs?",
        reply_markup=count_keyboard(),
        parse_mode=ParseMode.HTML,
    )


async def count_callback(update, context) -> None:
    if not await _authorised(update, context):
        return
    await update.callback_query.answer()
    count = int(update.callback_query.data.split(":", 1)[1])
    await _launch(update, context, _pending_geo(context), count)


async def custom_callback(update, context) -> None:
    if not await _authorised(update, context):
        return
    await update.callback_query.answer()
    context.user_data.setdefault("pending_run", {"geo": _pending_geo(context)})
    context.user_data["await_custom_count"] = True
    await update.effective_message.reply_text(
        f"✏️ Send the job count as a number (1–{MAX_JOB_COUNT}).")


async def custom_count_text(update, context) -> None:
    """Plain-text handler: only acts while a custom count is awaited."""
    if not context.user_data.get("await_custom_count"):
        return
    if not await _authorised(update, context):
        return
    raw = (update.effective_message.text or "").strip()
    if not raw.isdigit():
        await update.effective_message.reply_text(
            f"Send a whole number between 1 and {MAX_JOB_COUNT}.")
        return
    count = int(raw)
    if not 1 <= count <= MAX_JOB_COUNT:
        await update.effective_message.reply_text(
            f"Job count must be between 1 and {MAX_JOB_COUNT} — got {count}.")
        return
    context.user_data["await_custom_count"] = None
    await _launch(update, context, _pending_geo(context), count)


# -- scheduling commands ------------------------------------------------------

_TZ_PREFIX = "tz="


def _split_schedule_args(tokens: list[str]) -> tuple[str, int | None, str | None]:
    """Split `[tz=Zone] [count] [geo words …]` leftovers out of the arg list."""
    tz = "UTC"
    count: int | None = None
    geo_tokens: list[str] = []
    for token in tokens:
        if token.lower().startswith(_TZ_PREFIX):
            tz = token[len(_TZ_PREFIX):]
        elif token.isdigit():
            count = int(token)
        else:
            geo_tokens.append(token)
    geo = " ".join(geo_tokens).strip() or None
    return tz, count, geo


def _new_schedule_id(store: ScheduleStore) -> str:
    taken = {record.id for record in store.load()}
    for _ in range(20):
        candidate = "s" + secrets.token_hex(2)
        if candidate not in taken:
            return candidate
    raise RuntimeError("could not allocate a schedule id")


async def schedule(update, context) -> None:
    if not await _authorised(update, context):
        return
    store: ScheduleStore = context.bot_data["schedules"]
    args = list(context.args)
    if not args:
        await update.effective_message.reply_text(
            "Usage: /schedule <YYYY-MM-DDTHH:MM> [geo] [count] [tz=Zone]")
        return
    when_text, rest = args[0], args[1:]
    try:
        when = datetime.fromisoformat(when_text)  # naive local time
        tz, count, geo = _split_schedule_args(rest)
        record = Schedule(
            id=_new_schedule_id(store), kind="once", expression=when.strftime("%Y-%m-%dT%H:%M"),
            geo=geo, job_count=count or 10, timezone=tz,
        )
        upcoming = next_run(record, datetime.now(timezone.utc))
        if upcoming is not None:
            record = dataclasses.replace(record, next_run_at=upcoming.isoformat())
        store.save(store.load() + [record])
    except (ValueError, ScheduleStoreError) as exc:
        await update.effective_message.reply_text(
            f"⚠️ Could not schedule that: {html.escape(str(exc))}")
        return
    when_utc = next_run(record, datetime.now(timezone.utc))
    await update.effective_message.reply_text(
        f"🗓 Scheduled <code>{record.id}</code>: once at "
        f"<code>{html.escape(record.expression)}</code> ({html.escape(record.timezone)})"
        f"{', geo ' + html.escape(record.geo) if record.geo else ''}, "
        f"count {record.job_count}."
        + (f" Next fire: {when_utc.isoformat()}" if when_utc else ""),
        parse_mode=ParseMode.HTML)


async def schedule_recurring(update, context) -> None:
    if not await _authorised(update, context):
        return
    store: ScheduleStore = context.bot_data["schedules"]
    args = list(context.args)
    if len(args) < 5:
        await update.effective_message.reply_text(
            "Usage: /schedule_recurring <min hr dom mon dow> [geo] [count] [tz=Zone]\n"
            "Example: /schedule_recurring 0 8 * * 1-5 Germany 10")
        return
    expression, rest = " ".join(args[:5]), args[5:]
    try:
        validate_cron(expression)  # raises ScheduleError with a usable message
        tz, count, geo = _split_schedule_args(rest)
        record = Schedule(
            id=_new_schedule_id(store), kind="recurring", expression=expression,
            geo=geo, job_count=count or 10, timezone=tz,
        )
        upcoming = next_run(record, datetime.now(timezone.utc))
        if upcoming is not None:
            record = dataclasses.replace(record, next_run_at=upcoming.isoformat())
        store.save(store.load() + [record])
    except (ValueError, ScheduleStoreError) as exc:
        await update.effective_message.reply_text(
            f"⚠️ Could not schedule that: {html.escape(str(exc))}")
        return
    upcoming = next_run(record, datetime.now(timezone.utc))
    await update.effective_message.reply_text(
        f"🗓 Scheduled <code>{record.id}</code>: <code>{html.escape(expression)}</code> "
        f"({html.escape(record.timezone)})"
        f"{', geo ' + html.escape(record.geo) if record.geo else ''}, "
        f"count {record.job_count}."
        + (f" Next fire: {upcoming.isoformat()}" if upcoming else ""),
        parse_mode=ParseMode.HTML)


async def list_schedules(update, context) -> None:
    if not await _authorised(update, context):
        return
    store: ScheduleStore = context.bot_data["schedules"]
    try:
        records = store.load()
    except ScheduleStoreError as exc:
        await update.effective_message.reply_text(
            f"⚠️ Scheduling is disabled: {html.escape(str(exc))}")
        return
    if not records:
        await update.effective_message.reply_text("📭 No schedules saved.")
        return
    lines = ["🗓 <b>Schedules</b>"]
    for record in records:
        state = "" if record.enabled else " (disabled)"
        lines.append(
            f"<code>{html.escape(record.id)}</code> {record.kind} "
            f"<code>{html.escape(record.expression)}</code> "
            f"tz={html.escape(record.timezone)} "
            f"geo={html.escape(record.geo or 'all')} count={record.job_count}{state}"
        )
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cancel_schedule(update, context) -> None:
    if not await _authorised(update, context):
        return
    store: ScheduleStore = context.bot_data["schedules"]
    if not context.args:
        await update.effective_message.reply_text("Usage: /cancel_schedule <id>")
        return
    target = context.args[0]
    try:
        records = store.load()
        remaining = [record for record in records if record.id != target]
        if len(remaining) == len(records):
            await update.effective_message.reply_text(
                f"No schedule with id <code>{html.escape(target)}</code>.",
                parse_mode=ParseMode.HTML)
            return
        store.save(remaining)
    except (ScheduleStoreError, ValueError) as exc:
        await update.effective_message.reply_text(
            f"⚠️ Could not cancel: {html.escape(str(exc))}")
        return
    await update.effective_message.reply_text(
        f"🗑 Cancelled schedule <code>{html.escape(target)}</code>.",
        parse_mode=ParseMode.HTML)


# -- scheduler loop -----------------------------------------------------------


async def scheduler_tick(context, now: datetime | None = None) -> None:
    """Launch every due schedule once; safe to call every minute.

    Due records are marked *before* launch, so a refusal (the overlap rule, for
    example) is surfaced to the owner without re-firing every minute.
    """
    now = now or datetime.now(timezone.utc)
    store: ScheduleStore = context.bot_data["schedules"]
    config: Stage3Config = context.bot_data["config"]
    bot = context.bot
    try:
        records = store.load()
    except ScheduleStoreError as exc:
        await bot.send_message(
            config.chat_id,
            "⚠️ Scheduling is disabled — the store and its backup are both "
            f"unreadable: {html.escape(str(exc))}")
        return
    ready = due(records, now)
    if not ready:
        return
    updated: list[Schedule] = []
    for record in records:
        if record in ready:
            # dataclasses.replace keeps the frozen record honest
            updated.append(_mark_started(record, now))
        else:
            updated.append(record)
    try:
        store.save(updated)
    except ScheduleStoreError as exc:
        await bot.send_message(config.chat_id,
                               f"⚠️ Could not persist schedule state: {html.escape(str(exc))}")
        return
    for record in ready:
        orchestrator: Orchestrator = context.bot_data["orchestrator"]
        try:
            orchestrator.start(RunRequest(geo=record.geo, job_count=record.job_count))
        except RunRefusedError as exc:
            await bot.send_message(
                config.chat_id,
                f"⏰ Scheduled run <code>{html.escape(record.id)}</code> could not "
                f"start: {html.escape(str(exc))}", parse_mode=ParseMode.HTML)
            continue
        await bot.send_message(
            config.chat_id,
            f"🤖 Scheduled run <code>{html.escape(record.id)}</code> launched "
            f"(geo {html.escape(record.geo or 'all')}, count {record.job_count}).",
            parse_mode=ParseMode.HTML)


def _mark_started(record: Schedule, now: datetime) -> Schedule:
    upcoming = None
    if record.kind == "recurring":
        candidate = next_run(record, now)
        upcoming = candidate.isoformat() if candidate else None
    return dataclasses.replace(
        record, last_started_at=now.isoformat(), next_run_at=upcoming)


class _TickContext:
    """The slice of a PTB context the scheduler loop actually needs."""

    def __init__(self, application: Application):
        self.bot = application.bot
        self.bot_data = application.bot_data


async def _scheduler_loop(application: Application) -> None:
    context = _TickContext(application)
    while True:
        try:
            await scheduler_tick(context)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the loop must outlive any single bad tick
            pass
        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)


async def _start_background_tasks(application: Application) -> None:
    """Start lifecycle-owned tasks once the polling loop is ready."""
    tasks = [asyncio.create_task(_scheduler_loop(application))]
    handle = application.bot_data.get("restored_handle")
    if handle is not None:
        tasks.append(asyncio.create_task(
            _watch_restored_handle(handle, application.bot_data)
        ))
    application.bot_data["background_tasks"] = tasks


async def _stop_background_tasks(application: Application) -> None:
    """Cancel and await lifecycle-owned tasks during PTB shutdown."""
    tasks = application.bot_data.pop("background_tasks", [])
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _watch_restored_handle(handle, bot_data: dict) -> None:
    """Remove a restored handle once its monitor reaches a terminal state."""
    await asyncio.to_thread(handle.wait)
    if bot_data.get("active_handle") is handle:
        bot_data.pop("active_handle", None)


def restore_active_run(bot_data: dict):
    """Restore a validated live handle before polling accepts commands."""
    orchestrator: Orchestrator = bot_data["orchestrator"]
    config: Stage3Config = bot_data["config"]
    handle = orchestrator.restore_active(config.run_state_root)
    if handle is not None:
        bot_data["active_handle"] = handle
    else:
        bot_data.pop("active_handle", None)
    return handle


# -- application assembly -----------------------------------------------------


def build_application(config: Stage3Config, orchestrator, schedules: ScheduleStore,
                      geos: list[str] | None = None, post_init=None) -> Application:
    """Assemble the PTB application with every handler and the bot_data wiring.

    `orchestrator=None` is the production entry point: `main()` wires the real
    orchestrator's notification hook in `post_init`, once the event loop exists.
    """
    if geos is None:
        geos = _load_geos(config.repo)
    builder = Application.builder().token(config.bot_token)
    if post_init is not None:
        builder = builder.post_init(post_init)
    application = builder.build()
    application.bot_data.update({
        "config": config,
        "orchestrator": orchestrator,
        "schedules": schedules,
        "geos": geos,
        "started_monotonic": time.monotonic(),
    })
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("health", health))
    application.add_handler(CommandHandler("ping", health))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("run", run))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("schedule", schedule))
    application.add_handler(CommandHandler("schedule_recurring", schedule_recurring))
    application.add_handler(CommandHandler("list_schedules", list_schedules))
    application.add_handler(CommandHandler("cancel_schedule", cancel_schedule))
    application.add_handler(CallbackQueryHandler(geo_callback, pattern=r"^geo:\d+$"))
    application.add_handler(CallbackQueryHandler(count_callback, pattern=r"^count:\d+$"))
    application.add_handler(CallbackQueryHandler(custom_callback, pattern=r"^custom$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,
                                           custom_count_text))
    return application


def _active_pipeline_log(application: Application) -> Path | None:
    handle = application.bot_data.get("active_handle")
    manifest = getattr(handle, "manifest_path", None)
    return Path(manifest).parent / "pipeline.log" if manifest is not None else None


def _delivery_completed(future: Future, *, chat_id: int, preview: str) -> None:
    """Observe a cross-thread Telegram future so delivery failures are visible."""
    try:
        message = future.result()
    except Exception as exc:
        LOGGER.error(
            "telegram api failure method=send_message chat_id=%s exception_type=%s preview=%s",
            chat_id, type(exc).__name__, preview, exc_info=True,
        )
        return
    LOGGER.info(
        "telegram api success method=send_message chat_id=%s message_id=%s",
        chat_id, getattr(message, "message_id", "unknown"),
    )


def _wire_notifications(application: Application, orchestrator: Orchestrator,
                        config: Stage3Config, loop) -> object:
    """Install the production monitor-thread to Telegram-loop callback."""
    def notify(text: str) -> Future:
        prepared = prepare_telegram_text(text)
        preview = safe_preview(prepared)
        LOGGER.info(
            "notification callback invoked callback=%s chat_id=%s preview=%s",
            callable_reference(notify), config.chat_id, preview,
        )
        LOGGER.info(
            "telegram api call method=send_message chat_id=%s preview=%s",
            config.chat_id, preview,
        )
        future = asyncio.run_coroutine_threadsafe(
            application.bot.send_message(config.chat_id, prepared), loop,
        )
        future.add_done_callback(
            lambda done: _delivery_completed(
                done, chat_id=config.chat_id, preview=preview,
            )
        )
        return future

    LOGGER.info(
        "installing notification callback callback=%s",
        callable_reference(notify),
    )
    orchestrator.set_notify(notify)
    return notify


def main() -> None:
    """Entry point: `python -m stage_3.bot`."""
    config = load_config()  # validates token isolation before anything polls
    schedules = ScheduleStore(config.schedule_path)
    orchestrator = Orchestrator(config)
    application_ref = {}
    configure_diagnostics(
        config.run_state_root,
        lambda: _active_pipeline_log(application_ref["application"])
        if "application" in application_ref else None,
    )

    async def _post_init(application: Application) -> None:
        LOGGER.info("PTB post_init started")
        try:
            identity = await application.bot.get_me()
        except Exception as exc:
            LOGGER.critical(
                "telegram startup validation failed method=get_me exception_type=%s",
                type(exc).__name__, exc_info=True,
            )
            raise RuntimeError(
                "Telegram startup validation failed; check STAGE3_BOT_TOKEN and network access"
            ) from exc
        LOGGER.info(
            "telegram startup validation succeeded method=get_me bot_id=%s username=%s",
            getattr(identity, "id", "unknown"),
            safe_preview(getattr(identity, "username", "unknown")),
        )
        loop = asyncio.get_running_loop()
        _wire_notifications(application, orchestrator, config, loop)
        application.bot_data["restored_handle"] = restore_active_run(
            application.bot_data
        )
        await _start_background_tasks(application)
        LOGGER.info("PTB post_init completed")

    application = build_application(config, orchestrator, schedules,
                                    post_init=_post_init)
    application.post_stop = _stop_background_tasks
    application_ref["application"] = application
    LOGGER.info("starting Telegram polling")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
