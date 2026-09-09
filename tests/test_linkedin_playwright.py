"""Task 8 tests: the Playwright detail provider, driven by fake browsers.

Everything here runs offline with no Playwright installed — the provider's
`browser_factory` seam is exactly what makes that possible. Two classes of
check earn their place:

  1. **Policy under pressure.** A CAPTCHA page, a login wall and a consent
     screen each raise their typed error and close the browser — the provider
     never pushes through a gate, even when pushing would "work".
  2. **Budget honesty.** Every navigation is charged to the shared ledger
     before it happens, so a failing tier cannot spend more than it was given,
     and an exhausted budget raises before any browser is opened.

The fake browser documents the surface `_run_page` reads (page text, the
description element's text, the final URL) and counts its closes, so cleanup
is asserted rather than assumed.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "linkedin_playwright", REPO / "scripts" / "linkedin_playwright.py"
)
lp = importlib.util.module_from_spec(_spec)
sys.modules["linkedin_playwright"] = lp
_spec.loader.exec_module(lp)

_STATE_DIRECTORY = tempfile.TemporaryDirectory(prefix="linkedin-test-state-")
_TEST_STORAGE_STATE = Path(_STATE_DIRECTORY.name) / "state.json"
_TEST_STORAGE_STATE.write_text("{}", encoding="utf-8")
_TEST_STORAGE_STATE.chmod(0o600)


class FakeLedger:
    """The `RequestLedger` surface the provider touches, recorded for asserts."""

    def __init__(self, limit: int):
        self.limit = limit
        self.spent = 0

    def left(self) -> int:
        return max(0, self.limit - self.spent)

    def spend(self, n: int = 1) -> None:
        self.spent += max(0, int(n))


class FakeBrowser:
    """The factory surface `_run_page` consumes; counts its own closes."""

    def __init__(self, page_text="", description_text="", final_url="", hydration_rounds=0):
        self.page_text = page_text
        self.description_text = description_text
        self.final_url = final_url
        self.hydration_rounds = hydration_rounds
        self.closed = False
        self.navigated_to: list[str] = []

    def navigate(self, url, timeout_ms):
        self.navigated_to.append(url)

    def read_page(self):
        if self.hydration_rounds > 0:
            self.hydration_rounds -= 1
            return self.page_text, ""
        return self.page_text, self.description_text

    def wait(self, ms):
        pass

    def close(self):
        self.closed = True


class FakeClock:
    """A controllable clock so hydration polling never sleeps for real."""

    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_provider(
    browser: FakeBrowser,
    clock: FakeClock | None = None,
) -> tuple[lp.PlaywrightDetailProvider, FakeClock]:
    """Provider + shared clock; the clock advances on every provider sleep."""
    clock = clock or FakeClock()
    provider = lp.PlaywrightDetailProvider(
        storage_state=_TEST_STORAGE_STATE,
        browser_factory=lambda state, headless: browser,
        clock=clock,
        sleep=clock.advance,  # each poll advances the fake clock
    )
    return provider, clock


class ProviderSuccessTests(unittest.TestCase):
    def test_semantic_description_is_extracted_after_delayed_hydration(self):
        # The brief's own scenario: the SPA mounts the description late, so the
        # provider must poll rather than read once and give up. Each poll
        # advances the fake clock; the description arrives before the deadline.
        browser = FakeBrowser(
            page_text="Job page shell",
            description_text="A sufficiently long synthetic job description for testing.",
            hydration_rounds=3,
        )
        provider, _ = make_provider(browser)
        got = provider.fetch("123", FakeLedger(5))
        self.assertEqual(
            got["description"],
            "A sufficiently long synthetic job description for testing.",
        )
        self.assertTrue(browser.closed)  # closed by the finally, after success

    def test_navigation_targets_the_canonical_numeric_url(self):
        browser = FakeBrowser(description_text="real description text here")
        provider, _ = make_provider(browser)
        provider.fetch("4443429666", FakeLedger(5))
        self.assertEqual(browser.navigated_to, ["https://www.linkedin.com/jobs/view/4443429666"])

    def test_non_numeric_job_ids_are_rejected_before_any_navigation(self):
        browser = FakeBrowser(description_text="x")
        provider, _ = make_provider(browser)
        with self.assertRaises(lp.PlaywrightProviderError):
            provider.fetch("../admin", FakeLedger(5))
        with self.assertRaises(lp.PlaywrightProviderError):
            provider.fetch("4443429666; drop", FakeLedger(5))
        self.assertEqual(browser.navigated_to, [])
        self.assertFalse(browser.closed)  # never opened

    def test_missing_configured_storage_state_is_rejected_before_factory(self):
        opened = []
        provider, _ = make_provider(FakeBrowser(description_text="d"))
        provider.storage_state = Path("/nonexistent/state.json")
        provider._browser_factory = lambda state, headless: opened.append(state)

        with self.assertRaises(lp.PlaywrightStorageStateError):
            provider.fetch("123", FakeLedger(5))

        self.assertEqual(opened, [])

    def test_storage_state_must_have_exact_0600_mode(self):
        import os
        import tempfile

        opened = []
        with tempfile.NamedTemporaryFile(suffix=".json") as handle:
            os.chmod(handle.name, 0o640)
            provider, _ = make_provider(FakeBrowser(description_text="d"))
            provider.storage_state = Path(handle.name)
            provider._browser_factory = lambda state, headless: opened.append(state)

            with self.assertRaises(lp.PlaywrightStorageStateError) as caught:
                provider.fetch("123", FakeLedger(5))

        self.assertIn("0600", str(caught.exception))
        self.assertEqual(opened, [])

    def test_exact_0600_storage_state_is_passed_to_the_factory(self):
        import os
        import tempfile

        seen = {}

        def factory(state, headless):
            seen["state"] = state
            return FakeBrowser(description_text="d")

        with tempfile.NamedTemporaryFile(suffix=".json") as handle:
            os.chmod(handle.name, 0o600)
            provider, _ = make_provider(FakeBrowser(description_text="d"))
            provider.storage_state = Path(handle.name)
            provider._browser_factory = factory
            provider.fetch("123", FakeLedger(5))
        self.assertEqual(seen["state"], handle.name)


class PolicyGateTests(unittest.TestCase):
    def test_captcha_causes_fallback_error_and_closes_resources(self):
        browser = FakeBrowser(page_text="Please complete CAPTCHA to continue")
        provider, _ = make_provider(browser)
        with self.assertRaises(lp.PlaywrightChallengeError):
            provider.fetch("123", FakeLedger(5))
        self.assertTrue(browser.closed)

    def test_login_wall_raises_and_never_bypasses(self):
        browser = FakeBrowser(
            page_text="Sign in to LinkedIn",
            final_url="https://www.linkedin.com/authwall",
        )
        provider, _ = make_provider(browser)
        with self.assertRaises(lp.PlaywrightLoginWallError):
            provider.fetch("123", FakeLedger(5))
        self.assertTrue(browser.closed)

    def test_checkpoint_url_is_a_challenge(self):
        browser = FakeBrowser(
            description_text="some description",
            final_url="https://www.linkedin.com/checkpoint/challengesV2/x",
        )
        provider, _ = make_provider(browser)
        with self.assertRaises(lp.PlaywrightChallengeError):
            provider.fetch("123", FakeLedger(5))

    def test_consent_screen_raises_rather_than_clicking_through(self):
        browser = FakeBrowser(page_text="We value your privacy — consent to cookies")
        provider, _ = make_provider(browser)
        with self.assertRaises(lp.PlaywrightConsentError):
            provider.fetch("123", FakeLedger(5))
        self.assertTrue(browser.closed)

    def test_missing_description_after_timeout_is_typed_not_generic(self):
        browser = FakeBrowser(page_text="job shell, no body", description_text="")
        provider, _ = make_provider(browser)
        with self.assertRaises(lp.PlaywrightContentMissingError):
            provider.fetch("123", FakeLedger(5))
        self.assertTrue(browser.closed)


class BudgetTests(unittest.TestCase):
    def test_every_navigation_is_charged_before_it_happens(self):
        browser = FakeBrowser(description_text="d")
        ledger = FakeLedger(2)
        provider, _ = make_provider(browser)
        provider.fetch("123", ledger)
        self.assertEqual(ledger.spent, 1)

    def test_exhausted_budget_raises_before_opening_a_browser(self):
        browser = FakeBrowser(description_text="d")
        ledger = FakeLedger(0)
        provider, _ = make_provider(browser)
        with self.assertRaises(lp.PlaywrightBudgetExhaustedError):
            provider.fetch("123", ledger)
        self.assertEqual(ledger.spent, 0)
        self.assertFalse(browser.closed)  # never opened
        self.assertEqual(browser.navigated_to, [])

    def test_the_last_request_still_works(self):
        browser = FakeBrowser(description_text="d")
        ledger = FakeLedger(1)
        provider, _ = make_provider(browser)
        got = provider.fetch("123", ledger)
        self.assertIn("description", got)
        self.assertEqual(ledger.spent, 1)


class BrowserStartFailureTests(unittest.TestCase):
    def test_a_failing_factory_becomes_a_typed_availability_error(self):
        def factory(state, headless):
            raise RuntimeError("no browser binary")

        provider, _ = make_provider(FakeBrowser(description_text="d"))
        provider._browser_factory = factory
        with self.assertRaises(lp.PlaywrightNotAvailableError):
            provider.fetch("123", FakeLedger(5))

    def test_env_credentials_do_not_open_without_storage_state(self):
        import os

        os.environ["LINKEDIN_EMAIL"] = "synthetic@example.com"
        os.environ["LINKEDIN_PASSWORD"] = "synthetic-not-a-secret"
        try:
            with self.assertRaises(lp.PlaywrightLoginWallError) as caught:
                lp._real_browser_factory(None, headless=True)
        finally:
            os.environ.pop("LINKEDIN_EMAIL", None)
            os.environ.pop("LINKEDIN_PASSWORD", None)
        self.assertIn("linkedin_session.py", str(caught.exception))
        self.assertNotIn("LINKEDIN_EMAIL", str(caught.exception))

    def test_the_production_factory_refuses_to_start_without_storage_state(self):
        with self.assertRaises(lp.PlaywrightLoginWallError) as caught:
            lp._real_browser_factory(None, headless=True)
        self.assertIn("storage state", str(caught.exception).lower())


class ErrorHygieneTests(unittest.TestCase):
    """No credential value may ever reach an exception message."""

    def test_typed_errors_never_carry_credential_shapes(self):
        for exc_type in (
            lp.PlaywrightLoginWallError,
            lp.PlaywrightChallengeError,
            lp.PlaywrightConsentError,
            lp.PlaywrightContentMissingError,
            lp.PlaywrightBudgetExhaustedError,
            lp.PlaywrightNotAvailableError,
        ):
            message = str(exc_type("synthetic message"))
            self.assertNotIn("password", message.lower())


if __name__ == "__main__":
    unittest.main()
