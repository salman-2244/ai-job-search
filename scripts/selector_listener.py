#!/usr/bin/env python3
"""Long-lived-but-self-terminating listener for the Telegram job selection.

Why this exists as a second entry point rather than a flag on
scripts/telegram_select.py:

    The 08:00 cron run must not block. It fetches, ranks and gates, then exits
    inside its normal window. But the selection it offers can be answered hours
    later — after lunch, that evening, next morning. Something has to be alive
    to receive the button press at that point, and it cannot be the cron process
    (which is gone) nor a permanent daemon (which would own the selector token's
    getUpdates forever and 409 any manual telegram_select.py run).

    So: run_daily.sh kickstarts this via launchd and exits. This process sends
    the list, holds getUpdates, and exits as soon as generation finishes or the
    window closes. Idle cost is one long-poll HTTP connection.

How it learns what to offer:
    `launchctl kickstart` cannot pass arguments, so Phase 3 leaves the rankset
    path and the date in a handoff marker (/tmp/jobsearch_pending_selection.json)
    and this process reads it. Without that marker it posts nothing and exits 0.

    That is a safety property, not a convenience. `KeepAlive` starts a launchd
    job as soon as it is bootstrapped, regardless of `RunAtLoad`, so merely
    installing the plist runs this. An earlier version guessed the day off the
    clock instead, and that install posted a stale 15-job list to the chat.

Restart safety (the reboot case):
    Telegram queues undelivered updates for 24 hours and redelivers them to the
    next getUpdates caller. If this process dies — crash, reboot, launchd
    restart — the picks made so far are already on disk in the selection state
    file, and the message ids with them. On startup we reload that state and
    reattach to the SAME messages instead of sending a fresh list, so a reboot
    mid-selection resumes rather than double-posting 25 jobs. A pending Submit
    tapped while we were down arrives on the first poll after we come back.

    Two states are terminal and must NOT be resumed: a selection already locked
    and generated (documents exist; regenerating would duplicate them and
    re-append tracker rows) and a window that already closed. Both are recorded
    in the state file so a KeepAlive restart cannot redo finished work.

Exit codes are launchd-shaped, not CLI-shaped:
    0        every deliberate outcome — generated, window closed, nothing to
             offer, misconfigured, or a previous run interrupted.
    nonzero  an unhandled failure, i.e. a crash.

    The plist restarts on nonzero (KeepAlive/SuccessfulExit=false), and launchd
    cannot distinguish "exited 3 on purpose" from "died". So anything this
    process decides on purpose exits 0, or a restart would repeat the same
    decision every ThrottleInterval forever. Deliberate outcomes are reported on
    stdout with a [listener] prefix and, where a human needs to act, over
    Telegram — read those, not $?.

Usage:
    python3 scripts/selector_listener.py --rankset /tmp/jobsearch_rankset_X.json
    python3 scripts/selector_listener.py --rankset ... --window 72000

Config is shared with telegram_select.py: ~/.jobsearch-selector.env.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent

# telegram_select.py is the single source of truth for rendering, gate verdicts,
# escaping, the drafter subprocess and the tracker write. Loaded by path because
# scripts/ is not a package and duplicating any of that logic would let the two
# entry points drift apart — the list approved in a --dry-run must be the list
# this sends.
_spec = importlib.util.spec_from_file_location(
    "telegram_select", REPO / "scripts" / "telegram_select.py"
)
ts = importlib.util.module_from_spec(_spec)
sys.modules["telegram_select"] = ts
_spec.loader.exec_module(ts)

# 20 hours by default: long enough to answer the next morning, short enough that
# the window is closed before the following 08:00 run wants the same token.
DEFAULT_WINDOW_SECONDS = 72000.0

# The hour com.salman.jobsearch.daily fires. Only used to decide which day a bare
# restart belongs to — see resolve_today.
RUN_HOUR = 8

# Written by run_daily.sh Phase 3, read by read_handoff(). Not under /tmp's
# jobsearch_selection_* glob on purpose: Phase 7 ages those out by date, and this
# file is keyed to the pending selection rather than to a day.
HANDOFF_FILE = Path("/tmp/jobsearch_pending_selection.json")

# How often to check that the poller is still alive while the window is open.
# 60s costs nothing next to a 20h window and bounds how long a dead poller can
# look healthy — see wait_for_selection for what goes wrong without it.
POLLER_CHECK_SECONDS = 60.0


def _stale_query_errors() -> tuple:
    """The error raised when a callback query is answered too late.

    Imported by name at module scope so handlers can catch it without importing
    inside the hot path, and tolerantly so this module still loads for tests in
    an environment without python-telegram-bot installed.
    """
    try:
        from telegram.error import BadRequest
    except ImportError:  # pragma: no cover - PTB is a hard runtime dep
        return ()
    return (BadRequest,)


STALE_QUERY_ERRORS = _stale_query_errors()


async def ack(query, text: str = "", **kwargs) -> None:
    """Acknowledge a tap without letting a stale query abort the handler.

    Telegram invalidates a callback query roughly 15 seconds after it is
    created, and answering a dead one raises BadRequest("Query is too old").
    That is a normal condition, not a failure: it means the tap sat in
    Telegram's queue while nothing was polling, which is exactly the case where
    the handler most needs to finish its work.

    Before this existed, `await query.answer(...)` sat directly in front of the
    lines that redraw the checkbox and the Submit counter. On 2026-08-23 three
    taps were drained after a stalled poll, all three answers raised, and the
    redraws never ran — the state file recorded three picks while the chat
    showed none of them, so the buttons looked broken while working perfectly.

    Only the stale-query error is swallowed; a bad call signature is a coding
    mistake and still propagates.
    """
    try:
        await query.answer(text, **kwargs)
    except STALE_QUERY_ERRORS:
        # The spinner on the device timed out long ago; the edits that follow
        # are what actually shows Salman his tap landed.
        print(
            "[listener] a tap was acked too late to answer; redrawing anyway",
            flush=True,
        )


async def wait_for_selection(
    app, done, window: float, interval: float = POLLER_CHECK_SECONDS
) -> str:
    """Wait for Submit, for the window to close, or for the poller to die.

    Returns 'submitted', 'window' or 'poller-died'.

    That third outcome is a real incident, not a theoretical one.
    `Updater.start_polling` runs the poll in a BACKGROUND task, so when that
    task dies — a network transition on 2026-08-23 left its socket in
    CLOSE_WAIT — the failure is invisible from here. The original code awaited
    `done` in a single `wait_for(timeout=window)`, so the process went on
    sleeping out the remaining hours of its 20h window while consuming nothing.
    Every symptom pointed the wrong way: the process was alive, launchd
    reported `state = running` with `last exit code = 0`, and taps vanished
    silently. What proved it was a hand `getUpdates` returning HTTP 200 instead
    of the 409 Conflict an active poller forces.

    So the wait is chunked and re-checks `updater.running`. A dead poller
    becomes a nonzero exit, the one signal KeepAlive acts on, and the restart
    resumes rather than re-posting because the message ids are already on disk.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + window
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return "window"
        try:
            await asyncio.wait_for(done.wait(), timeout=min(interval, remaining))
            return "submitted"
        except asyncio.TimeoutError:
            if not app.updater.running:
                return "poller-died"


def phase_of(state) -> str:
    """What a restarted process should do with an existing state file.

    'fresh'       no state, or nothing decided yet — send the list.
    'resume'      messages sent, selection not yet submitted — reattach.
    'interrupted' died between Submit and the summary — report, do not retry.
    'finished'    terminal; generation ran or the window closed — exit.
    """
    if state.completed:
        return "finished"
    if state.locked:
        # Locked but not completed means we died after Submit. Generation writes
        # documents and tracker rows, so it is not idempotent and is reported
        # rather than silently retried.
        return "interrupted"
    if state.job_message_ids:
        return "resume"
    return "fresh"


async def run(rows: list, cfg: dict, args: argparse.Namespace) -> int:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.constants import ParseMode
    from telegram.error import RetryAfter
    from telegram.ext import Application, CallbackQueryHandler, ContextTypes

    token = cfg.get("SELECTOR_BOT_TOKEN")
    chat_id = cfg.get("SELECTOR_CHAT_ID")
    # Returned, not raised: a missing token is a permanent condition, and under
    # KeepAlive a nonzero exit would retry it every 10s until the window passed.
    if not token or not chat_id:
        print(
            "[listener] SELECTOR_BOT_TOKEN and SELECTOR_CHAT_ID are both required; "
            "check ~/.jobsearch-selector.env",
            file=sys.stderr,
            flush=True,
        )
        return 0
    allowed = {
        int(x)
        for x in re.split(r"[,\s]+", cfg.get("SELECTOR_ALLOWED_USER_IDS", ""))
        if x
    }
    if not allowed:
        print(
            "[listener] SELECTOR_ALLOWED_USER_IDS must list at least one user id",
            file=sys.stderr,
            flush=True,
        )
        return 0

    today = args.today
    state = ts.SelectionState(Path(f"/tmp/jobsearch_selection_{today}.json"))
    state.load()
    gen_log = Path(f"/tmp/jobsearch_select_generate_{today}.log")
    done = asyncio.Event()

    phase = phase_of(state)
    print(f"[listener] state phase: {phase}", flush=True)
    if phase == "finished":
        print("[listener] this selection already completed; nothing to do", flush=True)
        return 0
    if phase == "interrupted":
        print(
            "[listener] a previous run died between Submit and the summary. "
            "Generation is not idempotent, so it is not retried automatically. "
            f"Inspect {gen_log} and cv/ before re-running.",
            flush=True,
        )
        # 0, not nonzero: under KeepAlive a nonzero exit would relaunch straight
        # back into this same branch every ThrottleInterval. The condition needs
        # a human, so it is announced rather than retried.
        return 0

    def job_markup(idx: int) -> "InlineKeyboardMarkup":
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        ts.toggle_label(idx in state.selected),
                        callback_data=f"{ts.CB_TOGGLE}:{idx}",
                    )
                ]
            ]
        )

    def control_markup() -> "InlineKeyboardMarkup":
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Select all", callback_data=ts.CB_ALL),
                    InlineKeyboardButton("Clear", callback_data=ts.CB_NONE),
                ],
                [
                    InlineKeyboardButton(
                        f"✅ Submit ({len(state.selected)})",
                        callback_data=ts.CB_SUBMIT,
                    )
                ],
            ]
        )

    async def guard(query) -> bool:
        """Only the allowlisted user, tapping a button from THIS run.

        The identity half is not optional here. `callback_data` is "t:<idx>", an
        index into whichever run is polling, and old messages keep live keyboards
        indefinitely — so without this a tap on yesterday's job #3 toggles
        today's job #3 and Submit drafts for a posting never displayed. The check
        lived only in telegram_select.py until 2026-08-23; launchd runs THIS
        file, so production was the unprotected copy.
        """
        if not (query.from_user and query.from_user.id in allowed):
            await ack(query, "Not authorised.", show_alert=True)
            return False
        mid = query.message.message_id if query.message else None
        if not ts.is_current_message(mid, state):
            await ack(
                query,
                "This list is from an earlier run. Scroll down to the current one.",
                show_alert=True,
            )
            return False
        return True

    async def refresh_control(bot) -> None:
        if state.control_message_id is None:
            return
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=state.control_message_id,
                text=ts.render_control(
                    len(rows), len(state.selected), locked=state.locked
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=None if state.locked else control_markup(),
            )
        except Exception:
            pass  # an identical-text edit is not worth failing the flow

    async def on_toggle(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        query = update.callback_query
        if not await guard(query):
            return
        if state.locked:
            await ack(query, "Already submitted.")
            return
        idx = int(query.data.split(":", 1)[1])
        now = state.toggle(idx)
        # State first, then a best-effort ack, then the redraw. The redraw is
        # what Salman actually sees, so it must never sit behind the ack: when
        # three queued taps were drained on 2026-08-23 their answers all raised
        # and these two lines never ran, which is why the buttons looked dead.
        await ack(query, "Selected" if now else "Removed")
        await query.edit_message_reply_markup(reply_markup=job_markup(idx))
        await refresh_control(context.bot)

    async def on_bulk(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        query = update.callback_query
        if not await guard(query):
            return
        if state.locked:
            await ack(query, "Already submitted.")
            return
        if query.data == ts.CB_ALL:
            state.select_all(r.idx for r in rows)
        else:
            state.clear()
        await ack(query)
        for row in rows:
            mid = state.job_message_ids.get(row.idx)
            if mid is None:
                continue
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=chat_id, message_id=mid, reply_markup=job_markup(row.idx)
                )
            except Exception:
                pass
        await refresh_control(context.bot)

    async def on_submit(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        query = update.callback_query
        if not await guard(query):
            return
        if state.locked:
            await ack(query, "Already submitted.")
            return
        chosen = [rows[i] for i in state.ordered() if 0 <= i < len(rows)]
        if not chosen:
            await ack(query, "Nothing selected.", show_alert=True)
            return

        # Acked before locking, and through the best-effort helper. A raise
        # between `locked = True` and the generation below would leave the state
        # locked-but-incomplete, which phase_of reads as 'interrupted' and
        # refuses to retry — one stale tap would have bricked the day's run.
        await ack(query, "Submitted")
        state.locked = True
        state.save()

        for row in rows:  # freeze the set so it cannot drift mid-generation
            mid = state.job_message_ids.get(row.idx)
            if mid is None:
                continue
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=chat_id, message_id=mid, reply_markup=None
                )
            except Exception:
                pass
        await refresh_control(context.bot)

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Generating {len(chosen)} application(s)… this takes a few minutes.",
            parse_mode=ParseMode.HTML,
        )

        sem = asyncio.Semaphore(ts.MAX_PARALLEL_JOBS)
        outcomes = await asyncio.gather(
            *(ts.generate_one(r, today, sem, gen_log) for r in chosen)
        )
        ts.append_tracker(
            [(o["row"], o.get("result") or {}) for o in outcomes if o["ok"]],
            today,
            args.tracker,
        )
        # Marked complete BEFORE the summary send: if Telegram is unreachable at
        # that moment, a KeepAlive restart must not regenerate the documents.
        state.completed = True
        state.save()
        await context.bot.send_message(
            chat_id=chat_id,
            text=ts.render_summary(list(outcomes)),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        done.set()

    async def on_error(update: object, context: "ContextTypes.DEFAULT_TYPE") -> None:
        """Put handler exceptions in the log instead of PTB's default logger.

        PTB catches anything a handler raises and hands it here; with no handler
        registered it goes to a logging call that this process never configures,
        so it vanishes. That is how the 2026-08-23 BadRequests stayed invisible
        while the buttons appeared to do nothing — the tracebacks existed but had
        nowhere to go.
        """
        print(
            "[listener] handler error: "
            + "".join(
                traceback.format_exception(
                    type(context.error), context.error, context.error.__traceback__
                )
            ).rstrip(),
            flush=True,
        )

    app = Application.builder().token(token).build()
    app.add_handler(CallbackQueryHandler(on_toggle, pattern=rf"^{ts.CB_TOGGLE}:\d+$"))
    app.add_handler(
        CallbackQueryHandler(on_bulk, pattern=rf"^({ts.CB_ALL}|{ts.CB_NONE})$")
    )
    app.add_handler(CallbackQueryHandler(on_submit, pattern=rf"^{ts.CB_SUBMIT}$"))
    app.add_error_handler(on_error)

    async with app:
        await app.start()
        # stop() must mirror start() before __aexit__ calls shutdown();
        # updater.stop() alone does not satisfy PTB. Both in one finally so an
        # early return or a failed send still tears down in the right order.
        try:
            # Send whatever is not already in the chat. On a fresh run that is
            # every row; on a resume that died mid-send it is the tail, and on a
            # resume that got them all it is nothing. Driving off the state file
            # rather than a phase flag means a crash at any point in the loop
            # continues from where it stopped instead of either re-posting the
            # jobs already sent or hiding the ones it never reached.
            missing = [r for r in rows if r.idx not in state.job_message_ids]
            if phase != "fresh":
                print(
                    f"[listener] resuming {len(state.job_message_ids)} existing "
                    f"message(s), {len(state.selected)} already selected, "
                    f"{len(missing)} still to send",
                    flush=True,
                )
            if phase == "fresh":
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"<b>Job selection — {today}</b>\n"
                        f"{len(rows)} ranked job(s) follow."
                    ),
                    parse_mode=ParseMode.HTML,
                )
            for row in missing:
                for attempt in range(3):
                    try:
                        msg = await app.bot.send_message(
                            chat_id=chat_id,
                            text=ts.render_job(row),
                            parse_mode=ParseMode.HTML,
                            reply_markup=job_markup(row.idx),
                            disable_web_page_preview=True,
                        )
                        state.job_message_ids[row.idx] = msg.message_id
                        # Saved per message, not once at the end: a crash
                        # halfway through must not orphan the messages it
                        # has already posted.
                        state.save()
                        break
                    except RetryAfter as exc:
                        await asyncio.sleep(float(exc.retry_after) + 0.5)
                        if attempt == 2:
                            raise
                await asyncio.sleep(ts.SEND_DELAY_SECONDS)

            if state.control_message_id is None:
                # Sent last so it counts a complete list, and guarded by its own
                # id rather than by `phase`: a run that died after the final job
                # message but before this one has no Submit button at all, and
                # must post one instead of resuming into an unusable chat.
                control = await app.bot.send_message(
                    chat_id=chat_id,
                    text=ts.render_control(len(rows), len(state.selected)),
                    parse_mode=ParseMode.HTML,
                    reply_markup=control_markup(),
                )
                state.control_message_id = control.message_id
                state.save()
            else:
                # Refreshed so its count reflects picks made before the restart.
                await refresh_control(app.bot)
                # And repaint the checkboxes to match. Without this the chat
                # contradicts itself after a resume: the counter reads
                # "Submit (3)" while all 25 boxes still render unchecked,
                # because the redraws for those taps are exactly what the
                # 2026-08-23 stale-query raises destroyed. Worse than cosmetic —
                # tapping a job he had already picked would deselect it, so the
                # next Submit would draft the wrong set.
                for idx in sorted(state.selected):
                    mid = state.job_message_ids.get(idx)
                    if mid is None:
                        continue  # a partial send has gaps; editing one raises
                    try:
                        await app.bot.edit_message_reply_markup(
                            chat_id=chat_id,
                            message_id=mid,
                            reply_markup=job_markup(idx),
                        )
                    except Exception:
                        pass  # already correct, or the message is gone
                    await asyncio.sleep(ts.SEND_DELAY_SECONDS)

            # start_polling drains updates Telegram queued while we were down,
            # which is what makes a Submit tapped during a reboot survive.
            await app.updater.start_polling(allowed_updates=["callback_query"])
            print(
                f"[listener] holding getUpdates for up to {args.window:.0f}s "
                f"({len(rows)} job(s) offered)",
                flush=True,
            )
            outcome = await wait_for_selection(app, done, args.window)
            if outcome == "poller-died":
                print(
                    "[listener] the polling task died; exiting nonzero so "
                    "KeepAlive relaunches into 'resume'",
                    flush=True,
                )
                # The ONLY deliberate nonzero exit. Every other decided outcome
                # returns 0 because KeepAlive cannot tell a chosen nonzero from
                # a crash and would relaunch into the same decision forever.
                # Here relaunching is exactly right: the message ids are already
                # on disk, so the restart reattaches instead of re-posting, and
                # Salman sees nothing but working buttons. Deliberately NOT
                # marking the state completed — the selection is still open.
                return 1
            if outcome == "window":
                print(f"[listener] window closed after {args.window}s", flush=True)
                # Terminal, so a KeepAlive restart does not re-offer a stale list.
                state.completed = True
                state.save()
                try:
                    await app.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            "⏳ Selection window closed with no Submit. "
                            "Tomorrow's run will send a fresh list."
                        ),
                    )
                except Exception:
                    pass
                # 0: a closed window is the expected outcome of an unanswered
                # day, not a failure, and must not trigger a KeepAlive restart.
                return 0
        finally:
            if app.updater.running:
                await app.updater.stop()
            if app.running:
                await app.stop()
    return 0


def resolve_today(explicit: Optional[str], now: Optional[datetime] = None) -> str:
    """Which day's rankset and state file this process belongs to.

    Only consulted for a hand-run that passed --rankset without --today. A
    launchd start reads the day out of the handoff marker instead, because
    `launchctl kickstart` cannot pass arguments and a guessed date is what let a
    bare start re-offer a previous day's list.

    Not simply date.today(): the window is 20h from an 08:00 start, so it runs
    past midnight, and a restart at 01:00 asking date.today() would look for a
    rankset and a state file named for the new day and find neither. So before
    the 08:00 run of a given day exists, "today" is still yesterday. The cutoff
    is the schedule's own hour.
    """
    if explicit:
        return explicit
    now = now or datetime.now()
    stamp = now.date()
    if now.hour < RUN_HOUR:
        stamp -= timedelta(days=1)
    return stamp.isoformat()


def read_handoff(path: Path = HANDOFF_FILE) -> Optional[dict]:
    """The rankset and day Phase 3 asked for, or None if it never asked.

    This file is the whole authorisation to post to Telegram. Without it the
    listener does nothing, which is what makes the launchd job safe to load:
    `KeepAlive` starts a job the moment it is bootstrapped regardless of
    `RunAtLoad`, so installing the plist runs this process immediately. Before
    the marker existed that start guessed a date off the clock, found an old
    rankset, and posted a stale 15-job list to the chat.

    Deliberately NOT deleted once read. A KeepAlive restart mid-selection has to
    find it again to resume; what stops a finished selection being re-offered is
    the `completed` flag in the state file, not the absence of this one.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        print(f"[listener] handoff at {path} is unreadable ({exc})", flush=True)
        return None
    today = data.get("today")
    rankset = data.get("rankset")
    if not today or not rankset:
        print(f"[listener] handoff at {path} names no rankset; ignoring", flush=True)
        return None
    return {"today": str(today), "rankset": Path(str(rankset))}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--rankset",
        type=Path,
        default=None,
        help="rankset JSON to offer; defaults to /tmp/jobsearch_rankset_<today>.json",
    )
    ap.add_argument("--today", default=None, help="YYYY-MM-DD; names state files")
    ap.add_argument("--limit", type=int, default=25, help="max jobs to offer")
    ap.add_argument("--env", type=Path, default=None, help="override the config path")
    ap.add_argument(
        "--tracker",
        type=Path,
        default=None,
        help="write tracker rows here instead of job_search_tracker.csv",
    )
    ap.add_argument(
        "--window",
        type=float,
        default=float(os.environ.get("SELECTOR_WINDOW", DEFAULT_WINDOW_SECONDS)),
        help="seconds to stay alive waiting for Submit (default 20h)",
    )
    args = ap.parse_args(argv)

    # A hand-run says which rankset it wants; a launchd start cannot, because
    # `launchctl kickstart` takes no arguments. So the marker Phase 3 wrote is
    # the only thing that authorises posting a list, and its absence is a
    # perfectly normal state — the job sitting loaded with no selection pending.
    if args.rankset is None:
        handoff = read_handoff()
        if handoff is None:
            print(
                "[listener] no selection pending "
                f"({HANDOFF_FILE} absent); nothing to do",
                flush=True,
            )
            return 0
        args.rankset = handoff["rankset"]
        args.today = args.today or handoff["today"]

    args.today = resolve_today(args.today)
    today = args.today
    rankset = args.rankset or Path(f"/tmp/jobsearch_rankset_{today}.json")
    if not rankset.exists():
        # Not worth a nonzero exit under launchd: a day whose ranking failed has
        # nothing to offer, and a crash-looping listener is worse than a no-op.
        print(f"[listener] no rankset at {rankset}; nothing to offer", flush=True)
        return 0

    rows = ts.load_rankset(rankset)[: args.limit]
    if not rows:
        print(f"[listener] {rankset} contained no jobs", flush=True)
        return 0

    cfg = ts.load_env(args.env)
    return asyncio.run(run(rows, cfg, args))


if __name__ == "__main__":
    sys.exit(main())
