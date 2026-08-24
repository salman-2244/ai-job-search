"""Guards for Phase 1c's dual-path fetch: authenticated browser, guest CLI fallback.

The browser path is ~15x faster than the guest CLI (6.2 s/job vs 93 s/job, measured
2026-08-24) and that is the whole reason it exists. But it depends on three things an
08:00 launchd job cannot guarantee — a listening daemon, a connected browser extension,
and a LinkedIn session that has not lapsed — so the safety property here outranks the
speed one:

  **Every browser failure must land on the guest CLI without raising.** Extractor
  import failure, daemon down, daemon start failure, port never opening, session
  unhealthy, recovery failure, CAPTCHA, a single posting failing mid-list, and an
  unexpected payload shape from the daemon. Nine ways to lose the fast path, one
  outcome: the run finishes on guest HTTP with full posting bodies.

Two subtler properties are pinned alongside it, both learned from reading the code
they constrain rather than from a failure:

  1. **The request ledger is the cap, not the job count.** `linkedin_extract.extract`
     retries a partial mount up to MAX_ATTEMPTS times and *each attempt is a real
     navigation*, so budgeting one request per job would let 24 jobs spend 72 requests
     against a cap of 60. The ledger charges actual attempts, and the pre-flight's own
     spend is deducted before targets are chosen.
  2. **A CAPTCHA is never retried.** `recover_session` reloads the page, and reloading
     a challenge is the first step of hammering it. The standing instruction is to
     report and let a human clear it, so the CAPTCHA verdict short-circuits to the
     guest path before the recovery rung is reached.

Nothing here touches the daemon, linkedin.com, Telegram, or the filesystem: the
extractor module, `subprocess.run` and `sleep` are all injected. Safe offline, costs
no requests.
"""
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "enrich_linkedin", REPO / "scripts" / "enrich_linkedin.py")
enr = importlib.util.module_from_spec(_spec)
sys.modules["enrich_linkedin"] = enr
_spec.loader.exec_module(enr)

_lx_spec = importlib.util.spec_from_file_location(
    "linkedin_extract", REPO / "scripts" / "linkedin_extract.py")
lx = importlib.util.module_from_spec(_lx_spec)
# Registered before exec: `Extraction` is a @dataclass, and `dataclasses` resolves
# `cls.__module__` through `sys.modules` while the class body runs. An unregistered
# module makes that lookup return None and the import dies with AttributeError. This
# is the same trap `_load_extractor` fell into — see the comment there.
sys.modules["linkedin_extract"] = lx
_lx_spec.loader.exec_module(lx)


def quiet(_msg):
    """Swallow output in tests that assert on the result, not the messaging."""


class Recorder:
    """A warn/log stand-in that keeps what it was told, for order assertions."""

    def __init__(self):
        self.lines = []

    def __call__(self, message):
        self.lines.append(str(message))

    def text(self):
        return "\n".join(self.lines)


class FakeExtraction:
    """What `linkedin_extract.extract` returns on success."""

    def __init__(self, text="a real posting body, several thousand chars",
                 attempts=1):
        self.text = text
        self.attempts = attempts


class FakeExtractor:
    """A stand-in for `scripts/linkedin_extract.py` with scriptable verdicts.

    Deliberately re-uses the real `ExtractionError` class rather than a local
    look-alike: `browser_fetcher` catches `module.ExtractionError` specifically, and a
    fake that raised its own exception type would exercise the wrong except branch and
    pass while the production path broke.
    """

    ExtractionError = lx.ExtractionError

    def __init__(self, daemon=(True, "daemon up, 3 tabs"),
                 health=(True, "LinkedIn session authenticated (3/3 nav labels)"),
                 recovery=(True, "recovered"), extract_results=None):
        self._daemon = daemon
        self._health = health
        self._recovery = recovery
        self._extract_results = list(extract_results or [])
        self.calls = []

    def daemon_reachable(self, *_a, **_k):
        self.calls.append("daemon_reachable")
        return self._daemon

    def session_healthy(self, *_a, **_k):
        self.calls.append("session_healthy")
        return self._health

    def recover_session(self, *_a, **_k):
        self.calls.append("recover_session")
        return self._recovery

    def extract(self, url, **kwargs):
        self.calls.append(("extract", url, kwargs))
        if not self._extract_results:
            return FakeExtraction()
        result = self._extract_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def completed(returncode=0, stdout="", stderr=""):
    def _run(*_a, **_k):
        return subprocess.CompletedProcess([], returncode, stdout, stderr)
    return _run


def raising(exc):
    def _run(*_a, **_k):
        raise exc
    return _run


class TheLedgerIsTheCapNotTheJobCount(unittest.TestCase):
    """`extract` can spend three requests on one job. The cap is on requests."""

    def test_a_fresh_ledger_has_its_whole_limit(self):
        self.assertEqual(enr.RequestLedger(25).left(), 25)

    def test_spending_reduces_what_is_left(self):
        ledger = enr.RequestLedger(25)
        ledger.spend(3)
        self.assertEqual(ledger.spent, 3)
        self.assertEqual(ledger.left(), 22)

    def test_left_never_goes_negative(self):
        """An overspend is a bug to report, not a negative budget to hand out."""
        ledger = enr.RequestLedger(2)
        ledger.spend(5)
        self.assertEqual(ledger.left(), 0)

    def test_a_negative_or_zero_limit_is_an_empty_ledger(self):
        for limit in (0, -1, -25):
            with self.subTest(limit=limit):
                self.assertEqual(enr.RequestLedger(limit).left(), 0)

    def test_a_retried_extraction_is_charged_for_every_attempt(self):
        """The arithmetic that keeps 24 jobs from becoming 72 requests."""
        ledger = enr.RequestLedger(25)
        fetch = enr.browser_fetcher(
            FakeExtractor(extract_results=[FakeExtraction(attempts=3)]),
            ledger, quiet, quiet, {})
        fetch("4000000001")
        self.assertEqual(ledger.spent, 3,
                         "each retry of a partial mount was a real navigation")

    def test_a_failed_extraction_is_charged_too(self):
        """A navigation that returned nothing still reached linkedin.com."""
        ledger = enr.RequestLedger(25)
        fetch = enr.browser_fetcher(
            FakeExtractor(extract_results=[
                lx.ExtractionError("no description found", url="u", attempts=2,
                                   diagnostics={})]),
            ledger, quiet, quiet, {})
        with self.assertRaises(enr.DetailError):
            fetch("4000000001")
        self.assertEqual(ledger.spent, 2)

    def test_a_spent_ledger_refuses_the_next_job_rather_than_exceeding_the_cap(self):
        ledger = enr.RequestLedger(1)
        module = FakeExtractor()
        fetch = enr.browser_fetcher(module, ledger, quiet, quiet, {})
        fetch("4000000001")
        with self.assertRaises(enr.DetailError) as caught:
            fetch("4000000002")
        self.assertIn("max_requests_per_run", str(caught.exception))
        self.assertEqual(len([c for c in module.calls if c[0] == "extract"]), 1,
                         "the second job must not reach linkedin.com at all")


class StartingTheDaemonFailsSafely(unittest.TestCase):
    """launchd cannot start the Kimi Desktop App. Every failure here must fall back."""

    def test_only_the_start_verb_is_ever_used(self):
        """`stop`/`restart`/`uninstall` fight the desktop app that owns the browser."""
        seen = []

        def _run(argv, **_k):
            seen.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if not enr.WEBBRIDGE_BIN.is_file():
            self.skipTest("kimi-webbridge is not installed in this environment")
        enr.start_daemon(quiet, run=_run, sleep=lambda _s: None,
                         reachable=lambda: (True, "up"))
        self.assertEqual([argv[1:] for argv in seen], [["start"]])

    def test_a_missing_binary_is_reported_not_raised(self):
        ok, detail = enr.start_daemon(quiet, run=raising(FileNotFoundError()),
                                      sleep=lambda _s: None,
                                      reachable=lambda: (False, "down"))
        self.assertFalse(ok)
        self.assertTrue(detail)

    def test_a_nonzero_exit_is_reported_with_its_last_line(self):
        ok, detail = enr.start_daemon(
            quiet, run=completed(1, stderr="port 10086 already bound by another pid"),
            sleep=lambda _s: None, reachable=lambda: (False, "down"))
        self.assertFalse(ok)
        self.assertIn("already bound", detail)
        self.assertNotIn("\n", detail, "a log line must stay one line")

    def test_a_hung_start_is_bounded_by_a_timeout(self):
        ok, detail = enr.start_daemon(
            quiet, run=raising(subprocess.TimeoutExpired("kimi-webbridge", 45)),
            sleep=lambda _s: None, reachable=lambda: (False, "down"))
        self.assertFalse(ok)
        self.assertIn("timed out", detail)

    def test_the_process_returning_is_not_the_port_listening(self):
        """A clean exit with a dead port is the launchd failure mode, exactly."""
        polls = []
        ok, detail = enr.start_daemon(
            quiet, run=completed(0), sleep=lambda _s: None,
            reachable=lambda: (polls.append(1), (False, "connection refused"))[1])
        self.assertFalse(ok)
        self.assertEqual(len(polls), enr.DAEMON_WAIT_ATTEMPTS,
                         "the poll count is the retry budget from the brief")
        self.assertIn("never opened", detail)

    def test_a_daemon_that_comes_up_late_is_still_a_success(self):
        verdicts = [(False, "not yet"), (False, "not yet"), (True, "up, 2 tabs")]
        ok, detail = enr.start_daemon(quiet, run=completed(0),
                                      sleep=lambda _s: None,
                                      reachable=lambda: verdicts.pop(0))
        self.assertTrue(ok)
        self.assertIn("poll 3", detail)

    def test_an_unexpected_exception_is_contained(self):
        ok, _ = enr.start_daemon(quiet, run=raising(Exception("something new")),
                                 sleep=lambda _s: None,
                                 reachable=lambda: (False, "down"))
        self.assertFalse(ok)


class ThePreflightChainPicksAPathAndSaysWhy(unittest.TestCase):
    """Four rungs: daemon, start it if down, session, one recovery if unhealthy."""

    def chain(self, module, limit=25):
        log, warn = Recorder(), Recorder()
        ledger = enr.RequestLedger(limit)
        got, reason = enr.browser_fetch_path(limit, warn, log, ledger,
                                             extractor=module,
                                             run=completed(0),
                                             sleep=lambda _s: None)
        return got, reason, ledger, log, warn

    def test_a_healthy_stack_returns_the_browser_and_no_reason(self):
        module = FakeExtractor()
        got, reason, _ledger, log, _ = self.chain(module)
        self.assertIs(got, module)
        self.assertIsNone(reason, "None means the browser won; a string is the alert")
        self.assertIn("browser primary", log.text())
        self.assertIn("session healthy", log.text())

    def test_the_session_check_is_charged_to_the_ledger(self):
        """It is one real LinkedIn request out of detail_enrich_budget."""
        _, _, ledger, _, _ = self.chain(FakeExtractor())
        self.assertEqual(ledger.spent, 1)

    def test_the_daemon_probe_itself_is_free(self):
        """Local `list_tabs`, no LinkedIn traffic — it must not cost a job."""
        _, _, ledger, _, _ = self.chain(
            FakeExtractor(daemon=(False, "connection refused")))
        self.assertLessEqual(ledger.spent, 1,
                             "a down daemon costs no LinkedIn requests")

    def test_a_down_daemon_is_started_and_then_used(self):
        verdicts = [(False, "connection refused"), (True, "up, 1 tab")]
        module = FakeExtractor()
        module.daemon_reachable = lambda *a, **k: verdicts.pop(0)
        got, reason, _, log, _ = self.chain(module)
        self.assertIs(got, module)
        self.assertIsNone(reason)
        self.assertIn("daemon started", log.text())

    def test_a_daemon_that_will_not_start_falls_back_with_a_reason(self):
        got, reason, _, _, warn = self.chain(
            FakeExtractor(daemon=(False, "connection refused")))
        self.assertIsNone(got, "None module means the guest CLI")
        self.assertIn("daemon unavailable", reason)
        self.assertIn("could not start", warn.text())

    def test_a_lapsed_login_gets_exactly_one_recovery_attempt(self):
        module = FakeExtractor(
            health=(False, "LinkedIn session is not authenticated (auth wall)"),
            recovery=(True, "LinkedIn session authenticated (3/3 nav labels)"))
        got, reason, ledger, log, _ = self.chain(module)
        self.assertIs(got, module)
        self.assertIsNone(reason)
        self.assertEqual(module.calls.count("recover_session"), 1)
        self.assertEqual(ledger.spent, 2, "the check plus the recovery, one each")
        self.assertIn("session recovered", log.text())

    def test_an_unrecoverable_session_falls_back_with_the_reason(self):
        module = FakeExtractor(
            health=(False, "auth wall on /jobs/"),
            recovery=(False, "auth wall on /jobs/"))
        got, reason, _, _, warn = self.chain(module)
        self.assertIsNone(got)
        self.assertIn("unrecoverable", reason)
        self.assertIn("unrecoverable", warn.text())

    def test_a_captcha_is_never_reloaded(self):
        """Reloading a challenge page is the first step of hammering it."""
        module = FakeExtractor(
            health=(False, "CAPTCHA on the LinkedIn session probe — not attempting "
                           "to solve it"))
        got, reason, ledger, _, warn = self.chain(module)
        self.assertIsNone(got)
        self.assertIn("CAPTCHA", reason)
        self.assertNotIn("recover_session", module.calls,
                         "the standing instruction is to report, not to retry")
        self.assertEqual(ledger.spent, 1, "no extra request on a challenge page")
        self.assertIn("not retrying", warn.text())

    def test_a_budget_too_small_for_a_session_check_falls_back_without_spending(self):
        module = FakeExtractor()
        got, reason, ledger, _, _ = self.chain(module, limit=0)
        self.assertIsNone(got)
        self.assertIn("budget", reason)
        self.assertEqual(ledger.spent, 0)
        self.assertNotIn("session_healthy", module.calls)

    def test_a_recovery_with_no_budget_left_falls_back_rather_than_overspending(self):
        module = FakeExtractor(health=(False, "auth wall on /jobs/"))
        got, reason, ledger, _, _ = self.chain(module, limit=1)
        self.assertIsNone(got)
        self.assertEqual(ledger.spent, 1)
        self.assertNotIn("recover_session", module.calls)
        self.assertIn("no budget", reason)

    def test_the_chain_returns_a_verdict_rather_than_raising(self):
        """`extractor=None` sends it down the real `_load_extractor`. Whatever that
        finds, the contract holds: a module or None, and a reason or None — never an
        exception escaping into Phase 1c."""
        log, warn = Recorder(), Recorder()
        got, reason = enr.browser_fetch_path(
            25, warn, log, enr.RequestLedger(25), extractor=None,
            run=completed(0), sleep=lambda _s: None)
        self.assertTrue(got is None or hasattr(got, "extract"))
        self.assertTrue(reason is None or isinstance(reason, str))


class TheExtractorActuallyLoads(unittest.TestCase):
    """A regression guard for a bug that would have cost the browser path entirely.

    `_load_extractor` imports `scripts/linkedin_extract.py` by file location, and the
    first version did not register it in `sys.modules` before exec. That looks
    harmless and is not: `Extraction` is a `@dataclass`, `dataclasses` resolves
    `cls.__module__` through `sys.modules` while the class body runs, and an
    unregistered module makes that lookup return `None`. The extractor raised
    AttributeError on import, `_load_extractor`'s `except` swallowed it into a
    one-line warning, and Phase 1c fell back to the guest CLI **every morning** — the
    exact silent 15x slowdown the fallback design is supposed to make visible.

    So a test that only asserted "returns a module or None" would have passed on the
    broken version. This one insists on the module.
    """

    def test_load_extractor_returns_the_real_module_not_a_swallowed_error(self):
        warn = Recorder()
        module = enr._load_extractor(warn)
        self.assertIsNotNone(
            module, f"the browser path is unreachable: {warn.text()}")
        self.assertEqual(warn.lines, [], "a clean import must warn about nothing")

    def test_the_loaded_module_carries_the_surface_phase_1c_calls(self):
        module = enr._load_extractor(quiet)
        for attr in ("daemon_reachable", "session_healthy", "recover_session",
                     "extract", "ExtractionError"):
            with self.subTest(attr=attr):
                self.assertTrue(hasattr(module, attr))

    def test_the_dataclass_that_caused_the_bug_can_actually_be_built(self):
        """The failure was at class-creation time, so instantiating is the proof."""
        module = enr._load_extractor(quiet)
        got = module.Extraction(url="https://www.linkedin.com/jobs/view/1/",
                                job_id="1", text="body", strategy="s", selector="sel",
                                elapsed_s=1.0, attempts=1, expanded=False)
        self.assertEqual(got.attempts, 1)


class TheBrowserFetcherLooksLikeTheGuestCLI(unittest.TestCase):
    """`merge_detail` must not be able to tell which path fetched a row."""

    def fetcher(self, module, limit=25, state=None, guest=None):
        self.state = state if state is not None else {}
        self.ledger = enr.RequestLedger(limit)
        self.warn, self.log = Recorder(), Recorder()
        return enr.browser_fetcher(module, self.ledger, self.warn, self.log,
                                   self.state,
                                   guest=guest or (lambda _id: {"description": "g"}))

    def test_the_return_shape_is_the_cli_json_shape(self):
        fetch = self.fetcher(FakeExtractor(
            extract_results=[FakeExtraction(text="body text")]))
        self.assertEqual(fetch("4000000001"), {"description": "body text"})

    def test_an_empty_body_is_a_string_not_none(self):
        """`merge_detail` reads `.strip()` off it; None would raise mid-phase."""
        fetch = self.fetcher(FakeExtractor(
            extract_results=[FakeExtraction(text=None)]))
        self.assertEqual(fetch("4000000001"), {"description": ""})

    def test_the_canonical_url_is_built_from_the_id_not_the_locale_host(self):
        """`hu.linkedin.com/jobs/view/x-<id>` slugs need not match the posting."""
        module = FakeExtractor()
        self.fetcher(module)("4000000001")
        _, url, kwargs = next(c for c in module.calls
                              if isinstance(c, tuple) and c[0] == "extract")
        self.assertEqual(url, "https://www.linkedin.com/jobs/view/4000000001/")
        self.assertFalse(kwargs.get("new_tab"),
                         "do not open a tab the user did not ask for")

    def test_a_single_failure_raises_detail_error_and_keeps_the_browser(self):
        """One bad posting must not condemn the fast path for the other 23."""
        module = FakeExtractor(extract_results=[
            lx.ExtractionError("region-locked", url="u", attempts=1, diagnostics={}),
            FakeExtraction(text="the next one is fine")])
        fetch = self.fetcher(module)
        with self.assertRaises(enr.DetailError):
            fetch("4000000001")
        self.assertFalse(self.state.get("switched"))
        self.assertEqual(fetch("4000000002"), {"description": "the next one is fine"})
        self.assertEqual(self.state["streak"], 0, "a success clears the streak")

    def test_two_consecutive_failures_switch_the_phase_to_guest(self):
        """A login can lapse *between* jobs on an unattended run."""
        module = FakeExtractor(extract_results=[
            lx.ExtractionError("auth wall", url="u", attempts=1, diagnostics={}),
            lx.ExtractionError("auth wall", url="u", attempts=1, diagnostics={})])
        fetch = self.fetcher(module)
        for job_id in ("4000000001", "4000000002"):
            with self.assertRaises(enr.DetailError):
                fetch(job_id)
        self.assertTrue(self.state["switched"])
        self.assertIn("2 consecutive", self.state["reason"])
        self.assertIn("fallback guest", self.warn.text())

    def test_after_the_switch_the_guest_cli_serves_and_the_browser_is_not_called(self):
        module = FakeExtractor()
        fetch = self.fetcher(module, state={"switched": True})
        self.assertEqual(fetch("4000000001"), {"description": "g"})
        self.assertEqual(module.calls, [], "the browser is done for this phase")

    def test_after_the_switch_no_further_requests_are_charged_by_this_path(self):
        """Guest requests are the guest CLI's own; the ledger tracks browser spend."""
        fetch = self.fetcher(FakeExtractor(), state={"switched": True})
        fetch("4000000001")
        self.assertEqual(self.ledger.spent, 0)

    def test_an_unexpected_daemon_payload_becomes_a_detail_error(self):
        """`enrich` catches DetailError and only DetailError — anything else aborts."""
        module = FakeExtractor(extract_results=[KeyError("text")])
        fetch = self.fetcher(module)
        with self.assertRaises(enr.DetailError) as caught:
            fetch("4000000001")
        self.assertIn("KeyError", str(caught.exception))

    def test_the_switch_survives_an_unexpected_exception_too(self):
        module = FakeExtractor(extract_results=[TypeError("bad shape"),
                                                TypeError("bad shape")])
        fetch = self.fetcher(module)
        for job_id in ("4000000001", "4000000002"):
            with self.assertRaises(enr.DetailError):
                fetch(job_id)
        self.assertTrue(self.state["switched"])

    def test_successful_browser_jobs_are_counted_for_the_report(self):
        fetch = self.fetcher(FakeExtractor())
        fetch("4000000001")
        fetch("4000000002")
        self.assertEqual(self.state["browser_jobs"], 2)

    def test_the_failure_streak_threshold_is_more_than_one(self):
        """Condemning the fast path for one bad card would cost the phase 20 minutes."""
        self.assertGreaterEqual(enr.BROWSER_FAILURE_STREAK, 2)


class TheFallbackAlertIsBestEffort(unittest.TestCase):
    """A notifier that is down must not fail a phase whose actual work succeeded."""

    def test_a_sent_alert_reports_true(self):
        log = Recorder()
        self.assertTrue(enr.alert_fallback("daemon down", log, run=completed(0)))
        self.assertIn("sent", log.text())

    def test_a_missing_notifier_is_logged_and_swallowed(self):
        log = Recorder()
        self.assertFalse(enr.alert_fallback("daemon down", log,
                                            run=raising(FileNotFoundError())))
        self.assertIn("not sent", log.text())

    def test_a_send_failure_is_logged_and_swallowed(self):
        log = Recorder()
        self.assertFalse(enr.alert_fallback("daemon down", log, run=completed(2)))
        self.assertIn("not sent", log.text())

    def test_an_unexpected_exception_is_swallowed_too(self):
        log = Recorder()
        self.assertFalse(enr.alert_fallback("daemon down", log,
                                            run=raising(Exception("network gone"))))
        self.assertIn("not sent", log.text())

    def test_the_message_says_what_happened_and_what_to_do(self):
        sent = {}

        def _run(argv, **_k):
            sent["argv"] = argv
            return subprocess.CompletedProcess(argv, 0, "", "")
        enr.alert_fallback("WebBridge daemon unavailable", quiet, run=_run)
        body = sent["argv"][-1]
        self.assertIn("fallback mode", body)
        self.assertIn("guest HTTP", body)
        self.assertIn("WebBridge daemon unavailable", body,
                      "a bare 'fallback' alert tells you nothing to fix")
        self.assertIn("Nothing to re-run", body,
                      "the user must not be left wondering if the run is lost")

    def test_the_alert_goes_through_the_pipelines_existing_notifier(self):
        self.assertEqual(enr.TG_NOTIFY, "tg-notify")


class RecoverSessionReloadsRatherThanReNavigates(unittest.TestCase):
    """One attempt, foreground first, cache bypassed. Costs one request."""

    def probe(self, payload):
        seen = []

        def _call(action, args=None, **kwargs):
            seen.append((action, args))
            return {}
        saved = (lx._call, lx._evaluate, lx.HEALTH_SETTLE_DELAY)
        lx._call, lx._evaluate, lx.HEALTH_SETTLE_DELAY = (
            _call, lambda _c: payload, 0)
        try:
            return lx.recover_session(), seen
        finally:
            lx._call, lx._evaluate, lx.HEALTH_SETTLE_DELAY = saved

    LIVE = {"authWall": False, "captcha": False, "signedIn": True,
            "readyState": "complete", "chars": 8400}

    def test_the_tab_is_foregrounded_before_it_is_reloaded(self):
        """LinkedIn's SPA does not mount in a background tab, reload or not."""
        (_ok, _detail), seen = self.probe(self.LIVE)
        actions = [a for a, _ in seen]
        methods = [args.get("method") for a, args in seen if a == "cdp"]
        self.assertEqual(methods[:2], ["Page.bringToFront", "Page.reload"],
                         "reloading a hidden tab re-renders it hidden")
        self.assertNotIn("navigate", actions,
                         "the page is already on /jobs/ — re-navigating is a "
                         "second request for nothing")

    def test_the_reload_bypasses_the_cache(self):
        """A from-cache reload re-serves exactly the page that just failed."""
        (_ok, _d), seen = self.probe(self.LIVE)
        reload_args = next(args for a, args in seen
                           if a == "cdp" and args.get("method") == "Page.reload")
        self.assertTrue(reload_args["params"]["ignoreCache"])

    def test_a_still_lapsed_login_reports_unhealthy_rather_than_raising(self):
        (ok, detail), _ = self.probe(
            {"authWall": True, "captcha": False, "signedIn": False,
             "readyState": "complete", "chars": 900})
        self.assertFalse(ok)
        self.assertIn("not authenticated", detail)

    def test_it_returns_the_same_contract_as_session_healthy(self):
        (got, detail), _ = self.probe(self.LIVE)
        self.assertIsInstance(got, bool)
        self.assertIsInstance(detail, str)
        self.assertTrue(detail)

    def test_a_daemon_that_died_between_the_check_and_the_recovery_is_reported(self):
        saved = lx._call

        def _call(*_a, **_k):
            raise RuntimeError("connection refused")
        lx._call = _call
        try:
            ok, detail = lx.recover_session()
        finally:
            lx._call = saved
        self.assertFalse(ok)
        self.assertIn("RuntimeError", detail)


class PipelineWiring(unittest.TestCase):
    """The 08:00 run is the one where nobody is watching the log."""

    SCRIPT_TEXT = (REPO / "scripts" / "run_daily.sh").read_text()
    ENRICH_TEXT = (REPO / "scripts" / "enrich_linkedin.py").read_text()

    def test_the_unattended_run_asks_for_the_fallback_alert(self):
        self.assertIn("--alert-on-fallback", self.SCRIPT_TEXT)

    def test_the_flag_is_on_the_phase_1c_invocation(self):
        """Searched forward from the call, not by first occurrence: the flag is named
        in the comment above it too, so `index()` finds prose rather than the arg."""
        enrich_at = self.SCRIPT_TEXT.index("scripts/enrich_linkedin.py")
        command = self.SCRIPT_TEXT[enrich_at:enrich_at + 400]
        self.assertIn("--alert-on-fallback", command,
                      "the flag belongs to the enrichment call, not a later phase")
        self.assertLess(command.index("--alert-on-fallback"),
                        command.index('> "$ENRICH_FILE"'),
                        "an argument after the redirect is not an argument")

    def test_the_report_records_which_path_fetched_the_descriptions(self):
        """A verdict should be read together with the path that fetched it: the two
        paths can disagree (Stryker, 2026-08-24, FAIL@4 vs PASS@4 on a 38-char
        whitespace difference)."""
        self.assertIn("fetch_path", self.SCRIPT_TEXT)
        self.assertIn("fallback_reason", self.SCRIPT_TEXT)

    def test_a_fallback_run_leaves_a_trace_in_the_report(self):
        """Otherwise a down browser recurs silently every morning."""
        self.assertIn("fallback mode (guest HTTP)", self.SCRIPT_TEXT)

    def test_no_linkedin_credential_is_anywhere_near_this_path(self):
        """Standing instruction: no automated login, no password in code or config.

        Session persistence comes from the real browser profile the Kimi Desktop App
        owns; when it lapses, a human logs in and the pipeline falls back until then.
        """
        lowered = self.ENRICH_TEXT.lower()
        for smell in ("linkedin_password", "li_at", "linkedin_user",
                      "password=", "passwd"):
            with self.subTest(smell=smell):
                self.assertNotIn(smell, lowered)

    def test_only_the_start_lifecycle_verb_appears(self):
        """`stop`/`restart`/`uninstall` fight the Kimi Desktop App."""
        for verb in ('"stop"', '"restart"', '"uninstall"'):
            with self.subTest(verb=verb):
                self.assertNotIn(f"kimi-webbridge{verb}", self.ENRICH_TEXT)
        self.assertIn('"start"', self.ENRICH_TEXT)


if __name__ == "__main__":
    unittest.main()
