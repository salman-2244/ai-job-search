# LinkedIn description extraction — forensic findings

Investigated 2026-08-23 against a live authenticated session (Kimi WebBridge v1.11.6).
Target: `https://www.linkedin.com/jobs/view/4443429666` (Siemens Healthineers).

**Verdict: the DOM is scrapable. No external extraction API is needed.** There were two
independent defects, and the previous session diagnosed neither.

## Why the six selectors "failed"

They didn't fail. **There was nothing to select.** The description had never been
delivered to the page.

Evidence, in the order it was gathered:

1. After hydration settled, `document.body.innerText` was **2,170 characters** — the page
   went straight from the job header (`Hybrid`, `Full-time`, `Apply`, `Save`) to the
   Premium upsell and then the footer. No description section, not even a collapsed one.
2. Nine description-typical phrases (`responsibilit`, `qualificat`, `you will`,
   `we offer`, `your tasks`, `minimum requirements`, `what you`,
   `years of experience`, `job description`) scored **zero hits** across all 112,653
   characters of `document.documentElement.innerHTML`.
3. No `<code>` JSON islands (LinkedIn's old `bpr-guid-*` hydration payloads): **0**.
   No `<script type="application/ld+json">`: **0**.
4. Network capture across a full reload showed **11 voyager API requests, all HTTP 200,
   and not one fetching the job posting.** Global nav, `/voyager/api/me`, notifications,
   premium feature flags, profile — the job-detail module never even *asked* for the
   description.

So the earlier conclusion that "the job data IS present" was true only of the
server-rendered header metadata (company, title, location, posted-date). The description
is client-mounted, and the mount never happened.

### Root cause 1 — the tab was in the background (primary)

```
visibility: "hidden"   hasFocus: false   readyState: "complete"
```

LinkedIn defers mounting the job-detail route while the tab is hidden. `readyState` is
`complete`, so every "wait for load" heuristic reports success against a page that is
permanently missing its main content. This is the trap: **the page looks finished.**

`navigate` with `newTab: true` opens the tab in the background, which is exactly the
condition that triggers this. A polling loop cannot fix it — the text never arrives,
so the loop just times out on a stable, wrong answer.

Fix — foreground the tab via CDP before reading:

```json
{"action":"cdp","args":{"method":"Page.bringToFront","params":{}}}
```

Measured effect on the same URL, nothing else changed:

| | `<main>` innerText | `JobPosting` markers in HTML |
|---|---|---|
| hidden | 2,001 | 0 |
| foregrounded | 10,581 | 15 |

### Root cause 2 — every CSS class is a rotating hash (secondary)

The current build ships hashed classes. `<main>`'s class attribute is:

```
_823a9014 e6f094c7 _2dcf1c6f _75450a38 _2805faad a2b15277 _09ea2d99 a1a56b0e _917ab9d4
```

All nine legacy markers — `jobs-description`, `jobs-description__content`,
`job-details`, `jobs-box__html-content`, `show-more-less-html`,
`jobs-unified-top-card`, `job-view-layout`, `jobs-details`, `decoratedJobPosting` —
occur **zero** times in the served HTML. The six original selectors were written against
a build LinkedIn no longer serves, so they would have matched nothing even with the tab
foregrounded. Two bugs, either one sufficient to produce the empty result.

## The actual path to the text

Traced by walking a `TreeWalker` over text nodes for the literal string
`Drive improvement`, then climbing `parentElement` 14 levels. Every ancestor class is
hashed, but one ancestor carries a **semantic, stable `id`**:

```
li  (112 chars)
└ ul._a5b65bc4…            1,122
  └ span._4754385e…        6,202
    └ p.baa0bb74…          6,202
      └ div × 4 (hashed)   6,217
        └ div#JobDetails_AboutTheJob_4443429666   ← 6,217 chars  ★ ANCHOR
```

`JobDetails_AboutTheJob_<jobId>` belongs to a whole semantic ID namespace on the page:

```
JobDetails_ManageJobBanner_<id>      JobDetails_AboutTheJob_<id>       ★
JobDetailsPeopleWhoCanHelpSlot_<id>  JobDetails_AboutTheCompany_<id>
JobDetails_JobAlertToggle_<id>       JobDetails_PremiumApplicantInsights_<id>
JobDetails_ResumeReview_<id>         JobDetails_CourseRecommendations_<id>
```

IDs survive the class-hash rotation, which makes this the right thing to key on.
Prefix-match rather than interpolating the job id, so a URL-vs-canonical id mismatch
(locale hosts, redirects) can't break it:

```js
document.querySelector('[id^="JobDetails_AboutTheJob"]').innerText
```

Extracted cleanly: 6,217 chars, opening `About the job / Join us in pioneering
breakthroughs in healthcare…` and closing on the recruitment-agency boilerplate.

## Hypotheses from the brief, adjudicated

| | Hypothesis | Verdict |
|---|---|---|
| a | Shadow DOM | **No.** One shadow host on the page, unrelated to the description. |
| b | Deferred JS hydration | **Yes, but not the way expected** — not slow hydration, *suppressed* hydration while hidden. Waiting longer never helps. |
| c | iframe | **No.** One full-viewport same-origin `linkedin.com/preload/?_bprMode=vanilla` shell: 937 KB of HTML carrying 220 chars of text. A decoy. |
| d | Dynamic hashed class names | **Yes.** Root cause 2. |
| e | Truncated behind "Show more" | **Yes, minor.** A `… more` button exists; clicking added 159 chars (10,750 → 10,909). Mostly a CSS line-clamp. Worth clicking, not the main issue. |
| f | XHR after scroll/delay | **No.** The job fetch never fires at all while hidden; scrolling and waiting don't provoke it. |

Also worth recording: synthetic `click()` **works** on the `… more` button despite
`isTrusted: false`. The skill's warning applies to strict-checking pages, not this one.

## Consequences for the pipeline

- The 10 of 14 LinkedIn rows in the 2026-08-23 rankset with `evidence_chars: 0` are
  consistent with this bug rather than with genuine fetch failures. Their gate verdicts
  are `UNKNOWN` for lack of text that was retrievable all along.
- Any future browser-based extractor **must assert on content, not on `readyState`**.
  `readyState: "complete"` was true throughout the failure.
- The footer locale picker (`Deutsch (German)`, `English (English)`, `Français (French)`)
  is inside `document.body` but outside the anchor. Scoping to
  `JobDetails_AboutTheJob_*` excludes it, which removes the false language signal noted
  in `docs/PHASE_B_BROWSER_VERIFICATION.md` for free.

## Not done, deliberately

No third-party extraction API was integrated. The brief allowed configuring one "to use
our authenticated session if needed" — that would mean transmitting Salman's live
LinkedIn session cookie to an external service, which publishes a credential to a party
that may cache it. The DOM route works, so the question is moot; if it ever isn't, this
tradeoff should be an explicit decision rather than an implementation detail.
