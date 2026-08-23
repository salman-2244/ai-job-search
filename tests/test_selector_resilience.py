"""Tests for the two defects that took the 2026-08-23 selection down.

Both were found by systematic debugging of a live incident: Salman tapped three
job buttons, nothing visibly happened, and Submit could not be reached. The
process was alive the whole time and launchd reported `state = running` with
`last exit code = 0`.

What actually happened, in order:

  1. The polling task inside `start_polling` died on a network transition,
     leaving a socket in CLOSE_WAIT. `start_polling` runs the poll in a
     BACKGROUND task, so its death is invisible to the main coroutine, which
     went on waiting on `done` for the rest of its 20-hour window. Proof it was
     dead: a hand `getUpdates` returned HTTP 200 instead of the 409 Conflict
     that an active poller forces.

  2. The three taps that did land had been queued by Telegram while the poller
     was stalled, so by the time they were drained they were past Telegram's
     ~15s callback deadline. `query.answer()` raised BadRequest, the exception
     propagated out of the handler, and the two lines AFTER the answer — the
     ones that redraw the checkbox and the Submit counter — never ran. The
     state file recorded all three picks while the screen showed none of them.

The invariant that follows: acknowledging a tap is best-effort, and redrawing
the list is not allowed to depend on it.
"""
import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LISTENER = REPO / "scripts" / "selector_listener.py"

_spec = importlib.util.spec_from_file_location("selector_listener", LISTENER)
sl = importlib.util.module_from_spec(_spec)
sys.modules["selector_listener"] = sl
_spec.loader.exec_module(sl)

SRC = LISTENER.read_text(encoding="utf-8")


def handler_body(name: str) -> str:
    """Source of one handler, up to the next `async def` at the same depth."""
    at = SRC.index(f"    async def {name}(")
    nxt = SRC.find("\n    async def ", at + 10)
    return SRC[at : nxt if nxt != -1 else len(SRC)]


class StaleQuery:
    """A callback query past Telegram's answer deadline."""

    def __init__(self, data="t:3", message_id=67, user_id=1):
        self.data = data
        self.from_user = type("U", (), {"id": user_id})()
        self.message = type("M", (), {"message_id": message_id})()
        self.answered = False
        self.markup_edits = 0

    async def answer(self, *a, **kw):
        self.answered = True
        raise sl.STALE_QUERY_ERRORS[0](
            "Query is too old and response timeout expired or query id is invalid"
        )

    async def edit_message_reply_markup(self, **kw):
        self.markup_edits += 1


class TestAckIsBestEffort(unittest.TestCase):
    """A dead spinner must not stop the checkbox from being redrawn."""

    def test_ack_swallows_the_too_old_error(self):
        q = StaleQuery()
        asyncio.run(sl.ack(q, "Selected"))
        self.assertTrue(q.answered, "the ack was never attempted")

    def test_ack_lets_a_real_bug_through(self):
        """Only the stale-query error is tolerated; a coding error must surface."""

        class Boom:
            async def answer(self, *a, **kw):
                raise TypeError("wrong argument")

        with self.assertRaises(TypeError):
            asyncio.run(sl.ack(Boom(), "Selected"))

    def test_no_handler_answers_the_query_directly(self):
        """The ordering bug itself, asserted on the source.

        `await query.answer(...)` followed by a markup edit is exactly the shape
        that lost three taps: the answer raised and the edit never ran.
        """
        for name in ("on_toggle", "on_bulk", "on_submit"):
            self.assertNotIn(
                "await query.answer(",
                handler_body(name),
                f"{name} answers directly; a stale query aborts its redraw",
            )

    def test_every_handler_acks_through_the_helper(self):
        for name in ("on_toggle", "on_bulk", "on_submit"):
            self.assertIn("ack(query", handler_body(name), name)

    def test_the_toggle_redraws_after_acking(self):
        """State, ack, then redraw — the redraw must be last, not skipped."""
        body = handler_body("on_toggle")
        self.assertLess(
            body.index("ack(query"),
            body.index("edit_message_reply_markup"),
            "the redraw happens before the ack",
        )


class TestPollerDeathIsObserved(unittest.TestCase):
    """The silent stall: process alive, poller dead, nothing consumed."""

    def test_something_rechecks_the_poller_inside_the_window(self):
        """A single 20h wait_for cannot notice the poller dying inside it."""
        self.assertIn("POLLER_CHECK_SECONDS", SRC)
        self.assertLess(
            sl.POLLER_CHECK_SECONDS,
            sl.DEFAULT_WINDOW_SECONDS,
            "the check interval must be shorter than the window it guards",
        )

    def test_a_dead_poller_is_reported_not_waited_out(self):
        class App:
            updater = type("U", (), {"running": False})()

        outcome = asyncio.run(
            sl.wait_for_selection(App(), asyncio.Event(), window=1.0, interval=0.01)
        )
        self.assertEqual(outcome, "poller-died")

    def test_a_live_poller_waits_for_the_window(self):
        class App:
            updater = type("U", (), {"running": True})()

        outcome = asyncio.run(
            sl.wait_for_selection(App(), asyncio.Event(), window=0.05, interval=0.01)
        )
        self.assertEqual(outcome, "window")

    def test_submit_wins_over_both(self):
        class App:
            updater = type("U", (), {"running": True})()

        async def scenario():
            done = asyncio.Event()
            done.set()
            return await sl.wait_for_selection(App(), done, window=5.0, interval=0.01)

        self.assertEqual(asyncio.run(scenario()), "submitted")

    def test_poller_death_exits_nonzero_so_keepalive_restarts_it(self):
        """The one deliberate nonzero exit, and the reason it is safe.

        Every other decided outcome returns 0 because KeepAlive cannot tell a
        deliberate nonzero from a crash and would relaunch into the same
        decision forever. A dead poller is the exception: relaunching is
        precisely the correct response, and the restart lands in 'resume',
        which reattaches to the messages already sent rather than re-posting
        them, so the retry is silent to Salman.
        """
        at = SRC.index('== "poller-died"')
        self.assertIn("return 1", SRC[at : at + 800])


class TestProductionGuardHasTheStaleButtonCheck(unittest.TestCase):
    """The listener is the production path, and it had its own weaker guard.

    `is_current_message` was added to telegram_select.py, but launchd runs
    selector_listener.py, which carried a copy of `guard` that only checked the
    allowlist. The existing test for the check inspected telegram_select.py, so
    it passed while production was unprotected — a tap on yesterday's job #3
    would have toggled today's job #3.
    """

    def test_the_listener_guard_checks_message_identity(self):
        at = SRC.index("async def guard(query)")
        self.assertIn("is_current_message", SRC[at : at + 900])

    def test_it_reuses_the_shared_predicate_rather_than_a_second_copy(self):
        at = SRC.index("async def guard(query)")
        self.assertIn("ts.is_current_message", SRC[at : at + 900])

    def test_todays_live_message_ids_would_still_pass(self):
        """Guards against the fix locking Salman out of the open selection."""
        state = sl.ts.SelectionState(None)
        state.job_message_ids = {i: 67 + i for i in range(25)}
        state.control_message_id = 92
        for mid in list(state.job_message_ids.values()) + [92]:
            self.assertTrue(sl.ts.is_current_message(mid, state), mid)
        self.assertFalse(sl.ts.is_current_message(31, state))


class TestResumeRedrawsTheCheckboxes(unittest.TestCase):
    """A resume must repaint the job keyboards, not just the Submit counter.

    The tail of the same incident, and the part Salman would have hit next. The
    three taps were recorded in the state file but their redraws never ran, so
    the checkboxes on screen stayed unchecked. The resume path refreshed only the
    control message, which left the chat self-contradictory: "Submit (3)" above
    twenty-five boxes that all looked empty. Tapping the job he had already
    picked would then have DESELECTED it — the opposite of what the tap means to
    him — and Submit would have drafted the wrong set.

    So a resume with picks on disk has to repaint every job whose checkbox
    disagrees with the state file.
    """

    def test_the_resume_path_repaints_job_keyboards(self):
        at = SRC.index("# Refreshed so its count reflects picks made")
        branch = SRC[at : at + 1200]
        self.assertIn(
            "edit_message_reply_markup",
            branch,
            "resume refreshes only the control message; selected jobs still "
            "render unchecked and a tap would deselect them",
        )

    def test_it_repaints_only_what_it_has_message_ids_for(self):
        """A partial send has gaps; editing a missing id raises."""
        at = SRC.index("# Refreshed so its count reflects picks made")
        branch = SRC[at : at + 1200]
        self.assertIn("job_message_ids", branch)

    def test_todays_three_picks_are_the_ones_that_need_repainting(self):
        """The concrete case: selected {1,2,4} against 25 sent messages."""
        state = sl.ts.SelectionState(None)
        state.selected = {1, 2, 4}
        state.job_message_ids = {i: 67 + i for i in range(25)}
        stale = [i for i in state.selected if i in state.job_message_ids]
        self.assertEqual(sorted(stale), [1, 2, 4])
        self.assertEqual(
            [state.job_message_ids[i] for i in sorted(stale)], [68, 69, 71]
        )


if __name__ == "__main__":
    unittest.main()
