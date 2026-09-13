#!/usr/bin/env python3
"""Descriptive facts a posting states about itself, which no gate judges.

`hard_gates.py` answers eligibility questions and returns PASS/FAIL/UNKNOWN.
This module answers two descriptive ones the Telegram card wants but nothing in
the pipeline previously extracted: what the role pays, and where it is worked
from. Nothing here decides anything — it reports what the text says.

The contract is the same asymmetry the gates use, for the same reason: silence
is not evidence. A posting that never mentions money yields "unknown", never a
guessed band, and a posting that never mentions location policy yields
"unknown", never "onsite" by default. Every non-unknown answer carries the span
it was read from, so a wrong answer is auditable against the posting rather
than merely disbelieved.

Both extractors read the same body the gates read, so a job whose description
was never fetched reports "unknown" for both — which is true, and is the point.
"""

from __future__ import annotations

import re

UNKNOWN = "unknown"

# --------------------------------------------------------------------- salary

#: Currency tokens we will recognise. Symbol or ISO code; the code form must be
#: bounded so "HUFFMAN" is not a currency and "USD" inside a word is not either.
_CURRENCY = r"(?:[€£$₣]|(?:EUR|GBP|USD|CHF|HUF|PLN|SEK|NOK|DKK|CZK|RON)\b)"

#: A money quantity: 60000, 60,000, 60.000, 3.131, 1 200 000, 60k. The grouped
#: alternative comes first and allows a single leading digit, because European
#: notation writes 3131 as "3.131" — requiring two leading digits there made the
#: pattern skip the "3." and report "131€ per month" for a real Slovak posting
#: offering 3131, which understates the pay by a factor of ten. The ungrouped
#: alternative still demands two digits so a bare "5" in "5 years" is not money.
_AMOUNT = (r"\d{1,3}(?:[ .,]\d{3})+(?:[.,]\d{1,2})?|"
           r"\d{2,}(?:[.,]\d{1,2})?\s*[kK]?|"
           r"\d{1,3}\s*[kK]")

#: How often it is paid. Absent is fine — many postings state a band and no
#: period — but when present it is reported, because "€4,000" means very
#: different things per month and per year.
_PERIOD = (r"(?:per\s+(?:year|annum|anno|month|hour|day|week)|p\.?a\.?\b|"
           r"/\s*(?:yr|year|month|mo|hour|hr|day)|annually|monthly|hourly|"
           r"yearly|a\s+year|a\s+month|an\s+hour)")

#: Currency before the number ("€60,000 - €80,000") or after it
#: ("60 000 - 80 000 EUR"). Ranges and single figures both match; the range
#: alternative is first so "60k-80k EUR" is not truncated to its lower bound.
_SALARY_PATTERNS = tuple(re.compile(p, re.I) for p in (
    # currency-first range
    rf"{_CURRENCY}\s*(?:{_AMOUNT})\s*(?:-|–|—|to|up to)\s*{_CURRENCY}?\s*(?:{_AMOUNT})"
    rf"(?:\s*{_PERIOD})?",
    # amount-first range with a trailing currency
    rf"(?:{_AMOUNT})\s*(?:-|–|—|to)\s*(?:{_AMOUNT})\s*{_CURRENCY}(?:\s*{_PERIOD})?",
    # single currency-first figure, only when a period pins it down, so a stray
    # "$10" in a benefits sentence is not reported as the salary
    rf"{_CURRENCY}\s*(?:{_AMOUNT})\s*{_PERIOD}",
    # single amount-first figure with currency and period
    rf"(?:{_AMOUNT})\s*{_CURRENCY}\s*{_PERIOD}",
))

#: Phrases that mean "we are not telling you", which is a real answer and
#: distinct from the posting simply never raising the subject.
_SALARY_WITHHELD = tuple(re.compile(p, re.I) for p in (
    r"salary\s+(?:is\s+)?(?:not\s+disclosed|undisclosed|confidential)",
    r"compensation\s+(?:is\s+)?(?:not\s+disclosed|undisclosed|confidential)",
    r"salary\s+(?:will\s+be\s+)?discussed\s+(?:during|at|in)",
    r"competitive\s+(?:salary|compensation|package|remuneration)",
    r"salary\s+(?:commensurate|depending|based)\s+(?:with|on)",
))


def salary(text: str) -> dict:
    """What the posting says it pays.

    Returns ``value`` as the matched span verbatim, "not disclosed" when the
    posting explicitly declines to say, or "unknown" when it never raises the
    subject. The verbatim span is deliberate: normalising "60-80k EUR" into a
    structured band means choosing a reading, and a card that shows the
    posting's own words cannot be wrong about them.
    """
    body = " ".join(str(text or "").split())
    if not body:
        return {"value": UNKNOWN, "quote": None,
                "reason": "no posting text available to read"}
    for pattern in _SALARY_PATTERNS:
        match = pattern.search(body)
        if match:
            span = match.group(0).strip()
            return {"value": span, "quote": _context(body, match),
                    "reason": "stated in the posting text"}
    for pattern in _SALARY_WITHHELD:
        match = pattern.search(body)
        if match:
            return {"value": "not disclosed", "quote": _context(body, match),
                    "reason": "the posting declines to state a figure"}
    return {"value": UNKNOWN, "quote": None,
            "reason": "the posting states no salary"}


# ------------------------------------------------------------------ work mode

#: Ordered most-specific first. Hybrid is checked before remote and before
#: onsite because a hybrid posting almost always contains the words "remote"
#: and "office" too — matching remote first would mislabel every hybrid role.
_MODE_PATTERNS = (
    ("hybrid", tuple(re.compile(p, re.I) for p in (
        r"\bhybrid\s+(?:work|working|model|role|position|setup|arrangement)",
        r"\bhybrid\b(?!\s*(?:cloud|app|architecture))",
        r"\b\d\s*days?\s+(?:per|a)\s+week\s+(?:in|at)\s+the\s+office",
        r"\bpartially\s+remote",
    ))),
    ("remote", tuple(re.compile(p, re.I) for p in (
        r"\b(?:fully|100%|entirely|completely)\s*[- ]?\s*remote",
        r"\bremote[- ]first\b",
        r"\bwork\s+from\s+(?:home|anywhere)\b",
        r"\bremote\s+(?:work|working|position|role|job)\b",
        r"\bthis\s+is\s+a\s+remote\b",
        r"\(\s*remote\s*\)",
    ))),
    ("onsite", tuple(re.compile(p, re.I) for p in (
        r"\bon[- ]?site\b",
        r"\bin[- ]office\b",
        r"\bon\s+premises?\b",
        r"\boffice[- ]based\b",
        r"\bno\s+remote\s+work",
    ))),
)

#: A negation window ahead of a match. "remote work is not available" and "no
#: remote option" both state a policy, and reading them as "remote" would be
#: exactly backwards.
_MODE_NEGATORS = re.compile(
    r"\b(?:no|not|non|without|never|isn'?t|aren'?t|cannot|can'?t)\b", re.I)
_NEGATOR_WINDOW = 30


def work_mode(text: str, location: str = "") -> dict:
    """Remote, hybrid, onsite, or unknown.

    `location` is read too because several boards encode the policy there and
    nowhere else — LinkedIn's "Berlin, Germany (Remote)" is the whole statement
    for many postings.
    """
    body = " ".join(str(text or "").split())
    place = " ".join(str(location or "").split())
    haystack = f"{place}. {body}".strip(". ")
    if not haystack:
        return {"value": UNKNOWN, "quote": None,
                "reason": "no posting text or location available to read"}
    for name, patterns in _MODE_PATTERNS:
        for pattern in patterns:
            for match in pattern.finditer(haystack):
                if _negated(haystack, match.start()):
                    continue
                return {"value": name, "quote": _context(haystack, match),
                        "reason": f"the posting describes the role as {name}"}
    return {"value": UNKNOWN, "quote": None,
            "reason": "the posting states no work-location policy"}


def _negated(text: str, start: int) -> bool:
    """Is there a negator immediately before `start`?"""
    window = text[max(0, start - _NEGATOR_WINDOW):start]
    return bool(_MODE_NEGATORS.search(window))


# --------------------------------------------------------------------- shared

_QUOTE_CHARS = 140


def _context(text: str, match: re.Match) -> str:
    """A short readable span around a match, for auditing the answer."""
    pad = max((_QUOTE_CHARS - (match.end() - match.start())) // 2, 0)
    start = max(0, match.start() - pad)
    end = min(len(text), match.end() + pad)
    span = text[start:end].strip()
    if start > 0:
        span = "…" + span
    if end < len(text):
        span = span + "…"
    return span


def extract(job: dict) -> dict:
    """Both facts for one job record.

    Reads the same body the gates read — a fetched `description` when the job
    was enriched, otherwise the search card's `description_snippet` — so a job
    nobody fetched reports "unknown" for both, which is the truth about it.
    """
    body = job.get("description") or job.get("description_snippet") or ""
    title = job.get("title") or ""
    return {
        "salary": salary(f"{title}. {body}"),
        "work_mode": work_mode(body, job.get("location") or ""),
    }
