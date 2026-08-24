"""Guards for the browser extractor's pre-flight health checks.

`daemon_reachable` and `session_healthy` exist so the unattended 08:00 run can decide
*once, cheaply* whether the authenticated browser path is usable, instead of learning
it isn't by watching every job in the enrichment budget fail. That makes one property
safety-critical above all others: **neither may raise.** A health check that throws is
a health check the caller has to wrap in a try, and an unhandled exception inside
Phase 1c takes down a run that had a perfectly good guest-CLI fallback available.

So most of what is pinned here is failure behaviour — daemon down, daemon wedged,
socket timeout, garbage payload, auth wall, CAPTCHA — each asserted to return
`(False, reason)` rather than propagate. The CAPTCHA case is deliberate policy, not an
oversight: the standing instruction is never to work around one, so it reports
unhealthy and the caller falls back to the guest CLI.

`_call` is monkeypatched throughout. These tests never touch the daemon or
linkedin.com, so they are safe to run offline and cost no requests.
"""
import importlib.util
import sys
import unittest
from pathlib import Path
from urllib.error import URLError

REPO = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "linkedin_extract", REPO / "scripts" / "linkedin_extract.py"
)
lx = importlib.util.module_from_spec(_spec)
sys.modules["linkedin_extract"] = lx
_spec.loader.exec_module(lx)


class _Patch:
    """Swap module attributes for the duration of a test, then put them back."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.saved = {}

    def __enter__(self):
        for name, value in self.kwargs.items():
            self.saved[name] = getattr(lx, name)
            setattr(lx, name, value)
        return self

    def __exit__(self, *_exc):
        for name, value in self.saved.items():
            setattr(lx, name, value)
        return False


def raising(exc):
    def _call(*_args, **_kwargs):
        raise exc
    return _call


def returning(value, record=None):
    def _call(action, args=None, **kwargs):
        if record is not None:
            record.append((action, args, kwargs))
        return value
    return _call


def daemon_down_error():
    """The exact error `_call` raises when the daemon socket refuses.

    Reproduced verbatim from scripts/linkedin_extract.py rather than paraphrased,
    because both health checks trim this message at the ". Start it" boundary to keep
    a log line to one line. A paraphrase would let that marker rot silently — the
    trim would stop matching and the log would carry the install instructions on
    every fallback, which is exactly the noise the trim exists to prevent.
    """
    return lx.ExtractionError(
        f"cannot reach the WebBridge daemon at {lx.DAEMON} "
        "(<urlopen error [Errno 61] Connection refused>). "
        "Start it with `~/.kimi-webbridge/bin/kimi-webbridge start`.",
        url="", attempts=0, diagnostics={},
    )


class DaemonReachableUsesAnActionTheDaemonActuallyHas(unittest.TestCase):
    """`list_sessions` does not exist. Probing with it reports a healthy daemon down."""

    def test_probe_action_is_list_tabs(self):
        seen = []
        with _Patch(_call=returning([], seen)):
            ok, _ = lx.daemon_reachable()
        self.assertTrue(ok)
        self.assertEqual([a for a, _, _ in seen], ["list_tabs"],
                         "the daemon rejects unknown actions; list_tabs is in its "
                         "advertised vocabulary and list_sessions is not")

    def test_probe_is_cheap_by_default(self):
        """A pre-flight check must not inherit the 120s navigation timeout."""
        seen = []
        with _Patch(_call=returning([], seen)):
            lx.daemon_reachable()
        _, _, kwargs = seen[0]
        self.assertLessEqual(kwargs.get("timeout", lx.NAV_TIMEOUT), 10,
                             "a down daemon must be detected in seconds, not minutes")

    def test_tab_count_is_reported_from_a_list_payload(self):
        with _Patch(_call=returning([{"id": 1}, {"id": 2}])):
            ok, detail = lx.daemon_reachable()
        self.assertTrue(ok)
        self.assertIn("2 tab", detail)

    def test_tab_count_is_reported_from_a_dict_payload(self):
        """The daemon has returned both shapes; neither may crash the probe."""
        with _Patch(_call=returning({"tabs": [{"id": 1}]})):
            ok, detail = lx.daemon_reachable()
        self.assertTrue(ok)
        self.assertIn("1 tab", detail)

    def test_a_reachable_daemon_with_no_tabs_is_still_reachable(self):
        with _Patch(_call=returning([])):
            ok, _ = lx.daemon_reachable()
        self.assertTrue(ok, "zero tabs means an idle browser, not a dead daemon")


class DaemonReachableNeverRaises(unittest.TestCase):
    """Every failure mode returns a verdict. Phase 1c must not need a try block."""

    def test_daemon_down_is_reported_not_raised(self):
        with _Patch(_call=raising(daemon_down_error())):
            ok, detail = lx.daemon_reachable()
        self.assertFalse(ok)
        self.assertIn("cannot reach the WebBridge daemon", detail)
        self.assertNotIn("kimi-webbridge start", detail,
                         "the install instructions are trimmed off the log line")
        self.assertNotIn("\n", detail, "a log line must stay one line")

    def test_wedged_extension_is_distinguished_from_unreachable(self):
        """`ok: false` means the daemon answered — a different remedy than restarting."""
        with _Patch(_call=raising(RuntimeError("unknown action"))):
            ok, detail = lx.daemon_reachable()
        self.assertFalse(ok)
        self.assertIn("rejected list_tabs", detail)

    def test_unexpected_exception_is_caught_too(self):
        with _Patch(_call=raising(URLError("timed out"))):
            ok, detail = lx.daemon_reachable()
        self.assertFalse(ok)
        self.assertIn("URLError", detail)

    def test_even_a_bare_exception_is_contained(self):
        with _Patch(_call=raising(Exception("something new"))):
            ok, _ = lx.daemon_reachable()
        self.assertFalse(ok)


class SessionHealthyDetectsALapsedLogin(unittest.TestCase):
    """The whole point: fall back once, up front, not once per job in the budget."""

    @staticmethod
    def probe(**overrides):
        payload = {"authWall": False, "captcha": False, "signedIn": True,
                   "readyState": "complete", "chars": 4200}
        payload.update(overrides)

        def _call(action, args=None, **kwargs):
            return {}          # the navigate and bringToFront legs
        return _call, payload

    def run_probe(self, **overrides):
        nav, payload = self.probe(**overrides)
        # Delay zeroed so the ambiguous branch, which polls, stays fast in tests.
        with _Patch(_call=nav, _evaluate=lambda _code: payload,
                    HEALTH_SETTLE_DELAY=0):
            return lx.session_healthy()

    def test_a_live_session_is_healthy(self):
        ok, detail = self.run_probe()
        self.assertTrue(ok)
        self.assertIn("authenticated", detail)

    def test_the_tab_is_foregrounded_before_the_page_is_read(self):
        """A hidden tab never mounts LinkedIn's SPA, so a probe without this reads
        page furniture and reports a good session as unauthenticated. Regression:
        the first live run of this check did exactly that at 1,172 chars."""
        seen = []

        def nav(action, args=None, **kwargs):
            seen.append((action, args))
            return {}
        with _Patch(_call=nav, _evaluate=lambda _c: {
                "authWall": False, "captcha": False, "signedIn": True, "chars": 9}):
            lx.session_healthy()
        self.assertIn("cdp", [a for a, _ in seen])
        cdp = next(args for a, args in seen if a == "cdp")
        self.assertEqual(cdp["method"], "Page.bringToFront")
        self.assertLess([a for a, _ in seen].index("navigate"),
                        [a for a, _ in seen].index("cdp"),
                        "foreground after navigating, not before")

    def test_auth_wall_is_unhealthy(self):
        ok, detail = self.run_probe(authWall=True, signedIn=False)
        self.assertFalse(ok)
        self.assertIn("not authenticated", detail)

    def test_captcha_reports_unhealthy_rather_than_being_worked_around(self):
        ok, detail = self.run_probe(captcha=True)
        self.assertFalse(ok)
        self.assertIn("CAPTCHA", detail)
        self.assertIn("not attempting", detail.lower().replace("—", "-"))

    def test_captcha_outranks_a_present_signed_in_marker(self):
        """A challenge on the page means the session is unusable, chrome or not."""
        ok, _ = self.run_probe(captcha=True, signedIn=True)
        self.assertFalse(ok)

    def test_no_wall_and_no_signed_in_marker_fails_closed(self):
        """Ambiguity costs nothing to resolve pessimistically: guest returns full
        bodies for these postings anyway (11/11 rows, 2.5k-10.1k chars, 2026-08-24)."""
        ok, detail = self.run_probe(signedIn=False, chars=180)
        self.assertFalse(ok)
        self.assertIn("180", detail)

    def test_probe_navigates_to_a_page_that_requires_a_session(self):
        seen = []

        def nav(action, args=None, **kwargs):
            seen.append((action, args))
            return {}
        with _Patch(_call=nav, _evaluate=lambda _c: {
                "authWall": False, "captcha": False, "signedIn": True, "chars": 1}):
            lx.session_healthy()
        self.assertEqual(seen[0][0], "navigate")
        self.assertIn("linkedin.com/jobs", seen[0][1]["url"])

    def test_probe_reuses_the_tab_rather_than_opening_one(self):
        """One request per run, and no tab the user did not ask for."""
        seen = []

        def nav(action, args=None, **kwargs):
            seen.append((action, args))
            return {}
        with _Patch(_call=nav, _evaluate=lambda _c: {
                "authWall": False, "captcha": False, "signedIn": True, "chars": 1}):
            lx.session_healthy()
        self.assertFalse(seen[0][1].get("newTab"),
                         "the health check must not spawn tabs")


class SessionHealthySettlesBeforeItJudges(unittest.TestCase):
    """A verdict read off a half-rendered page forces the fallback for a whole run."""

    def probe_sequence(self, payloads):
        """Return (verdict, probes_taken) for a page that mounts progressively."""
        calls = []

        def _evaluate(_code):
            calls.append(1)
            return payloads[min(len(calls) - 1, len(payloads) - 1)]
        with _Patch(_call=lambda *a, **k: {}, _evaluate=_evaluate,
                    HEALTH_SETTLE_DELAY=0):
            return lx.session_healthy(), len(calls)

    def test_a_slow_mount_is_waited_out_rather_than_failed(self):
        blank = {"authWall": False, "captcha": False, "signedIn": False,
                 "readyState": "loading", "chars": 1172}
        mounted = {"authWall": False, "captcha": False, "signedIn": True,
                   "readyState": "complete", "chars": 8400}
        (ok, detail), probes = self.probe_sequence([blank, blank, mounted])
        self.assertTrue(ok, f"a session that mounts on the third look is healthy: {detail}")
        self.assertEqual(probes, 3)

    def test_a_definitive_signal_ends_the_poll_immediately(self):
        wall = {"authWall": True, "captcha": False, "signedIn": False,
                "readyState": "complete", "chars": 900}
        (ok, _), probes = self.probe_sequence([wall])
        self.assertFalse(ok)
        self.assertEqual(probes, 1, "a lapsed login must not cost the full settle wait")

    def test_a_page_that_never_mounts_is_bounded_and_fails_closed(self):
        blank = {"authWall": False, "captcha": False, "signedIn": False,
                 "readyState": "loading", "chars": 12}
        (ok, detail), probes = self.probe_sequence([blank])
        self.assertFalse(ok)
        self.assertEqual(probes, lx.HEALTH_SETTLE_ATTEMPTS)
        self.assertIn("readyState=loading", detail,
                      "the log must show why it was ambiguous, not just that it was")


class SessionHealthyNeverRaises(unittest.TestCase):
    def test_daemon_down_during_the_probe_is_reported(self):
        with _Patch(_call=raising(daemon_down_error())):
            ok, detail = lx.session_healthy()
        self.assertFalse(ok)
        self.assertIn("cannot reach the WebBridge daemon", detail)

    def test_navigation_failure_is_reported(self):
        with _Patch(_call=raising(RuntimeError("navigate failed"))):
            ok, detail = lx.session_healthy()
        self.assertFalse(ok)
        self.assertIn("RuntimeError", detail)

    def test_a_non_object_probe_result_is_reported_not_unpacked(self):
        """`_evaluate` returns `{"raw": ...}` or a bare string on odd payloads."""
        with _Patch(_call=returning({}), _evaluate=lambda _c: "not json"):
            ok, detail = lx.session_healthy()
        self.assertFalse(ok)
        self.assertIn("str", detail)

    def test_none_result_is_reported(self):
        with _Patch(_call=returning({}), _evaluate=lambda _c: None):
            ok, _ = lx.session_healthy()
        self.assertFalse(ok)


class HealthChecksReturnAUniformContract(unittest.TestCase):
    """Callers branch on [0] and log [1]; both functions must agree on the shape."""

    def test_both_return_bool_and_str(self):
        cases = [
            (lambda: lx.daemon_reachable(), returning([]), None),
            (lambda: lx.daemon_reachable(), raising(RuntimeError("x")), None),
            (lambda: lx.session_healthy(), returning({}),
             lambda _c: {"authWall": False, "captcha": False,
                         "signedIn": True, "chars": 1}),
            (lambda: lx.session_healthy(), raising(RuntimeError("x")), None),
        ]
        for i, (fn, call, ev) in enumerate(cases):
            with self.subTest(case=i):
                patches = {"_call": call}
                if ev is not None:
                    patches["_evaluate"] = ev
                with _Patch(**patches):
                    got = fn()
                self.assertIsInstance(got, tuple)
                self.assertEqual(len(got), 2)
                self.assertIsInstance(got[0], bool)
                self.assertIsInstance(got[1], str)
                self.assertTrue(got[1], "an empty reason tells the log nothing")


if __name__ == "__main__":
    unittest.main()
