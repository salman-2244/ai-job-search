#!/usr/bin/env python3
"""Terminal-only helper: create the LinkedIn storage state tier 3 consumes.

Why this exists and why it is terminal-only, in one sentence each:

  * **Why**: Playwright needs an authenticated session. Logging in through an
    automated browser is exactly what LinkedIn's anti-bot screens are built to
    stop, so the session is created by the human, here, once, and saved.
  * **Terminal-only**: a machine-driven login is what the CAPTCHA/challenge
    screens exist to detect. A human typing their own password into a real
    browser is the sanctioned path; this helper deliberately never accepts a
    session, cookie or token over Telegram, email or any other channel — an
    upload arriving out-of-band cannot be verified to belong to the operator.

What it writes: a Playwright `storage_state` JSON to a path outside the repo
(`~/.jobsearch-linkedin-state.json` by default, overridable with
`--path`), created owner-only (`0600`). Cookie and token values are never
printed; the only output is the resulting path and a summary of the session's
shape (name, count of cookies, expiry of the soonest-lapsing cookie) so the
operator can tell a live session from a lapsed one without seeing a value.

The login itself happens in a headed browser this helper opens and the human
drives. The helper waits for the operator to land on a signed-in LinkedIn page,
then saves the storage state and closes.

Usage::

    python3 scripts/linkedin_session.py            # interactive, headed browser
    python3 scripts/linkedin_session.py --path /somewhere/state.json
    python3 scripts/linkedin_session.py --show     # describe, don't re-login
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_STATE_PATH = Path.home() / ".jobsearch-linkedin-state.json"
SIGNED_IN_URL_MARKERS = ("linkedin.com/feed", "linkedin.com/jobs", "linkedin.com/mynetwork")
LOGIN_URL_MARKERS = ("login", "signin", "checkpoint", "authwall")


def describe(state_path: Path) -> int:
    """Print a shape summary of an existing storage state. Never a value."""
    if not state_path.is_file():
        print(f"no storage state at {state_path}")
        return 1
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"{state_path} is unreadable ({type(exc).__name__}: {exc})")
        return 1
    cookies = data.get("cookies") or []
    origins = data.get("origins") or []
    linkedin_cookies = [
        c for c in cookies
        if isinstance(c, dict) and "linkedin.com" in str(c.get("domain", ""))
    ]
    soonest = None
    for cookie in linkedin_cookies:
        expires = cookie.get("expires")
        if isinstance(expires, (int, float)) and expires > 0:
            soonest = expires if soonest is None else min(soonest, expires)
    mode = stat.S_IMODE(state_path.stat().st_mode)
    print(f"storage state: {state_path}")
    print(f"  permissions : {mode:04o} " + ("(owner-only)" if mode & 0o077 == 0 else "(⚠ group/world accessible — chmod 600)"))
    print(f"  cookies     : {len(cookies)} total, {len(linkedin_cookies)} for linkedin.com")
    print(f"  origins     : {len(origins)} with local storage")
    if soonest is not None:
        delta = soonest - time.time()
        if delta > 0:
            print(f"  soonest lap : in {delta / 86400:.1f} days")
        else:
            print("  soonest lap : already expired — re-provision the session")
    else:
        print("  soonest lap : unknown (session cookies do not expire on a clock)")
    print("values are never printed by this helper.")
    return 0


def provision(state_path: Path, headful_wait_seconds: float = 300.0) -> int:
    """Open a headed browser, wait for a signed-in page, save the state.

    Every failure path returns a nonzero exit and never prints a cookie, a
    token, or the page's content.
    """
    if state_path.exists():
        print(f"{state_path} already exists — delete it first if you mean to replace it.")
        return 2
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright is not installed. It is optional; install with\n"
            "  pip install -r requirements-stage3.txt\n"
            "  playwright install chromium\n"
            "then re-run this helper.",
            file=sys.stderr,
        )
        return 3

    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")

    print("A headed browser will open. Sign in to LinkedIn yourself.")
    print("This helper never types your credentials and never sees them.")
    print(f"Waiting up to {headful_wait_seconds:.0f}s for a signed-in page…")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")

        deadline = time.monotonic() + headful_wait_seconds
        signed_in = False
        while time.monotonic() < deadline:
            try:
                url = page.url or ""
            except Exception:  # noqa: BLE001 — a mid-navigation race must not crash the wait
                url = ""
            if any(marker in url for marker in SIGNED_IN_URL_MARKERS) and not any(
                marker in url for marker in LOGIN_URL_MARKERS
            ):
                signed_in = True
                break
            time.sleep(1.0)
        if not signed_in:
            browser.close()
            print(
                "timed out waiting for a signed-in LinkedIn page — no storage "
                "state was written.",
                file=sys.stderr,
            )
            return 4

        # Give the page a moment to finish writing session cookies, then save
        # to a temp file and move it into place so a crash mid-save can never
        # leave a half-written state file that looks valid.
        time.sleep(2.0)
        try:
            context.storage_state(path=str(tmp_path))
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, state_path)
        except Exception as exc:  # noqa: BLE001
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            print(f"could not save the storage state ({type(exc).__name__}: {exc})", file=sys.stderr)
            browser.close()
            return 5
        browser.close()

    print(f"saved: {state_path} (mode 600)")
    print("Tier 3 enrichment can now use it via LINKEDIN_PLAYWRIGHT_STORAGE_STATE.")
    return describe(state_path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Create the LinkedIn Playwright storage state (terminal-only).",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help=f"where to write the storage state (default {DEFAULT_STATE_PATH})",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="describe an existing storage state without opening a browser",
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=300.0,
        help="how long to wait for the sign-in to complete (default 300)",
    )
    args = parser.parse_args(argv)
    if args.show:
        return describe(args.path)
    return provision(args.path, headful_wait_seconds=args.wait_seconds)


if __name__ == "__main__":
    sys.exit(main())
