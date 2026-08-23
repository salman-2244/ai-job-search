"""Tests for the decoupled selection listener's restart decisions.

The listener is what makes generation independent of the 08:00 cron run: it can
be killed, rebooted through, or restarted by launchd's KeepAlive between the
moment the list is sent and the moment Submit is pressed. Every one of those
restarts re-reads one state file and decides from it alone what to do.

That decision is the whole risk surface, and it is asymmetric:

  * Re-sending a list that was already sent double-posts up to 25 messages.
  * Re-running generation that already ran writes a second set of documents and
    a second pair of tracker rows.

So `phase_of` is tested exhaustively rather than by example, and the state file
is checked to round-trip the `completed` flag that distinguishes "Submit was
pressed" from "the work finished".
"""
import fnmatch
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LISTENER = REPO / "scripts" / "selector_listener.py"

_spec = importlib.util.spec_from_file_location("selector_listener", LISTENER)
sl = importlib.util.module_from_spec(_spec)
sys.modules["selector_listener"] = sl
_spec.loader.exec_module(sl)

ts = sl.ts  # the listener re-exports telegram_select, loaded by path


class PhaseBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def state(self, **kw):
        """A state object as if reloaded from disk after a restart."""
        s = ts.SelectionState(self.tmp / "s.json")
        s.selected = set(kw.get("selected", []))
        s.job_message_ids = dict(kw.get("job_message_ids", {}))
        s.control_message_id = kw.get("control_message_id")
        s.locked = kw.get("locked", False)
        s.completed = kw.get("completed", False)
        return s


class TestPhaseOf(PhaseBase):
    def test_no_state_is_fresh(self):
        """First run of the day: nothing sent, nothing decided."""
        self.assertEqual(sl.phase_of(self.state()), "fresh")

    def test_messages_sent_but_not_submitted_resumes(self):
        """The reboot-mid-selection case: reattach, never re-post the list."""
        s = self.state(job_message_ids={0: 31, 1: 32}, control_message_id=33)
        self.assertEqual(sl.phase_of(s), "resume")

    def test_picks_made_before_the_crash_still_resume(self):
        s = self.state(selected=[1, 6], job_message_ids={0: 31}, control_message_id=33)
        self.assertEqual(sl.phase_of(s), "resume")

    def test_locked_without_completed_is_interrupted(self):
        """Died mid-generation. Documents may be half-written, so do not retry."""
        s = self.state(selected=[1], job_message_ids={0: 31}, locked=True)
        self.assertEqual(sl.phase_of(s), "interrupted")

    def test_completed_is_finished(self):
        s = self.state(
            selected=[1], job_message_ids={0: 31}, locked=True, completed=True
        )
        self.assertEqual(sl.phase_of(s), "finished")

    def test_completed_wins_over_every_other_signal(self):
        """A closed window sets completed without locked; both must terminate."""
        self.assertEqual(sl.phase_of(self.state(completed=True)), "finished")
        self.assertEqual(
            sl.phase_of(
                self.state(completed=True, locked=False, job_message_ids={0: 1})
            ),
            "finished",
        )

    def test_only_an_empty_message_map_may_resend(self):
        """Guards the double-post case directly."""
        self.assertEqual(sl.phase_of(self.state()), "fresh")
        self.assertEqual(sl.phase_of(self.state(selected=[3])), "fresh")
        for extra in (
            {"job_message_ids": {0: 1}},
            {"job_message_ids": {0: 1}, "selected": [0]},
        ):
            self.assertNotEqual(sl.phase_of(self.state(**extra)), "fresh", extra)


class TestCompletedRoundTrips(PhaseBase):
    """`completed` is the flag the whole restart story rests on."""

    def test_completed_survives_a_save_load_cycle(self):
        path = self.tmp / "s.json"
        s = ts.SelectionState(path)
        s.locked = True
        s.completed = True
        s.save()

        reloaded = ts.SelectionState(path)
        reloaded.load()
        self.assertIs(reloaded.completed, True)
        self.assertEqual(sl.phase_of(reloaded), "finished")

    def test_a_pre_listener_state_file_reads_as_not_completed(self):
        """Files written before the flag existed must not look finished."""
        path = self.tmp / "old.json"
        path.write_text(
            json.dumps(
                {
                    "selected": [1, 6],
                    "job_message_ids": {"0": 31},
                    "control_message_id": 46,
                    "locked": True,
                }
            ),
            encoding="utf-8",
        )
        s = ts.SelectionState(path)
        s.load()
        self.assertIs(s.completed, False)
        self.assertEqual(sl.phase_of(s), "interrupted")

    def test_completed_is_written_to_disk_by_name(self):
        path = self.tmp / "s.json"
        s = ts.SelectionState(path)
        s.completed = True
        s.save()
        self.assertIs(json.loads(path.read_text(encoding="utf-8"))["completed"], True)

    def test_fresh_state_is_not_completed(self):
        s = ts.SelectionState(self.tmp / "s.json")
        s.toggle(2)
        self.assertIs(
            json.loads((self.tmp / "s.json").read_text(encoding="utf-8"))["completed"],
            False,
        )


class TestListenerDefaults(unittest.TestCase):
    def test_window_outlives_a_workday_but_dies_before_the_next_run(self):
        """The window must not still hold getUpdates when tomorrow's run starts."""
        self.assertGreater(sl.DEFAULT_WINDOW_SECONDS, 12 * 3600)
        self.assertLess(sl.DEFAULT_WINDOW_SECONDS, 24 * 3600)

    def test_a_missing_rankset_exits_zero(self):
        """launchd must not crash-loop on a day whose ranking produced nothing."""
        self.assertEqual(
            sl.main(["--rankset", "/tmp/definitely-not-a-rankset-9f3a.json"]), 0
        )

    def test_reuses_telegram_select_rather_than_reimplementing_it(self):
        """Two renderers would let the approved list drift from the sent list."""
        for name in (
            "render_job",
            "render_control",
            "toggle_label",
            "load_rankset",
            "generate_one",
            "append_tracker",
            "render_summary",
            "SelectionState",
        ):
            self.assertTrue(hasattr(sl.ts, name), name)


class TestNoDeliberateNonzeroExit(unittest.TestCase):
    """The plist sets KeepAlive/SuccessfulExit=false.

    launchd cannot distinguish "exited 3 on purpose" from "died", so a nonzero
    exit the listener chooses gets relaunched into the same choice every
    ThrottleInterval — forever. Deliberate outcomes must therefore exit 0.

    There is exactly one justified exception, and the test enumerates it rather
    than relaxing the rule: a dead polling task. Relaunching is the correct
    response there — `start_polling` runs the poll in a background task whose
    death the main coroutine cannot see, so on 2026-08-23 the process sat out
    the rest of its 20h window consuming nothing while launchd reported
    `state = running` and `last exit code = 0`. Exiting nonzero converts that
    into the one signal KeepAlive acts on, and the relaunch lands in 'resume',
    which reattaches to the already-sent messages instead of re-posting them.
    That is why it terminates: the restart does different work.

    This is a property of the source, so it is asserted against the source.
    """

    SRC = LISTENER.read_text(encoding="utf-8")

    def test_the_only_nonzero_exit_is_the_dead_poller(self):
        for m in re.finditer(r"^\s*return\s+([1-9]\d*)\s*$", self.SRC, re.M):
            context = self.SRC[max(0, m.start() - 900) : m.start()]
            self.assertIn(
                '== "poller-died"',
                context,
                f"deliberate nonzero exit {m.group(1)!r} outside the poller-death "
                f"branch; KeepAlive would relaunch into it forever",
            )

    def test_a_dead_poller_does_not_mark_the_day_finished(self):
        """The restart has to find the selection still open, or it gives up.

        `completed = True` reads as phase 'finished', which exits without
        offering anything — so setting it here would turn a recoverable stall
        into a silently lost day.
        """
        at = self.SRC.index('== "poller-died"')
        branch = self.SRC[at : self.SRC.index("return 1", at)]
        self.assertNotIn("state.completed = True", branch)

    def test_no_sys_exit_or_systemexit_with_a_message(self):
        """raise SystemExit("...") exits 1, which KeepAlive would retry."""
        self.assertNotIn("raise SystemExit", self.SRC)

    def test_the_plist_agrees_that_nonzero_means_crash(self):
        plist = (REPO / "com.salman.jobsearch.selector.plist").read_text(
            encoding="utf-8"
        )
        self.assertIn("<key>SuccessfulExit</key>", plist)
        self.assertIn("<key>ThrottleInterval</key>", plist)
        # A permanent poller would own the selector token's getUpdates and 409
        # any hand-run of telegram_select.py.
        self.assertNotIn("<key>RunAtLoad</key>", plist)


class TestResolveToday(unittest.TestCase):
    """The 20h window crosses midnight, so date.today() is the wrong question."""

    def test_an_explicit_day_always_wins(self):
        self.assertEqual(
            sl.resolve_today("2026-08-23", datetime(2031, 1, 1, 3, 0)), "2026-08-23"
        )

    def test_a_restart_after_midnight_stays_on_the_run_that_started_it(self):
        """The bug this guards: a 01:00 relaunch abandoning a live selection."""
        self.assertEqual(
            sl.resolve_today(None, datetime(2026, 8, 24, 1, 0)), "2026-08-23"
        )
        self.assertEqual(
            sl.resolve_today(None, datetime(2026, 8, 24, 7, 59)), "2026-08-23"
        )

    def test_from_the_run_hour_onwards_it_is_the_new_day(self):
        self.assertEqual(
            sl.resolve_today(None, datetime(2026, 8, 24, 8, 0)), "2026-08-24"
        )
        self.assertEqual(
            sl.resolve_today(None, datetime(2026, 8, 24, 20, 30)), "2026-08-24"
        )

    def test_the_cutoff_is_the_hour_the_daily_job_fires(self):
        """A drifted schedule and a drifted listener would silently disagree."""
        plist = (REPO / "com.salman.jobsearch.daily.plist").read_text(encoding="utf-8")
        hour = re.search(
            r"<key>Hour</key>\s*<integer>(\d+)</integer>", plist
        )
        self.assertIsNotNone(hour, "daily plist has no StartCalendarInterval Hour")
        self.assertEqual(sl.RUN_HOUR, int(hour.group(1)))

    def test_the_window_cannot_outlive_the_day_it_is_pinned_to(self):
        """resolve_today's cutoff only works if the window ends before it."""
        self.assertLess(
            sl.RUN_HOUR * 3600 + sl.DEFAULT_WINDOW_SECONDS, 48 * 3600
        )


class TestPartialSendResumes(PhaseBase):
    """A crash mid-send must continue the list, not re-post or abandon it."""

    def rows_missing_from(self, state, count):
        """Mirror of the listener's `missing` computation."""
        return [i for i in range(count) if i not in state.job_message_ids]

    def test_a_half_sent_list_resumes_at_the_tail(self):
        s = self.state(job_message_ids={0: 31, 1: 32, 2: 33})
        self.assertEqual(sl.phase_of(s), "resume")
        self.assertEqual(self.rows_missing_from(s, 6), [3, 4, 5])

    def test_a_fully_sent_list_sends_nothing_more(self):
        s = self.state(job_message_ids={i: 30 + i for i in range(6)})
        self.assertEqual(self.rows_missing_from(s, 6), [])

    def test_a_missing_control_message_is_still_missing_after_a_resume(self):
        """The unusable-chat case: all jobs posted, no Submit button."""
        s = self.state(job_message_ids={i: 30 + i for i in range(6)})
        self.assertIsNone(s.control_message_id)
        self.assertEqual(sl.phase_of(s), "resume")

    def test_the_listener_keys_resending_off_state_not_the_phase_flag(self):
        src = LISTENER.read_text(encoding="utf-8")
        self.assertIn("r.idx not in state.job_message_ids", src)
        self.assertIn("if state.control_message_id is None:", src)


class TestHandoffMarker(unittest.TestCase):
    """Loading the launchd job must not post anything to Telegram.

    `KeepAlive` starts a job the moment it is bootstrapped — `RunAtLoad` being
    absent does not prevent it. This was found the hard way: bootstrapping the
    selector plist ran the listener, which guessed the date off the clock, found
    a previous day's rankset and posted a stale 15-job list to the real chat.

    So the rankset to offer must arrive from Phase 3 and never be inferred.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, payload):
        p = self.tmp / "pending.json"
        p.write_text(payload if isinstance(payload, str) else json.dumps(payload))
        return p

    def test_no_marker_means_no_selection_pending(self):
        self.assertIsNone(sl.read_handoff(self.tmp / "absent.json"))

    def test_a_marker_names_the_rankset_and_the_day(self):
        got = sl.read_handoff(
            self.write({"today": "2026-08-23", "rankset": "/tmp/rs.json"})
        )
        self.assertEqual(got["today"], "2026-08-23")
        self.assertEqual(got["rankset"], Path("/tmp/rs.json"))

    def test_a_half_written_marker_is_refused_not_guessed(self):
        """A truncated write must not fall back to a clock-derived date."""
        self.assertIsNone(sl.read_handoff(self.write({"today": "2026-08-23"})))
        self.assertIsNone(sl.read_handoff(self.write({"rankset": "/tmp/rs.json"})))

    def test_corrupt_json_is_refused_not_raised(self):
        """Under KeepAlive an exception here would crash-loop every 10s."""
        self.assertIsNone(sl.read_handoff(self.write("{not json")))

    def test_a_bare_start_reads_the_marker_rather_than_the_clock(self):
        """main() must consult the marker before resolve_today can name a file."""
        src = LISTENER.read_text(encoding="utf-8")
        marker = src.index("read_handoff()")
        guess = src.index("args.today = resolve_today(args.today)")
        self.assertLess(marker, guess, "the clock is consulted before the marker")

    def test_the_marker_is_not_deleted_after_being_read(self):
        """A crash restart has to find it again to resume the selection."""
        src = LISTENER.read_text(encoding="utf-8")
        self.assertNotIn("HANDOFF_FILE.unlink", src)

    def test_phase_3_writes_the_marker_before_starting_the_listener(self):
        run_daily = (REPO / "scripts" / "run_daily.sh").read_text(encoding="utf-8")
        write = run_daily.index("jobsearch_pending_selection.json")
        start = run_daily.index("launchctl kickstart -k")
        self.assertLess(write, start, "the listener is started with nothing to offer")

    def test_the_marker_and_the_listener_agree_on_the_path(self):
        run_daily = (REPO / "scripts" / "run_daily.sh").read_text(encoding="utf-8")
        self.assertIn(str(sl.HANDOFF_FILE), run_daily)

    def test_a_day_that_offers_nothing_retracts_the_marker(self):
        """Otherwise a restart resumes a list this run declined to offer."""
        run_daily = (REPO / "scripts" / "run_daily.sh").read_text(encoding="utf-8")
        skipped = run_daily.index("Phase 3: skipped")
        self.assertIn(
            "rm -f /tmp/jobsearch_pending_selection.json", run_daily[skipped:]
        )

    def test_cleanup_does_not_age_out_the_pending_marker(self):
        """Phase 7's globs are date-keyed; this file is not.

        Checked with fnmatch against the patterns actually in the script, so a
        future glob broad enough to eat the marker fails here rather than in
        production, where it would silently disarm the next day's resume.
        """
        run_daily = (REPO / "scripts" / "run_daily.sh").read_text(encoding="utf-8")
        globs = re.findall(r"-name '(jobsearch_[^']+)'", run_daily)
        self.assertTrue(globs, "no cleanup globs found to check against")
        for glob in globs:
            self.assertFalse(
                fnmatch.fnmatch(sl.HANDOFF_FILE.name, glob),
                f"cleanup glob {glob!r} matches the handoff marker",
            )


class TestStaleButtonsCannotDriveThisRun(unittest.TestCase):
    """A tap on an old list must not toggle a job in the current one.

    `callback_data` is "t:<idx>" — an index into whichever run is polling. Old
    messages keep live keyboards indefinitely, so without a message-identity
    check a tap on yesterday's job #3 selects today's job #3 and Submit drafts
    an application for a posting that was never displayed.
    """

    def state(self, job_message_ids, control_message_id):
        s = ts.SelectionState(None)
        s.job_message_ids = dict(job_message_ids)
        s.control_message_id = control_message_id
        return s

    def test_a_job_message_from_this_run_is_accepted(self):
        s = self.state({0: 31, 1: 32}, 33)
        self.assertTrue(ts.is_current_message(31, s))
        self.assertTrue(ts.is_current_message(32, s))

    def test_the_control_message_from_this_run_is_accepted(self):
        s = self.state({0: 31}, 33)
        self.assertTrue(ts.is_current_message(33, s))

    def test_a_message_from_an_earlier_run_is_rejected(self):
        """The 2026-08-22 ids against a 2026-08-23 state."""
        s = self.state({0: 50, 1: 51}, 52)
        for stale in (31, 32, 45, 46):
            self.assertFalse(ts.is_current_message(stale, s))

    def test_an_index_colliding_across_runs_is_still_rejected(self):
        """Same idx, different message: the whole point of the check."""
        s = self.state({3: 90}, 99)
        self.assertFalse(ts.is_current_message(33, s))
        self.assertTrue(ts.is_current_message(90, s))

    def test_a_callback_without_a_message_is_rejected(self):
        """Telegram drops the message on very old callbacks."""
        self.assertFalse(ts.is_current_message(None, self.state({0: 31}, 32)))

    def test_nothing_is_accepted_before_the_list_is_sent(self):
        self.assertFalse(ts.is_current_message(31, self.state({}, None)))

    def test_the_guard_applies_the_check_and_not_only_the_allowlist(self):
        """Asserted on BOTH copies of `guard`, because there are two.

        This test used to read telegram_select.py alone. launchd runs
        selector_listener.py, which carried its own `guard` that never got the
        check — so the test passed green for days while the production path was
        unprotected. Any third copy of `guard` must be added here too.
        """
        for rel in ("scripts/telegram_select.py", "scripts/selector_listener.py"):
            src = (REPO / rel).read_text(encoding="utf-8")
            guard = src.index("async def guard(query)")
            body = src[guard : guard + 1200]
            self.assertIn("is_current_message", body, rel)
            self.assertIn("allowed", body, rel)


if __name__ == "__main__":
    unittest.main()
