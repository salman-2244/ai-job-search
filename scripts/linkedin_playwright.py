#!/usr/bin/env python3
"""Tier-3 LinkedIn detail enrichment: an optional, bounded Playwright provider.

Tiers 1 and 2 (Kimi WebBridge, guest HTTP) already cover the normal path. This
module is the third tier, used only when both are unavailable or thin: an
authenticated browser session that renders the full job description.

Why a provider and not another extractor module. The two authenticated
constraints that make tier 3 hard are policy, not engineering:

  * **It never bypasses a wall.** A login page, 2FA prompt, CAPTCHA, checkpoint,
    consent screen or consent wall is an operator action, not an obstacle: the
    provider raises a typed error naming what it saw and stops. The session the
    storage state carries was created by a human in a terminal
    (`scripts/linkedin_session.py`); if it has lapsed, a human renews it.
  * **It spends the shared budget.** Every navigation is charged to the same
    `RequestLedger` the browser and guest paths use, before the navigation
    happens, so tier 3 cannot put Phase 1c over the cap the search planner
    already subtracted it from.

`playwright` is imported lazily inside the provider's first use, never at module
scope: this file must import cleanly (and its tests must run) on a machine with
no browser installed at all — the same reason `telegram_select.py` defers its
`telegram` import. The only supported authenticated mechanism is a storage-state
file created manually by `scripts/linkedin_session.py`; credentials are never
typed into LinkedIn by this provider.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

#: The canonical posting URL shape. `job_id` is validated before it is ever
#: interpolated — a job id that is not digits is not a LinkedIn id, and could
#: be an injection attempt reaching a URL.
JOB_URL = "https://www.linkedin.com/jobs/view/%s"

_JOB_ID_RE = re.compile(r"^\d{1,20}$")

#: substrings that mean "this is not the job page, it is a gate in front of it".
#: Checked against the final URL and the page text; a hit is a typed error, and
#: the provider never tries to pass the gate.
#:
#: `checkpoint` sits in the challenge list, not the login list, and the order
#: matters: `linkedin.com/checkpoint/challengesV2/...` is LinkedIn's
#: CAPTCHA/challenge router, so reading it as "the storage state lapsed" would
#: send the operator to re-provision a session that may be perfectly live.
#: The challenge check runs first and reads the URL as well as the text.
_LOGIN_MARKERS = ("login", "signin", "authwall")
_CHALLENGE_MARKERS = ("captcha", "challenge", "verify", "security check",
                      "unusual activity", "checkpoint")
_CONSENT_MARKERS = ("consent",)

#: The semantic-description selector. The three-tier design found LinkedIn
#: mounts the description body here after hydration; `document.body` is
#: poisoned by footer furniture (the locale picker reads as four languages).
_DESCRIPTION_SELECTOR = ".show-more-less-html__markup, .jobs-description__content, [class*='jobs-description']"


class PlaywrightProviderError(RuntimeError):
    """Base for tier-3 failures. Never carries credentials in its message."""


class PlaywrightNotAvailableError(PlaywrightProviderError):
    """`playwright` (or its browser) is not installed. Operator action: install."""


class PlaywrightBudgetExhaustedError(PlaywrightProviderError):
    """The shared RequestLedger has nothing left. Not retryable this run."""


class PlaywrightStorageStateError(PlaywrightProviderError):
    """The configured storage state is missing or not owner-only mode 0600."""


class PlaywrightLoginWallError(PlaywrightProviderError):
    """The session is gone and the page wants a login. A human must re-provision."""


class PlaywrightChallengeError(PlaywrightProviderError):
    """A CAPTCHA / checkpoint / unusual-activity screen. Never bypassed."""


class PlaywrightConsentError(PlaywrightProviderError):
    """A consent screen or wall appeared. A human must accept or decline it."""


class PlaywrightContentMissingError(PlaywrightProviderError):
    """The page loaded but no description element mounted within the timeout."""


class PlaywrightTimeoutError(PlaywrightProviderError):
    """Navigation or hydration exceeded the bounded timeout."""


@dataclass
class _FakeBrowser:
    """What the tests inject; also documents the surface the provider touches.

    Only `page_text`, `description_text`, `final_url` and `closed` are read by
    the provider — a real Playwright page exposes these through navigation and
    DOM queries, which `_run_page` adapts.
    """

    page_text: str = ""
    description_text: str = ""
    final_url: str = ""
    hydration_delay: float = 0.0
    closed: bool = False
    attempts: int = 0


def _looks_like_login(url: str, text: str) -> bool:
    low_url = (url or "").lower()
    low_text = (text or "").lower()
    if any(marker in low_url for marker in _LOGIN_MARKERS):
        return True
    return any(marker in low_text[:2000] for marker in _LOGIN_MARKERS)


def _looks_like_challenge(url: str, text: str) -> bool:
    low_text = (text or "").lower()
    if any(marker in low_text[:3000] for marker in _CHALLENGE_MARKERS):
        return True
    low_url = (url or "").lower()
    return any(marker in low_url for marker in _CHALLENGE_MARKERS)


def _looks_like_consent(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low[:2000] for marker in _CONSENT_MARKERS)


class PlaywrightDetailProvider:
    """Fetch one job's full description through an authenticated page.

    The only supported authentication input is a Playwright storage-state file
    that a human created with `scripts/linkedin_session.py`. Its exact mode must
    be 0600 before this provider passes it to a browser.

    `browser_factory` is the test seam: given `(storage_state, headless)` it
    returns an object with the `_FakeBrowser` surface. Production passes
    `_real_browser_factory`. No other part of this module knows whether the
    browser was real.
    """

    def __init__(
        self,
        storage_state: Optional[Path] = None,
        headless: bool = True,
        timeout: float = 30.0,
        browser_factory=None,
        clock=time.monotonic,
        sleep=time.sleep,
    ):
        self.storage_state = Path(storage_state) if storage_state else None
        self.headless = headless
        self.timeout = max(1.0, float(timeout))
        self._browser_factory = browser_factory or _real_browser_factory
        # Injectable clock/sleeper, the same pattern the Stage 3 orchestrator
        # uses: tests drive hydration polling with a fake clock and a no-op
        # sleep instead of waiting out a real 30-second deadline.
        self._clock = clock
        self._sleep = sleep

    # -- public API -----------------------------------------------------------

    def fetch(self, job_id: str, ledger) -> dict:
        """The full description for one posting, or a typed error.

        Charges `ledger` BEFORE every navigation attempt, so a failure still
        pays for what it spent and the cap is never exceeded. Raises
        `PlaywrightBudgetExhaustedError` (not `DetailError`) so the caller can
        distinguish "stop the tier" from "skip this job".
        """
        if not _JOB_ID_RE.fullmatch(str(job_id)):
            raise PlaywrightProviderError(f"not a LinkedIn job id: {job_id!r}")
        if ledger is not None and ledger.left() < 1:
            raise PlaywrightBudgetExhaustedError(
                f"LinkedIn request budget spent ({ledger.spent}/{ledger.limit}) — "
                "tier 3 refuses to exceed the cap"
            )
        if ledger is not None:
            ledger.spend(1)

        browser = self._open()
        try:
            return self._run_page(browser, str(job_id))
        finally:
            self._close(browser)

    # -- lifecycle ------------------------------------------------------------

    def _open(self):
        """Open only with a manually generated, owner-only storage state."""
        if self.storage_state is None:
            raise PlaywrightStorageStateError(
                "tier 3 requires a manually generated Playwright storage state; "
                "run scripts/linkedin_session.py in a terminal."
            )
        try:
            mode = self.storage_state.stat().st_mode & 0o777
        except OSError as exc:
            raise PlaywrightStorageStateError(
                "the configured Playwright storage state is missing or unreadable; "
                "run scripts/linkedin_session.py in a terminal."
            ) from exc
        if not self.storage_state.is_file():
            raise PlaywrightStorageStateError(
                "the configured Playwright storage state is not a regular file."
            )
        if mode != 0o600:
            raise PlaywrightStorageStateError(
                f"Playwright storage state must have exact mode 0600, got {mode:04o}; "
                f"fix with: chmod 600 {self.storage_state}"
            )
        try:
            return self._browser_factory(str(self.storage_state), self.headless)
        except PlaywrightProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PlaywrightNotAvailableError(
                f"could not start a browser ({type(exc).__name__}: {exc})"
            ) from exc

    @staticmethod
    def _close(browser) -> None:
        try:
            browser.close()
        except Exception:  # noqa: BLE001 — cleanup is best-effort by contract
            pass

    # -- the page ---------------------------------------------------------------

    def _run_page(self, browser, job_id: str) -> dict:
        url = JOB_URL % job_id
        deadline = self._clock() + self.timeout
        try:
            browser.navigate(url, timeout_ms=int(self.timeout * 1000))
        except PlaywrightProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PlaywrightTimeoutError(f"navigation to the posting failed: {exc}") from exc

        # Poll for hydration rather than sleeping a fixed amount: the SPA mounts
        # the description route late, and a fixed sleep is either too short or
        # wasteful. Bounded by the same deadline as everything else.
        text = ""
        description = ""
        while self._clock() < deadline:
            text, description = browser.read_page()
            if description.strip():
                break
            self._sleep(0.25)

        # Challenge before login, deliberately: LinkedIn's checkpoint router is
        # reached from a signed-in session too, and mislabeling it as a lapsed
        # login sends the operator re-provisioning a live session. See the
        # marker-list comment above.
        if _looks_like_challenge(browser.final_url or url, text):
            raise PlaywrightChallengeError(
                "LinkedIn served a CAPTCHA/checkpoint screen — paused, not "
                "bypassed. A human must complete it in a real browser."
            )
        if _looks_like_login(browser.final_url or url, text):
            raise PlaywrightLoginWallError(
                "the page is a login/authwall — the storage state has lapsed. "
                "Re-provision the session from a terminal; the provider never "
                "bypasses a login wall."
            )
        if _looks_like_consent(text):
            raise PlaywrightConsentError(
                "a consent screen appeared — a human must accept or decline it."
            )
        if not description.strip():
            raise PlaywrightContentMissingError(
                "no description element mounted within "
                f"{self.timeout:.0f}s (page had {len(text)} chars)"
            )
        return {"description": description.strip()}


def _real_browser_factory(storage_state: Optional[str], headless: bool):
    """The production factory. Imports playwright lazily; returns a `_RealBrowser`.

    The authentication-presence rule is enforced here as a second boundary:
    production never launches a browser without manually generated storage
    state. Automated email/password login is intentionally unsupported.
    """
    if storage_state is None:
        raise PlaywrightLoginWallError(
            "tier 3 has no Playwright storage state. Provision it manually with "
            "scripts/linkedin_session.py in a terminal; automated credential login "
            "is not supported."
        )
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PlaywrightNotAvailableError(
            "playwright is not installed — it is an optional tier-3 dependency; "
            "install with `pip install -r requirements-stage3.txt` and then "
            "`playwright install chromium`"
        ) from exc

    holder = _RealBrowser(sync_playwright, storage_state, headless)
    holder.start()
    return holder


class _RealBrowser:
    """The surface `_run_page` reads, implemented over sync Playwright."""

    def __init__(self, sync_playwright, storage_state, headless):
        self._sync_playwright = sync_playwright
        self._storage_state = storage_state
        self._headless = headless
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self.final_url = ""

    def start(self) -> None:
        self._pw = self._sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self._headless)
        kwargs = {}
        if self._storage_state:
            kwargs["storage_state"] = self._storage_state
        self._context = self._browser.new_context(**kwargs)
        self._page = self._context.new_page()

    def navigate(self, url: str, timeout_ms: int) -> None:
        self._page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        self.final_url = self._page.url
    def read_page(self) -> tuple[str, str]:
        text = self._page.evaluate("() => document.body ? document.body.innerText : ''")
        description = ""
        for selector in _DESCRIPTION_SELECTOR.split(","):
            selector = selector.strip()
            if not selector:
                continue
            try:
                el = self._page.query_selector(selector)
            except Exception:  # noqa: BLE001 — a bad selector must not kill the page
                continue
            if el is not None:
                candidate = (el.inner_text() or "").strip()
                if len(candidate) > len(description):
                    description = candidate
        return text or "", description

    def wait(self, ms: int) -> None:
        if ms > 0:
            time.sleep(ms / 1000.0)

    def close(self) -> None:
        for closer in (
            lambda: self._context.close(),
            lambda: self._browser.close(),
            lambda: self._pw.stop(),
        ):
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass
