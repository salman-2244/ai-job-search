# Phase B — browser verification of the deep-ranked set

Status: **confirmed, not built.** The pre-build investigation the user asked for is
finished and the findings below are measured, not assumed. No Phase B code exists yet.

Goal: for all 25 deep-ranked rows — verified and unverified alike — open the real live
posting, read the language and experience requirements off the page as it exists now,
and publish a `browser_verified` verdict **alongside** the existing `hard_gates`
verdict so disagreements are visible. It does not replace the gate.

## Confirmed tool state (measured 2026-08-23)

### Kimi WebBridge — ready, authenticated

- Skill `~/.claude/skills/kimi-webbridge/SKILL.md` (v1.11.6); binary
  `~/.kimi-webbridge/bin/kimi-webbridge`; daemon HTTP on `127.0.0.1:10086`.
- Call shape: `POST /command` with `{"action":..., "args":{...}, "session":"..."}`.
  `session` is **top-level**, not inside `args`. One task = one session name = one tab
  group; never switch it mid-task.
- `kimi-webbridge status` reported `extension_connected: true`, extension `1.11.6`
  (matches the skill — no version mismatch), uptime ~21h.
- **LinkedIn is logged in.** Opening `linkedin.com/jobs/view/4443429666` rendered the
  authenticated page: notification chrome, "My Network", "Retry Premium", and the real
  job meta (`Siemens Healthineers`, `Project Manager - Supplier Management`,
  `Budapest, Budapest, Hungary · Reposted 2 days ago`). A LinkedIn session cookie is
  visible to JS.
- `authWall: false`, `captcha: false`, no sign-in prompt on that page. Nothing to pause
  on for LinkedIn as of this measurement.
- Shell quoting mangles inline JSON easily. Posting via `python3` + `urllib` (or a temp
  file + `--data-binary @file`) is reliable; that is what the probes used.

### agent-browser — ready, logged out

`/opt/homebrew/bin/agent-browser` v0.34.0, Chrome for Testing `152.0.7977.54` in
`~/.agent-browser/browsers/`. No sessions, which is correct for public postings.
`agent-browser skills get core --full` loads its usage guide.

## Constraints found the hard way

1. **`navigate` returns before the SPA hydrates.** The first probe saw 1569 body chars
   on a page that later had 2170 and the real content. Phase B needs a poll-until-present
   loop, not a fixed sleep.
2. **None of the obvious LinkedIn description selectors matched.** All of
   `.jobs-description__content`, `.show-more-less-html__markup`, `.description__text`,
   `[class*="jobs-box__html-content"]`, `#job-details`, `[class*="jobs-description"]`
   returned zero text >200 chars *while the description was demonstrably on the page*
   (found via `document.body.innerText`). The body is likely behind a "See more"
   collapse. **Find the real selector against the live DOM before writing the
   extractor** — this is the single biggest unknown left.
3. **Page furniture reads as job content.** A naive language scan of the Siemens page
   returned `Deutsch (German)`, `English (English)`, `Français (French)` — LinkedIn's
   **footer locale picker**, not requirements. The extractor must scope to the
   description element, never `document.body`. Same anchor/weak-signal discipline as
   `scripts/hard_gates.py`.
4. **Synthetic clicks are `isTrusted: false`.** Strictly-checking pages ignore
   `click`/`fill`. Per the skill, tell the user the page needs manual interaction rather
   than reaching for the `cdp` escape hatch.

## Scope: 25 rows span 13 hosts, not one

Measured from the 2026-08-23 rankset:

| Route | Rows | Hosts |
|---|---|---|
| Kimi WebBridge (auth needed) | 14 | `www.linkedin.com` 8, `hu.linkedin.com` 4, `se.linkedin.com` 2 |
| agent-browser (public) | 11 | whatjobs (ie/es/nl) 4, arbeitnow, techjob.dk, amazon.jobs, justjoin.it, careers.ocadogroup.com, echojobs.io, jobs.smartrecruiters.com |

This is per-host extraction work, not one extractor. Splitting by auth need also keeps
the real browser free while the 11 public pages are handled headless.

## Standing constraints from the user

- **Never bypass a CAPTCHA or anti-bot measure.** If one appears, pause the run and
  report it.
- **Never guess past a login wall.** If a page needs auth to show details, pause and
  report rather than skipping silently.
- Rate-limit sensibly; state the pacing chosen and why. Not 25 back-to-back.
- `browser_verified` is `PASS` / `FAIL` / `COULD_NOT_VERIFY`, shown **side by side**
  with the gate verdict in both the report and the Telegram list — never replacing it,
  so disagreement is visible.
- Sandbox on 3–5 rows before any full run.

## Test case: Siemens is now an AGREEMENT case

Siemens was Phase B's motivating *disagreement*: the gate said verified-pass while the
live posting required years of experience the profile lacks. **That is no longer true.**
After this session's fixes the gate returns:

```
overall: FAIL   failed: ['experience']
experience: FAIL — "5+ years stated as a hard requirement"
language:   UNKNOWN — body cut off at its length limit
evidence:   description_truncated, 6001 chars
```

So Siemens is now exactly the "both should agree" sanity check the user asked for:
browser verification must also come back FAIL on experience. **If `browser_verified`
says PASS on Siemens, the extractor is broken** — do not trust it on anything else until
that passes. Draw the disagreement candidates from rows where the gate says PASS on a
whole, untruncated body.

Minor, not blocking: the gate's experience *quote* for Siemens cites a degree clause
rather than the years phrase, so the verdict is right and the citation is weak.

## Uncommitted work this sits on top of

Three fixes are complete and **deliberately uncommitted** pending review together with
Phase B. `scripts/` and `tests/` are untracked, so `git status` shows them as `??` and
there is no committed baseline to diff against — reproduce pre-fix behaviour by
monkeypatching, not by `git show HEAD:`.

1. Retry-with-backoff around the Phase 2 `claude -p` ranker in `scripts/run_daily.sh`.
2. The truncation fix in `scripts/hard_gates.py` — a truncated body can no longer
   produce PASS (`_unverified()` downgrades PASS only, never lifting FAIL/UNKNOWN).
3. The language-detection fix — `_STOPWORDS` extended from 7 to all 14
   `BLOCKED_LANGUAGES`, plus an English-density test so "no list matched" is no longer
   reported as "reads as English".

Suite at handoff: **1029 passed, 1 failed.** The single failure is pre-existing and
unrelated — `python-telegram-bot` is not installed, so
`selector_listener.STALE_QUERY_ERRORS` is an empty tuple and
`tests/test_selector_resilience.py::TestAckIsBestEffort::test_ack_swallows_the_too_old_error`
raises `IndexError`. Run tests with system `python3 -m pytest`; `.venv/bin/python` has no
pytest.

## Housekeeping

One tab is open in the real browser under group **"Job posting verification"**. Per the
skill, tabs are closed only when the user asks (`close_session`).
