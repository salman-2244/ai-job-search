#!/usr/bin/env python3
"""Deterministic hard gates for Phase 1b, run before the model ever sees a job.

Why this module exists
----------------------
The language, experience and pure-technical rules lived only as *instructions in
the Phase 2 prompt*. That has three costs, and the 2026-08-19 run paid all three:

1. **A gate that runs after the cut cannot save a slot.** Six of the 25 jobs that
   reached the model were discarded by it on "Minimum 5-7 years", "5+ years",
   "At least 5 years", "10+ years", "5-8 years" and "At least 15 years". Every one
   of those consumed a deep-rank slot and a share of the enrichment budget to be
   thrown away — and the wording that discarded them was already in the text at
   pre-rank time.
2. **A gate that runs after the cut cannot direct enrichment.** Enrichment's real
   payoff that day was discards, not promotions: 6 of 9 enriched-and-selected
   alert jobs were removed on quoted wording. Spending a scarce LinkedIn request
   to learn that a job is ineligible is the worst possible use of it.
3. **Nothing was measurable.** With no Python implementation there was no way to
   ask "how many jobs does the experience gate remove, and which ones" without
   running a model over the whole corpus.

So the rules move here, as functions over text, and Phase 2 keeps its own copy as
the second line of defence. The two are deliberately redundant: this module runs on
whatever text exists at pre-rank time, which for an unenriched LinkedIn card is a
title and a 500-character snippet. It therefore returns `UNKNOWN` a great deal, and
`UNKNOWN` must never be treated as a failure.

A fourth gate was added on 2026-08-22 that did not come from the prompt: the
seniority filter. It reads the *title* rather than the body, so unlike the three
above it decides on evidence every job already has and never returns UNKNOWN.
It discards a title carrying any of **Senior, Sr., Sr, Snr, Lead, Leader,
Principal, Head, Director** or **Expert** as a standalone word — nine markers, not
just the senior family. Matching is bounded to word and separator boundaries, which
is what keeps "SRE Manager", "Sri Lanka Operations Analyst", "Leadership Development
Program", "Overhead Cost Analyst" and "Headcount Planning Analyst" alive; "Lead" as
a word covers every compound (Team Lead, Workstream Lead, Country Lead, "(Lead)
Project Manager") without enumerating them, at the price of one exception list for
the cases where "lead" is the sales/supply-chain noun — see `LEAD_NOUN_FOLLOWERS`.

The three verdicts, and why UNKNOWN is a first-class one
--------------------------------------------------------
    PASS     the text states a condition this profile meets
    FAIL     the text states a condition it does not meet, unambiguously
    UNKNOWN  the text does not say

DHL is the case that makes the distinction load-bearing. Its "Fluent English and
Hungarian" line was present in the 2026-08-18 fetch and absent from the 2026-08-19
corpus, where the description was empty. A gate that reads no-evidence as PASS
passed it and it was drafted; a gate that reads no-evidence as FAIL would drop most
of a LinkedIn corpus unread. `UNKNOWN` says "ask for the text", which is exactly
what the enrichment allocator wants to hear.

Conservatism, stated plainly
----------------------------
Every FAIL here removes a job with no model in the loop, so each one is anchored to
a quotable phrase and the ambiguity markers are honoured *before* the numbers are
read. Where this module is unsure it says UNKNOWN and lets the model decide. The
asymmetry is deliberate: a wrongly-kept job costs a slot, a wrongly-dropped job is
invisible.

This module is pure. It reads no files and writes nothing.
"""

import re
import unicodedata
from functools import lru_cache

# --------------------------------------------------------------------- verdicts

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"


def _norm(text) -> str:
    """Lowercase, strip accents, collapse punctuation the patterns don't need.

    Accents go because postings mix "Küche"/"Kuche" and, more importantly, because
    the language detector counts stopwords: "è" and "e" must not be different
    tokens. `+`, `-`, `/` and `&` survive — "5+ years", "3-5 years", "C1/C2" and
    "S&OP" all carry meaning in the punctuation itself.

    Every other dash is folded onto the ASCII hyphen *first*, because the strip below
    would otherwise turn it into a space and the meaning would be gone before any
    pattern saw it. That is not hypothetical: it is why en-dash year ranges were
    misread for the whole life of the experience gate. "3–5 years" arrived at `_YEARS`
    as "3 5 years", where the range branch cannot match, so the pattern fell through
    to the bare trailing figure and graded the posting on "5 years" alone. Adding the
    en-dash to `_YEARS`' separator class alone does not fix this — by then the
    character no longer exists. See the ceiling policy note on `_YEARS`.
    """
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    # U+2010..U+2015 hyphen through horizontal bar (en-dash U+2013 and em-dash U+2014
    # among them), U+2212 minus sign, U+FF0D fullwidth hyphen-minus.
    text = re.sub(r"[‐-―−－]", "-", text)
    text = re.sub(r"[^a-z0-9+\-/&]+", " ", text.lower())
    return " " + re.sub(r"\s+", " ", text).strip() + " "


# ------------------------------------------------------------- language gate

# Languages the profile cannot work in professionally. Hungarian sits at A2, which
# is not a working level, so a role that *requires* it is a discard rather than a
# note — that is the DHL correction. Names, not countries: a Munich-based role
# advertised in English requiring only English passes.
BLOCKED_LANGUAGES = {
    "german": ("german", "deutsch"),
    "hungarian": ("hungarian", "magyar"),
    "spanish": ("spanish", "espanol", "castellano"),
    "french": ("french", "francais"),
    "italian": ("italian", "italiano"),
    "dutch": ("dutch", "nederlands"),
    "polish": ("polish", "polski"),
    "czech": ("czech", "cestina"),
    "portuguese": ("portuguese", "portugues"),
    "swedish": ("swedish", "svenska"),
    "danish": ("danish", "dansk"),
    "norwegian": ("norwegian", "norsk"),
    "finnish": ("finnish", "suomi"),
    "romanian": ("romanian", "romana"),
}

# Wording that makes a language a hard job condition.
#
# The intensifier block on the second row is the 2026-08-24 repair. BRP-Rotax's
# "Very good German and English skills" matched the `german` alias, then missed
# *both* marker lists — no "very good" in either — so it fell through to the tail
# and was stamped PASS with "no language condition stated", about a posting that
# states one in plain words. Probing found the same hole under "excellent",
# "strong", "working knowledge", "good command of" and a bare "needed". Real
# postings reach for an adjective far more often than for the word "required".
REQUIRED_MARKERS = (
    "required", "requirement", "require", "mandatory", "must have", "must speak",
    "very good", "excellent", "strong", "good command", "command of", "advanced",
    "working knowledge", "needed", "solid", "good knowledge", "sound knowledge",
    "fluent", "fluency", "native", "proficient", "proficiency", "business level",
    "business fluent", "c1", "c2", "b2", "verhandlungssicher", "essential",
    "is a must", "a must", "necessary", "obligatory", "compulsory",
)

# Native-speaker demands, scanned *without* reference to any language name.
#
# "Must be a native speaker." names no language, so the `for lang, aliases in
# BLOCKED_LANGUAGES.items()` loop never enters its body and no marker list can ever
# reach it. It is still an absolute bar: the profile's native languages are Urdu and
# Punjabi, and no European employer writing this sentence means those. Same for
# "mother tongue" and "native level" used without naming the tongue — in context
# they always mean the local one.
_NATIVE_DEMAND = tuple(re.compile(p) for p in (
    r"\bnative\s+(?:speaker|level|proficiency|fluency|command|tongue)\b",
    r"\bmother\s+tongue\b",
    r"\bmothertongue\b",
    r"\bnative\s+or\s+bilingual\b",
    r"\bmust\s+be\s+(?:a\s+)?native\b",
))

# Wording that makes it optional. Checked FIRST and it wins: "Hungarian knowledge
# is an advantage" and "Hungarian (preferred)" are both passes, and a sentence can
# easily contain both an optional marker and the word "fluent".
OPTIONAL_MARKERS = (
    "advantage", "advantageous", "plus", "nice to have", "nice-to-have",
    "preferred", "preferable", "preferably", "desirable", "desired", "beneficial",
    "bonus", "asset", "welcome", "appreciated", "ideally", "optional",
    "not required", "no need", "not necessary", "not mandatory", "would be great",
)


# A bare conjunction of English with a blocked language, with no marker either way.
#
# "English and German." and "English, German." carry no marker at all, required or
# optional, so both marker lists missed them and the tail stamped PASS. A
# requirements list that names two languages side by side is asking for both — the
# instruction here is to err cautious.
#
# Anchored on *adjacency*, not co-occurrence, and that distinction is what keeps the
# rule usable: "We are an English-speaking company with German and French offices"
# contains both names in one sentence but never puts them next to each other, so it
# does not match. `_norm` deletes commas, so "English, German" arrives as
# "english german" — bare adjacency is the normalised form of the comma list, which
# is why the joiner group is optional.
#
# It is still the loosest rule in this gate, and it is deliberately checked LAST,
# after both marker lists. "English and preferably German" is broken up by the
# adverb so it does not match the adjacency either — but even if it did, the
# optional branch has already claimed it. Belt and braces on the Leica acquittal.
_CONJUNCTION_JOINERS = r"(?:\s+(?:and|&|/|or|plus|with))?\s+"


@lru_cache(maxsize=None)
def _conjunction_pattern(lang: str) -> re.Pattern:
    alias = "|".join(re.escape(a) for a in BLOCKED_LANGUAGES[lang])
    return re.compile(
        rf"\benglish{_CONJUNCTION_JOINERS}(?:{alias})\b"
        rf"|\b(?:{alias}){_CONJUNCTION_JOINERS}english\b")

# Sentence splitter. A language condition and its qualifier live in the same
# sentence or the same bullet; scanning the whole posting at once would let one
# bullet's "advantage" excuse another bullet's "fluent German required".
_SENTENCE = re.compile(r"[.;!?\n\r•|]+")


def _sentences(text: str) -> list:
    return [s for s in _SENTENCE.split(str(text or "")) if s.strip()]


# Every FAIL here is anchored to a quotable phrase, so the quote has to contain the
# phrase. Splitting on the enders above is not enough to guarantee that: LinkedIn's
# `detail` endpoint returns bodies whose bullets are joined with no punctuation at
# all, so a 4,800-character posting can arrive as one single span, and taking its
# first 300 characters then quotes an opening paragraph while the verdict was
# reached on wording 2,000 characters later.
#
# Two real cases, both from the 2026-08-21 live-enrichment run: Wizz Air's "4-6+
# years of overall experience" sat at offset ~2,700 of a 4,473-char span, and
# Morgan Stanley's "5+ years of experience in Finance, Business Intelligence,
# Reporting, or Data Analytics" at ~2,400 of 4,828. Both were correctly discarded
# and both quoted text that says nothing about years — the verdict was right and
# unauditable at the same time, which is the worse failure of the two.
#
# The window is centred on the trigger rather than the split made finer. Splitting
# harder would change *classification*: the language gate reads its optional and
# required markers across the whole span deliberately, so one bullet's "advantage"
# cannot excuse another bullet's "fluent German required". Only the quote moves.
_QUOTE_CHARS = 300
_ELLIPSIS = "…"


def _excerpt(span: str, trigger: re.Pattern, ordinal: int = 0) -> str:
    """A <=300-char window of `span` centred on its `ordinal`th `trigger` match.

    `ordinal` is the match's index within the span, counted the same way the caller
    counted it. Classification runs on the `_norm`-ed copy of the span while the
    quote is cut from the raw text, so the two are matched up by position in the
    sequence rather than by character offset.

    Falls back to the head of the span when the trigger cannot be located that many
    times in the raw text, which normalisation could in principle cause. That is the
    old behaviour: thinner evidence, never wrong evidence.
    """
    span = " ".join(str(span or "").split())
    if len(span) <= _QUOTE_CHARS:
        return span
    found = list(trigger.finditer(span))
    if ordinal >= len(found):
        return span[:_QUOTE_CHARS]
    at = found[ordinal]
    room = _QUOTE_CHARS - 2 * len(_ELLIPSIS)
    start = max(0, (at.start() + at.end()) // 2 - room // 2)
    start = min(start, max(0, len(span) - room))
    body = span[start:start + room]
    return (_ELLIPSIS if start else "") + body + (
        _ELLIPSIS if start + room < len(span) else "")


@lru_cache(maxsize=None)
def _alias_pattern(lang: str) -> re.Pattern:
    """Where a language name sits in the raw span, for `_excerpt` to centre on."""
    return re.compile("|".join(re.escape(a) for a in BLOCKED_LANGUAGES[lang]), re.I)


# Why a PASS earned by silence was not trusted. Both caveats say "the text we had
# stops before the posting does", but they are different facts and the report names
# which one applied, so keep them distinct.
SNIPPET_CAVEAT = "the only text available is a truncated card snippet"
CUT_BODY_CAVEAT = ("the fetched body was cut off at "
                   "its length limit before the posting ended")


def _unverified(block: dict, caveat: str = SNIPPET_CAVEAT) -> dict:
    """Downgrade a PASS that was earned by silence in text that stops early.

    Two shapes of incomplete evidence reach this. A card snippet is the first ~500
    characters of a posting. A *truncated body* is one this pipeline fetched and
    then cut at `enrich_linkedin.max_description_chars(job)`; it is far longer, but
    it ends mid-sentence with an ellipsis and a posting's requirements section is
    routinely below the cut. Neither is evidence that the posting states no
    blocker — only that the part we read did not. So either can carry a job to
    FAIL (it *said* something disqualifying) but never to PASS (it said nothing).

    FAIL is left alone, which is the whole asymmetry: "Fluent German is required."
    in a snippet is a real requirement and stays a real rejection. UNKNOWN is left
    alone too, so a job with no text keeps its own more accurate reason.
    """
    if block["verdict"] != PASS:
        return block
    return {**block, "verdict": UNKNOWN,
            "reason": f"{block['reason']}, but {caveat} — unverified, not a pass"}


# ------------------------------------------- sponsorship & citizenship gate
#
# LAW 1. The highest-priority gate, and the only one that reads work authorisation.
# It exists because on 2026-08-24 two postings reached Telegram whose text stated an
# absolute bar in plain English, three lines from the top in one case, and this
# module had no code path capable of noticing:
#
#   Baker Hughes  "...and hold an EU passport"
#   MCS Group     "Please not there is no sponsorship available for this role"
#
# The profile is a Pakistani passport holder on a Hungarian residence permit. A role
# demanding an EU passport, EU citizenship, permanent residency, or pre-existing
# unrestricted work rights is not a stretch application — it is arithmetically
# impossible, and spending a rank slot on it is pure waste. See docs/GATE_TRIBUNAL.md.
#
# Two families of pattern, and they treat negation in *opposite* ways, which is why
# they are separate tuples rather than one list:
#
#   _SPONSORSHIP_DENIED — negation is the disqualifier and is baked into the pattern.
#   "no sponsorship" IS the bar. Nothing pardons these.
#
#   _AUTHORISATION_WALL — the requirement is the disqualifier, so a negator sitting
#   just ahead of it *pardons* it. "No EU passport required" is the opposite of
#   "EU passport required", and a naive substring match on the latter reads the
#   former as a rejection.
#
# Deliberately anchored on the disqualifying core, never on the polite preamble
# wrapped around it. MCS Group typed "Please not" for "Please note"; a pattern
# keyed on "please note there is no sponsorship" would have missed the real posting
# that motivated this gate. Corporate euphemism is the norm here, so the patterns
# are paranoid by instruction: match the claim, ignore the framing.
_SPONSORSHIP_DENIED = tuple(re.compile(p) for p in (
    # "no sponsorship", "no visa sponsorship", "no work visa sponsorship"
    r"\bno\s+(?:visa\s+|work\s+|immigration\s+)*sponsorship\b",
    r"\bno\s+(?:visa|work\s+permit|immigration)\s+(?:support|assistance)\b",
    # "we do not sponsor", "does not sponsor", "will not sponsor", "won't sponsor"
    r"\b(?:do|does|will|can|could|shall)\s+not\s+sponsor\b",
    r"\bwon\s+t\s+sponsor\b",          # `_norm` strips the apostrophe
    r"\bcan\s*not\s+sponsor\b",
    r"\bunable\s+to\s+(?:sponsor|offer\s+sponsorship|provide\s+sponsorship)\b",
    r"\bnot\s+(?:able|in\s+a\s+position)\s+to\s+(?:sponsor|offer|provide)\b",
    # "sponsorship is not provided/available/offered/possible"
    r"\bsponsorship\s+(?:is\s+|are\s+)?not\s+"
    r"(?:provided|available|offered|possible|supported)\b",
    r"\bsponsorship\s+(?:cannot|can\s*not)\s+be\s+(?:offered|provided)\b",
    # "we do not offer visa sponsorship", "does not provide sponsorship"
    r"\bnot\s+(?:offer|provide|support)\w*\s+(?:any\s+)?"
    r"(?:visa\s+|work\s+|immigration\s+)*sponsorship\b",
    # "candidates must be eligible to work ... without sponsorship"
    r"\bwithout\s+(?:visa\s+|the\s+need\s+for\s+)*sponsorship\b",
    # "this role is not eligible for immigration sponsorship" — the euphemism that
    # refuses nothing out loud. The role is the grammatical subject, so no verb of
    # refusal ever appears and every "we do not sponsor" pattern above misses it.
    r"\bnot\s+eligible\s+for\s+(?:\w+\s+){0,3}sponsorship\b",
))

# Nationality adjectives, for the bars that are stated as a demonym rather than as
# a country name — "an Irish passport", "only German applicants". Hoisted into one
# alternation so a new nationality is added in one place rather than five.
#
# "local" is deliberately absent. "Only local candidates" is a commuting
# constraint, not a citizenship one; conflating them would reject Budapest-eligible
# postings that merely name a preferred office.
_NATIONALITY = (r"(?:eu|eea|european|uk|british|us|usa|american|irish|swiss|"
                r"german|austrian|dutch|belgian|french|spanish|italian|"
                r"hungarian|polish|czech|portuguese|finnish|swedish|danish|"
                r"norwegian|canadian|australian)")

# The requirement family. A negator in the short run-up pardons these — see the
# _NEGATORS note below.
_AUTHORISATION_WALL = tuple(re.compile(p) for p in (
    # Passports. The bare "hold an EU passport" form is required, not optional:
    # Baker Hughes stated it with no "must" anywhere in the sentence.
    rf"\b(?:hold|holding|possess|have)\s+(?:an?\s+|a\s+valid\s+)?"
    rf"{_NATIONALITY}\s+passport\b",
    rf"\b{_NATIONALITY}\s+passport\s+(?:is\s+)?"
    rf"(?:required|mandatory|essential|a\s+must)\b",
    # Citizenship and nationality. Plural matters: real postings address a pool of
    # applicants ("Applicants must be EU citizens"), not one candidate.
    rf"\bmust\s+be\s+(?:an?\s+)?{_NATIONALITY}\s+(?:citizens?|nationals?)\b",
    rf"\b{_NATIONALITY}\s+citizenship\s+(?:is\s+)?"
    rf"(?:required|mandatory|essential)\b",
    r"\bmust\s+be\s+a\s+(?:citizen|national)\s+of\b",
    r"\bonly\s+(?:\w+\s+)?(?:citizens|nationals|passport\s+holders)\b",
    # "Only Irish applicants will be considered." Anchored on the nationality, not
    # on "only ... applicants" — "only shortlisted applicants will be contacted" is
    # boilerplate on half the corpus and must not be read as a citizenship bar.
    rf"\bonly\s+{_NATIONALITY}\s+(?:applicants|candidates|residents)\b",
    # Pre-existing, unrestricted work rights. These are the polite euphemisms:
    # nothing is refused, the bar is simply assumed.
    r"\b(?:must|should|need\s+to)\s+(?:already\s+)?(?:have|hold|possess)\s+"
    r"(?:the\s+|an?\s+|a\s+valid\s+|an?\s+existing\s+|unrestricted\s+)*"
    r"(?:legal\s+)?right\s+to\s+work\b",
    r"\b(?:applicants|candidates|you)\s+must\s+(?:already\s+)?have\s+"
    r"(?:the\s+)?right\s+to\s+work\b",
    r"\bright\s+to\s+work\s+in\s+[\w\s]{1,20}?\s+(?:is\s+)?"
    r"(?:required|essential|mandatory)\b",
    r"\bmust\s+be\s+eligible\s+to\s+work\s+(?:in\s+[\w\s]{1,20}?\s+)?"
    r"without\s+(?:restriction|sponsorship|limitation)\b",
    r"\bmust\s+be\s+(?:legally\s+)?authoris\w*\s+to\s+work\b",
    r"\bmust\s+be\s+(?:legally\s+)?authoriz\w*\s+to\s+work\b",
    r"\bmust\s+(?:have|hold)\s+(?:a\s+|an?\s+|a\s+valid\s+)?"
    r"work\s+(?:permit|visa|authoris\w+|authoriz\w+)\b",
    # "A valid work permit is required." Same bare shape as Baker Hughes: the demand
    # is carried by "is required", with no modal verb anywhere near the candidate.
    r"\b(?:a\s+|an\s+)?(?:valid\s+|current\s+)?"
    r"work\s+(?:permit|visa|authoris\w+|authoriz\w+)\s+(?:is\s+)?"
    r"(?:required|mandatory|essential|a\s+must)\b",
    r"\bmust\s+have\s+unrestricted\s+work\s+authoris\w*\b",
    r"\bmust\s+have\s+unrestricted\s+work\s+authoriz\w*\b",
    r"\bmust\s+have\s+permanent\s+resid\w+\b",
))

# Words that flip an _AUTHORISATION_WALL match from a bar into a waiver. Checked in
# the run-up only: "no EU passport required" and "we do not require an EU passport"
# are reassurances, and reading them as rejections would discard exactly the
# sponsorship-friendly postings this profile most wants to see.
_NEGATORS = ("no ", "not ", "without ", "dont ", "don t ", "doesn t ", "need not ",
             "regardless of ", "irrespective of ")
_NEGATOR_LOOKBEHIND = 40

# Wording that says sponsorship IS on offer. Consulted only to pardon an
# _AUTHORISATION_WALL hit elsewhere in the same posting — never to pardon an
# explicit denial, because "no sponsorship available" contains the substring
# "sponsorship available" and a naive offer check would acquit the very posting
# that motivated this gate.
_SPONSORSHIP_OFFERED = tuple(re.compile(p) for p in (
    r"\b(?:we|company)\s+(?:can|will|do|may)\s+sponsor\b",
    r"\b(?:visa\s+)?sponsorship\s+(?:is\s+)?(?:available|provided|offered)\b",
    r"\bwe\s+(?:offer|provide)\s+(?:visa\s+)?sponsorship\b",
    r"\b(?:happy|open|willing)\s+to\s+sponsor\b",
    # "happy to provide visa sponsorship" — the offer stated as a noun. The verb
    # forms above miss it, and missing an offer is not neutral: it lets an ordinary
    # "a work permit is required before your start date" line convict a posting
    # that is explicitly sponsorship-friendly.
    r"\b(?:happy|open|willing|able|prepared|glad)\s+to\s+"
    r"(?:offer|provide|support|arrange)\s+"
    r"(?:visa\s+|work\s+|immigration\s+)*sponsorship\b",
    r"\bwe\s+(?:will|can|do)\s+(?:offer|provide|support|arrange)\s+"
    r"(?:visa\s+|work\s+|immigration\s+)*sponsorship\b",
    r"\bwill\s+consider\s+sponsorship\b",
    r"\brelocation\s+and\s+visa\s+support\b",
    r"\bvisa\s+sponsorship\s+for\s+the\s+right\s+candidate\b",
    r"\binternational\s+(?:applicants|candidates)\s+(?:are\s+)?welcome\b",
    # "we will help you obtain a work permit" — an offer stated as assistance rather
    # than as the word "sponsorship". Without this, the employer's own promise to
    # help sits in the same sentence as the papers clause and the clause convicts.
    r"\b(?:help|assist|support)\s+(?:you\s+|candidates\s+|with\s+)*(?:to\s+)?"
    r"(?:obtain|obtaining|apply|applying|secure|securing|arrange|arranging|"
    r"get|getting)\s+(?:a\s+|the\s+|your\s+)*"
    r"(?:work\s+permit|work\s+visa|visa|residence\s+permit)\b",
))


def sponsorship_verdict(title, description="", *, full_text=True,
                        caveat=SNIPPET_CAVEAT) -> dict:
    """Does the posting bar a candidate who needs sponsorship?

    Capped at UNKNOWN on incomplete evidence for the same reason as language and
    experience: a snippet that does not mention sponsorship is not evidence that
    the posting allows it. See `_unverified`.
    """
    block = _sponsorship_verdict(title, description)
    return block if full_text else _unverified(block, caveat)


def _sponsorship_verdict(title, description="") -> dict:
    """Is this role open to a candidate who needs a work permit sponsored?

    Sentence-scoped like the language gate, and for the same reason: a bar and the
    thing that waives it live in one sentence, and a posting-wide scan would let one
    bullet's "we sponsor visas" excuse another bullet's "must hold an EU passport".
    The offer check is the one deliberate exception — an employer who states
    sponsorship anywhere is offering it for the role, not for a paragraph.
    """
    text = f"{title}\n{description}"
    normed = [(s, _norm(s)) for s in _sentences(text)]

    # An offer only counts from a sentence that is not itself a denial. Measured on
    # the real MCS Group posting: "no sponsorship available" contains the substring
    # "sponsorship available", so a posting-wide offer scan recorded
    # sponsorship_offered=True on the very job being rejected for having none. The
    # verdict was still right (denials are checked first and nothing pardons them),
    # but the evidence was a lie, and this block gets persisted to
    # job["prerank"]["gates"] for humans to audit.
    offered = any(any(p.search(n) for p in _SPONSORSHIP_OFFERED)
                  and not any(d.search(n) for d in _SPONSORSHIP_DENIED)
                  for _, n in normed)

    for sentence, n in normed:
        for pattern in _SPONSORSHIP_DENIED:
            if pattern.search(n):
                return {"verdict": FAIL,
                        "reason": "posting states sponsorship is not available",
                        "quote": _excerpt(sentence, pattern),
                        "bar": "sponsorship_denied",
                        "sponsorship_offered": offered}

    if not offered:
        for sentence, n in normed:
            for pattern in _AUTHORISATION_WALL:
                m = pattern.search(n)
                if not m:
                    continue
                run_up = n[max(0, m.start() - _NEGATOR_LOOKBEHIND): m.start()]
                if any(neg in run_up for neg in _NEGATORS):
                    continue
                return {"verdict": FAIL,
                        "reason": "posting requires citizenship, a passport or "
                                  "pre-existing work rights this profile lacks",
                        "quote": _excerpt(sentence, pattern),
                        "bar": "authorisation_wall",
                        "sponsorship_offered": offered}

    if description:
        return {"verdict": PASS,
                "reason": ("posting states sponsorship is available" if offered
                           else "no sponsorship or citizenship bar stated"),
                "quote": None, "bar": None, "sponsorship_offered": offered}
    return {"verdict": UNKNOWN,
            "reason": "no description text to read a sponsorship condition from",
            "quote": None, "bar": None, "sponsorship_offered": offered}


def language_verdict(title, description="", posting_language=None,
                     *, full_text=True, caveat=SNIPPET_CAVEAT) -> dict:
    """Is this role workable in English (plus Urdu/Punjabi, plus A2 Hungarian)?

    `full_text=False` says `description` is incomplete — a truncated card snippet,
    or a fetched body that was cut at its length limit — which caps the best
    outcome at UNKNOWN. `caveat` names which one, for the reason string. See
    `_unverified`.
    """
    block = _language_verdict(title, description, posting_language)
    return block if full_text else _unverified(block, caveat)


def _language_verdict(title, description="", posting_language=None) -> dict:
    """Is this role workable in English (plus Urdu/Punjabi, plus A2 Hungarian)?

    Reads the requirement as stated for **the role**, never the language the ad
    happens to be written in — a job is not rejected because the company operates
    in Germany. Posting-language detection is reported separately, as a risk flag,
    and only escalates to FAIL when the body is long enough for the signal to mean
    something.
    """
    text = f"{title}\n{description}"
    hits, optional_hits = [], []

    for sentence in _sentences(text):
        n = _norm(sentence)

        # Language-agnostic native-speaker bar. Runs before the per-language loop
        # because its trigger sentence may name no language at all.
        for pattern in _NATIVE_DEMAND:
            if pattern.search(n) and not any(mk in n for mk in OPTIONAL_MARKERS):
                hits.append(("native-speaker", _excerpt(sentence, pattern)))
                break

        for lang, aliases in BLOCKED_LANGUAGES.items():
            if not any(f" {a} " in n for a in aliases):
                continue
            # Centred on the language name, not cut from the head of the span. A
            # 4,800-char bullet run quoted from its head says nothing about German.
            quote = _excerpt(sentence, _alias_pattern(lang))
            # Optional wins, always and first. This ordering is the entire mechanism
            # behind the Leica acquittal ("English and preferably German") and must
            # never be reordered — see docs/GATE_TRIBUNAL.md.
            if any(mk in n for mk in OPTIONAL_MARKERS):
                optional_hits.append((lang, quote))
            elif any(mk in n for mk in REQUIRED_MARKERS):
                hits.append((lang, quote))
            # No marker either way: a bare "English and German" adjacency is a
            # requirement. Checked last, and only when optional has already been
            # ruled out for this sentence, so it can never overturn an acquittal.
            elif _conjunction_pattern(lang).search(n):
                hits.append((lang, quote))

    if hits:
        lang, quote = hits[0]
        return {
            "verdict": FAIL,
            "reason": f"{lang} stated as a hard job condition",
            "quote": quote,
            "languages_required": sorted({h[0] for h in hits}),
            "languages_optional": sorted({h[0] for h in optional_hits}),
            "posting_language": posting_language,
        }

    detected = posting_language or detect_posting_language(title, description)
    if detected["verdict"] == FAIL:
        return {
            "verdict": FAIL,
            "reason": f"posting is written in {detected['language']}",
            "quote": detected["quote"],
            "languages_required": [],
            "languages_optional": sorted({h[0] for h in optional_hits}),
            "posting_language": detected,
        }

    # The detector can also come back "this is not English and I cannot name what it
    # is". That must not fall through to the PASS below, which would restate the exact
    # bug: reporting "no language condition stated" about a body nobody here can read.
    # Keyed on `english_absent` rather than on UNKNOWN generally, because the
    # detector's *other* UNKNOWN is "too thin to judge" — the AYES 503-character case,
    # which is a statement about the evidence, not about the language, and is already
    # handled by the snippet cap in `evaluate`.
    if detected["verdict"] == UNKNOWN and detected.get("english_absent"):
        return {
            "verdict": UNKNOWN,
            "reason": "posting does not read as English and its language could not "
                      "be identified - any stated language requirement is unread",
            "quote": detected["quote"],
            "languages_required": [],
            "languages_optional": sorted({h[0] for h in optional_hits}),
            "posting_language": detected,
        }

    verdict = PASS if (optional_hits or description) else UNKNOWN
    return {
        "verdict": verdict,
        "reason": ("only optional language preferences stated" if optional_hits
                   else "no language condition stated" if description
                   else "no description text to read a language condition from"),
        "quote": optional_hits[0][1] if optional_hits else None,
        "languages_required": [],
        "languages_optional": sorted({h[0] for h in optional_hits}),
        "posting_language": detected,
    }


# Stopwords that barely occur in English postings but saturate their own language.
# Deliberately short and high-frequency: a *ratio* over a long body is a robust
# signal and a bare count on a truncated snippet is not, which is why the
# thresholds below test both.
_STOPWORDS = {
    "italian": ("il", "la", "le", "dei", "delle", "della", "che", "con", "per",
                "una", "sono", "nostro", "azienda", "lavoro", "esperienza"),
    "german": ("und", "der", "die", "das", "mit", "fur", "von", "bei", "wir",
               "sie", "unser", "erfahrung", "kenntnisse", "aufgaben"),
    "spanish": ("los", "las", "para", "con", "que", "una", "nuestro",
                "empresa", "experiencia", "trabajo", "conocimientos",
                # LAW 3, 2026-08-24. Spanish was losing its own postings to
                # Portuguese, which reported a Spanish body as "written in
                # portuguese" — a correct FAIL naming the wrong language, which is
                # unauditable. The cause was one token: "de" sat in the Portuguese
                # list and not here, despite being the most frequent word in both.
                # Measured tallies on Spanish prose were portuguese 35 / spanish 24,
                # and `max(counts, key=counts.get)` duly picked portuguese.
                #
                # Fixed by giving each list the shared high-frequency words *and* its
                # own exclusive discriminators, so the winner is decided by what
                # actually separates the two rather than by which list forgot a word.
                # Spanish-exclusive: el/y/del/es/muy/tambien/ademas versus
                # Portuguese o/e/do/da/nao/sao/mais/tambem.
                "de", "el", "y", "del", "es", "un", "en", "por", "su", "sus",
                "como", "muy", "tambien", "ademas", "desarrollo", "gestion"),
    "hungarian": ("es", "az", "egy", "hogy", "vagy", "nem", "munka", "tapasztalat",
                  "feladatok", "elvarasok", "cegunk", "csapat"),
    "french": ("les", "des", "pour", "avec", "que", "une", "notre",
               "entreprise", "experience", "travail", "connaissances"),
    "dutch": ("het", "van", "een", "voor", "met", "onze", "werk",
              "ervaring", "kennis", "wij"),
    "polish": ("na", "jest", "oraz", "nasza", "praca",
               "doswiadczenie", "wymagania"),
    # The Nordic, Baltic-adjacent and Romance tails. Every language in
    # BLOCKED_LANGUAGES needs an entry here or the detector cannot see it at all:
    # IHM Business School's wholly Swedish body scored 0 hits in every list above
    # and the gate reported "no language condition stated" on 6,001 characters of
    # Swedish. Written accent-stripped, because `_norm` folds "är"→"ar" before
    # counting.
    #
    # Swedish, Danish and Norwegian overlap heavily by design ("som", "med",
    # "det", "du", "vi", "har", "om", "eller"), so each list leans on the tokens
    # that separate them — och/att/ett/till/ar/inte against og/at/et/til/er/ikke.
    # A Danish posting misnamed Norwegian is a cosmetic error in the reason
    # string, not a wrong verdict: all three are blocked either way.
    "swedish": ("och", "att", "som", "med", "till", "av", "den", "det", "ett",
                "du", "vi", "har", "ar", "pa", "om", "eller", "inte", "vill",
                "vara", "samt", "erfarenhet", "arbeta", "kunskap", "soker",
                "meriterande", "krav", "tjanster", "verksamhet"),
    "danish": ("og", "til", "af", "er", "det", "ikke", "med", "som", "har",
               "vi", "du", "et", "pa", "om", "eller", "vores", "erfaring",
               "arbejde", "kendskab", "krav", "opgaver"),
    "norwegian": ("og", "til", "av", "er", "det", "ikke", "med", "som", "har",
                  "vi", "du", "et", "pa", "om", "eller", "vare", "erfaring",
                  "arbeid", "kunnskap", "krav", "oppgaver"),
    "finnish": ("ja", "ei", "etta", "tai", "kanssa", "seka", "meilla", "haemme",
                "kokemus", "kokemusta", "osaaminen", "tehtavat", "tyo"),
    "czech": ("je", "se", "pro", "ve", "nebo", "nase", "praxe", "znalost",
              "zkusenosti", "pozadujeme", "nabizime"),
    # Paired with the Spanish list above — see the LAW 3 note there. These are the
    # tokens that are Portuguese and *not* Spanish: da/dos/das/nao/sao/mais/tambem.
    # "do" is deliberately absent: it is an ordinary English verb and the Polish
    # list's over-fire on English postings was traced to exactly that token.
    "portuguese": ("de", "para", "com", "que", "uma", "nossa", "empresa",
                   "experiencia", "trabalho", "conhecimentos", "voce",
                   "da", "dos", "das", "nao", "sao", "mais", "tambem", "em",
                   "seu", "seus", "como", "desenvolvimento", "gestao"),
    "romanian": ("si", "pentru", "cu", "care", "este", "sau", "nostru",
                 "experienta", "cunostinte", "echipa", "cerinte"),
}

# The same measurement for English, because several of the lists above collide with
# ordinary English words — "die", "van", "per", "sie", "la". Without this baseline a
# perfectly ordinary English posting scored 10 "Polish" stopwords ("do", "na") and
# was flagged. The detector must show the candidate language beating English by a
# clear margin, not merely reaching a threshold.
_ENGLISH_STOPWORDS = (
    "the", "and", "of", "to", "in", "for", "with", "you", "our", "we", "is", "are",
    "will", "your", "a", "an", "as", "on", "be", "have", "this", "that", "or",
    "from", "at", "by", "not", "do", "it", "they", "their",
)

# A posting must be at least this long, and clear this ratio, before its own
# language is read as a job condition. Below it the evidence is a truncated SEO
# snippet: AYES's Italian posting reached the ranker on 503 characters and there
# is no honest way to gate that, so it stays UNKNOWN and the model sees the flag.
MIN_CHARS_FOR_LANGUAGE_DETECTION = 400
MIN_STOPWORD_RATIO = 0.06
MIN_STOPWORD_HITS = 8
MIN_MARGIN_OVER_ENGLISH = 1.5   # the candidate language must clear English by this

# The other half of the question. Everything above asks "does some language I have a
# word list for beat English?", which can only ever catch a language that is *in* the
# table — and answering "no" was being reported as "reads as English" even when the
# body contained no English whatsoever. IHM's Swedish posting came back
# "reads as English (0 English stopwords vs 0 italian)": the absence of a match was
# read as proof of English.
#
# So measure English on its own terms. Across the 15 bodies of the 2026-08-23 rankset
# with enough text to judge, genuine English postings ran 0.194-0.351 English
# stopwords per token (the floor being a bullet-heavy Ocado/6 River listing). IHM's
# full body ran 0.011 — eighteen times below that floor. The threshold sits at 0.06,
# roughly three times under the observed English floor and five times over IHM, so a
# terse English posting has a wide margin before it trips.
#
# This yields UNKNOWN, not FAIL, and that asymmetry is deliberate: a named language is
# something the detector can defend, whereas "this is not English but I cannot say
# what it is" is a reason to look rather than a reason to reject. It also means the
# one plausible over-fire — an English posting written as a bare keyword dump — costs
# a flag instead of a discarded job.
MIN_ENGLISH_RATIO = 0.06
MIN_TOKENS_FOR_ENGLISH_TEST = 60


def detect_posting_language(title, description="") -> dict:
    """Flag a posting written in a language the profile cannot work in.

    This is about the *text*, not the employer's country, and it is deliberately
    hard to trigger. A thin snippet must not bypass the filter — but it must not
    fabricate a verdict either, so a short body returns UNKNOWN with the counts
    attached rather than a guess.
    """
    body = str(description or "")
    n = _norm(f"{title} {body}")
    tokens = n.split()
    total = len(tokens)
    if not total:
        return {"verdict": UNKNOWN, "language": None, "ratio": 0.0, "hits": 0,
                "english_hits": 0, "english_absent": False, "quote": None,
                "reason": "no text"}

    counts = {lang: sum(1 for tok in tokens if tok in words)
              for lang, words in _STOPWORDS.items()}
    english = sum(1 for tok in tokens if tok in _ENGLISH_STOPWORDS)
    lang = max(counts, key=counts.get)
    hits = counts[lang]
    ratio = hits / total

    common = {"language": lang if hits else None, "ratio": round(ratio, 4),
              "hits": hits, "english_hits": english, "english_absent": False}
    if len(body) < MIN_CHARS_FOR_LANGUAGE_DETECTION:
        return {**common, "verdict": UNKNOWN, "quote": None,
                "reason": f"only {len(body)} chars of text - too thin to judge"}
    if (hits >= MIN_STOPWORD_HITS and ratio >= MIN_STOPWORD_RATIO
            and hits >= english * MIN_MARGIN_OVER_ENGLISH):
        return {**common, "verdict": FAIL, "quote": body.strip()[:200],
                "reason": f"{hits} {lang} stopwords ({ratio:.0%} of the text) "
                          f"against {english} English"}
    # No list matched, so the language cannot be named — but that is not the same as
    # the text being English, and this is where the check for English itself goes.
    # `language` is cleared rather than reported: a body with three incidental French
    # tokens is not a French posting, and naming it one would be a false statement in
    # the report. The counts carry the actual finding.
    if (total >= MIN_TOKENS_FOR_ENGLISH_TEST
            and english / total < MIN_ENGLISH_RATIO):
        return {**common, "verdict": UNKNOWN, "language": None,
                "english_absent": True, "quote": body.strip()[:200],
                "reason": f"only {english} English stopwords in {total} words "
                          f"({english / total:.1%}, against {MIN_ENGLISH_RATIO:.0%} "
                          f"for the tersest English posting measured) - this does "
                          f"not read as English, and no listed language matched it"}
    return {**common, "verdict": PASS, "language": None, "quote": None,
            "reason": f"reads as English ({english} English stopwords vs "
                      f"{hits} {lang})"}


# ----------------------------------------------------------- experience gate

MAX_YEARS_ELIGIBLE = 3          # 0-3 is eligible; 4+ stated as mandatory is a FAIL

# "5+ years", "at least 5 years", "minimum 5 years", "5 years of experience",
# "3-5 years", "5 to 7 years", "5-7 yrs". The number is what matters; the
# surrounding wording is classified separately below.
#
# `re.I` is not needed for classification — that runs on `_norm` output, which is
# already lowercase. It is here so the same pattern can be re-run over the *raw*
# span to place the quote, where "5+ Years" keeps its capital.
#
# CEILING POLICY (2026-08-24, by instruction). A range is a requirement at its UPPER
# bound: "3-5 years" asks for five, "6-8 years" asks for eight. The low end is the
# concession a posting is willing to make, not the bar it advertises, and grading on
# it let a five-year ask through as a three-year one.
#
# The separator class therefore has to catch every dash a posting might use, or a
# range silently degrades into whichever bare figure the pattern can still reach —
# which is the *higher* one here, so the old bug happened to fail safe on the number
# and unsafe on everything downstream that reads `low`. The dash variants are listed
# in the class for the raw-span re-run in `_excerpt`; the classification pass never
# sees them, because `_norm` has already folded them to "-" (see there — that fold,
# not this class, is what actually repairs the verdict).
_YEARS = re.compile(
    r"(?<![0-9])(\d{1,2})\s*(?:\+|plus)?\s*"
    r"(?:(?:[-‐-―−－]|/|to|up to)\s*(\d{1,2})\s*(?:\+|plus)?\s*)?"
    r"(?:year|yr)s?\b", re.I)

# Wording that forbids an automatic discard even when a big number is present.
# Every one of these was observed doing real work on the corpus: Citi's
# "+/- 5 years of experience in Finance industry" carries two of them at once.
AMBIGUITY_MARKERS = (
    "+/-", "+-", "approximately", "approx", "around", "circa", "about",
    "ideally", "preferably", "preferred", "nice to have", "nice-to-have",
    "or equivalent", "equivalent experience", "comparable", "desirable",
    "advantage", "a plus", "is a plus", "bonus", "asset",
    "not required", "or more", "typically",
)

# The fourth ambiguity marker: years scoped to an adjacent *domain* rather than to
# the role's core work. Citi's "+/- 5 years of experience in Finance industry" is
# the case — the years are about a sector, not about the analytical work. Only the
# narrow, quotable form is implemented here; the general judgement stays with the
# model, which is why the quote travels with the verdict.
DOMAIN_SCOPED_MARKERS = ("industry", "sector", "domain", "vertical")

# Wording that makes a years figure a hard requirement.
#
# "background" and "track record" are the 2026-08-24 additions. The list keyed on
# the literal word "experience", so stripping that one word — "5 years of relevant
# background", "a track record of 6 years" — turned a hard requirement into prose the
# gate filed as incidental and passed. They are synonyms in job-ad register, not
# hedges, and nothing else in the pipeline was reading them.
MANDATORY_MARKERS = (
    "required", "requirement", "must", "minimum", "min", "at least",
    "no less than", "we require", "you have", "you bring", "candidates must",
    "essential", "mandatory", "proven", "demonstrated", "solid", "experience",
    "background", "track record", "history of", "hands on", "hands-on",
)

# How far from the number a marker has to sit to be about the number. This is the
# whole reason the windows exist rather than a sentence-wide scan: Fressnapf reads
# "Minimum 5-7 years of experience delivering and owning complex solutions,
# **ideally** within I2P, Finance Operations" — the "ideally" scopes the domain at
# the end of the sentence, not the "Minimum" at the front, and a sentence-wide
# check let it excuse an unambiguous 5-7 year floor.
_LOOKBEHIND = 60
_LOOKAHEAD = 45

# Wording that sets a floor so explicitly that a qualifier sitting *after* the
# figure cannot have been about the number. Siemens Healthineers' "At least 5 years
# of project management experience, preferably in a highly regulated industry" is
# the case that forced this: "preferably" qualifies the *industry*, sits 44
# characters out — inside the lookahead — and, because the ambiguity branch is
# tested before the mandatory one, it excused an explicit "At least 5 years". The
# posting was reported as a verified pass in the 2026-08-23 top 25.
#
# Checked in the *lookbehind only*, and it does not override an ambiguity marker
# that is also in the lookbehind. Both halves matter:
#   * lookbehind only — "8 years of experience preferred" has no floor marker, so
#     the "preferred" still pardons it. Narrowing the lookahead by character count
#     instead would have made that a discard, which it is not.
#   * ambiguity in the lookbehind still wins — Citi's "+/- 5 years of experience in
#     Finance industry" carries "+/-" ahead of the figure and stays a PASS.
HARD_FLOOR_MARKERS = ("at least", "minimum", "no less than", "not less than")

# A years figure attached to one of these is not about the candidate's tenure.
_NOT_TENURE = (
    "years of age", "over the past", "in the last", "founded", "since",
    "for the past", "anniversary", "history", "contract", "fixed term",
    "fixed-term", "duration", "graduated within", "warranty", "years old",
)

# Company-age prose, recognised by its *subject* rather than by its preposition.
#
# These exist to contain the bare "N years in <domain>" rule added below: once a
# preposition alone can make a figure count, "our company has 18 years in business"
# reads as a candidate requirement. The first attempt at this suppressed the
# prepositional phrases instead ("in business", "in the market") and immediately ate
# a real requirement — Amaris's "At least 15 years of experience in business
# analysis" contains the substring "in business", so the figure was discarded and a
# 15-year floor passed. `tests/test_hard_gates.py::ExperienceGate` caught it.
#
# Checked in the lookbehind only. A corporate subject sits ahead of the figure; a
# requirement's domain sits after it, which is exactly what keeps the two apart.
COMPANY_AGE_MARKERS = (
    "our company has", "the company has", "we have been", "we have over",
    "company was", "we celebrate", "celebrating", "we have grown", "has grown",
    "our journey", "in business for", "on the market for", "operating for",
)

# ...and the mirror case: when the phrase that made a figure look like company
# history is in fact the phrase that makes it a requirement.
#
# A genuine precedence conflict, not a gap: "proven" is a MANDATORY_MARKER and
# "history" is a _NOT_TENURE suppressor, so "A proven history of 5 years" hit both
# and the suppressor's `continue` won — the figure was discarded and the posting
# passed with "no years requirement stated". `_NOT_TENURE` exists to skip "our
# company history"; "a proven history of N years" is the opposite claim. Checked
# ahead of the suppressor so the more specific phrase decides.
TENURE_OVERRIDE_MARKERS = ("proven history", "track record", "demonstrated history",
                           "documented history", "history of delivering",
                           "history of leading", "history of building")


def experience_verdict(title, description="", *, full_text=True,
                       caveat=SNIPPET_CAVEAT) -> dict:
    """Does the posting state a years requirement above the profile's ~3.7?

    `full_text=False` says `description` is incomplete — a truncated card snippet,
    or a fetched body that was cut at its length limit — which caps the best
    outcome at UNKNOWN. `caveat` names which one. See `_unverified`. This applies
    to the soft-years PASS too: "the highest figure here is one you meet" is a
    claim about the whole posting, and neither a snippet nor a cut body is the
    whole posting.
    """
    block = _experience_verdict(title, description)
    return block if full_text else _unverified(block, caveat)


def _years_reason(item) -> str:
    """The FAIL reason, naming the figure the gate actually graded on.

    A range gets its span spelled out and the ceiling stated. "5+ years stated as a
    hard requirement" against a posting that printed "3-5 years" reads as the gate
    misquoting the posting, when what happened is the gate applying a policy — and
    the ceiling is the single decision here most worth being auditable from the
    report alone, without re-reading this module.
    """
    if item["low"] != item["required"]:
        return (f"{item['low']}-{item['required']} years stated as a hard "
                f"requirement, read at its ceiling of {item['required']}")
    return f"{item['required']}+ years stated as a hard requirement"


def _experience_verdict(title, description="") -> dict:
    """Does the posting state a years requirement above the profile's ~3.7?

    Only an explicit, unambiguous 4+ is a FAIL. A range counts as its **ceiling** —
    "3-5 years" is a five-year requirement, not a three-year one (see the ceiling
    policy note on `_YEARS`) — an ambiguity marker beside the figure forbids the
    discard *unless* an explicit floor was stated ahead of it (see
    HARD_FLOOR_MARKERS), and a figure that is not about candidate tenure at all is
    skipped.
    """
    text = f"{title}\n{description}"
    found, blocked = [], []

    for sentence in _sentences(text):
        n = _norm(sentence)
        # `ordinal` is this match's index among the years figures in the span, which
        # is how `_excerpt` finds the same figure in the raw text to quote around.
        for ordinal, match in enumerate(_YEARS.finditer(n)):
            low = int(match.group(1))
            high = int(match.group(2)) if match.group(2) else low
            # The ceiling policy in one line: every range is graded at its upper
            # bound, whatever separator wrote it. A bare figure has low == high, so
            # the single-figure path is untouched.
            required = max(low, high)
            # A marker only qualifies the number it sits next to. See _LOOKBEHIND.
            before = n[max(0, match.start() - _LOOKBEHIND): match.start()]
            after = n[match.end(): match.end() + _LOOKAHEAD]
            window = before + match.group(0) + after
            # An explicit floor stated ahead of the figure outranks a qualifier
            # stated after it. See HARD_FLOOR_MARKERS.
            hard_floor = (any(mk in before for mk in HARD_FLOOR_MARKERS)
                          and not any(mk in before for mk in AMBIGUITY_MARKERS))
            # The more specific phrase decides — see TENURE_OVERRIDE_MARKERS.
            if (any(p in window for p in _NOT_TENURE)
                    and not any(mk in window for mk in TENURE_OVERRIDE_MARKERS)):
                continue
            # A bare tenure preposition, with no requirement noun anywhere. Google's
            # "5 years of experience in operations" states the noun; plenty of
            # postings do not — "5 years in operations", "6 years within supply
            # chain" — and those were filed as "years stated without a mandatory
            # marker" and passed. "N years <preposition> <domain>" in a job ad is a
            # tenure requirement; the noun is elided, not absent. Company-age prose
            # that shares the shape is excluded by `_NOT_TENURE` above.
            tenure_phrase = bool(re.match(
                r"\s*(?:in|of|within|as|leading|managing|working|supporting|driving|"
                r"delivering|building)\b", after)) and not any(
                mk in before for mk in COMPANY_AGE_MARKERS)
            # `low` is kept only so the reason string can say a range was read at its
            # top rather than quoting a figure the posting never printed alone.
            item = {"required": required, "low": min(low, high),
                    "quote": _excerpt(sentence, _YEARS, ordinal)}
            if required <= MAX_YEARS_ELIGIBLE:
                blocked.append({**item,
                                "why": "the requirement is within the profile's range"})
            elif hard_floor:
                found.append(item)
            elif any(mk in window for mk in AMBIGUITY_MARKERS):
                blocked.append({**item, "why": "ambiguity marker beside the figure"})
            elif any(mk in after for mk in DOMAIN_SCOPED_MARKERS):
                blocked.append({**item,
                                "why": "years scoped to an adjacent domain, not the "
                                       "role's core work"})
            elif (any(mk in window for mk in MANDATORY_MARKERS)
                    or "+" in match.group(0) or tenure_phrase):
                found.append(item)
            else:
                blocked.append({**item,
                                "why": "years stated without a mandatory marker"})

    if found:
        worst = max(found, key=lambda f: f["required"])
        return {"verdict": FAIL, "years_required": worst["required"],
                "quote": worst["quote"],
                "reason": _years_reason(worst), "inspected": blocked}
    if blocked:
        soft = max(blocked, key=lambda f: f["required"])
        return {"verdict": PASS, "years_required": soft["required"],
                "quote": soft["quote"], "reason": soft["why"], "inspected": blocked}
    if description:
        return {"verdict": PASS, "years_required": None, "quote": None,
                "reason": "no years requirement stated", "inspected": []}
    return {"verdict": UNKNOWN, "years_required": None, "quote": None,
            "reason": "no description text to read a years requirement from",
            "inspected": []}


# ------------------------------------------------------------ seniority gate

# Grade words that put a posting above this profile's level, read off the title
# alone. Three groups, and they are here for different reasons:
#
#   "senior"/"sr"/"snr" — the original three. The abbreviations are needed because
#   "Sr. Business Systems Analyst" and "Sr Program Manager, Global Finance
#   Transformation" are both in the 2026-08-22 corpus and neither spells the word.
#
#   "lead"/"leader" — matched as standalone words rather than as the compounds
#   ("Team Lead", "Program Lead", "Project Lead") that prompted the request. The
#   compound list is unbounded: the 2026-08-22 corpus alone carries Enablement,
#   Platform Team, Benefits Operations, Value Stream, Reporting & Analytics, Global
#   SCM Project and Digital Go to Market variants. Any title where "lead" stands as
#   its own word is claiming the role level, so matching the word covers every
#   compound including "(Lead)", whose parentheses `_norm` strips.
#
#   "principal"/"head"/"director"/"expert" — above the level too, and previously
#   left to the Phase 2 prompt to score on the Experience dimension. That was a
#   slot-level mistake: a Director posting reaching Phase 2 spends a rank slot to
#   arrive at 25/100.
SENIORITY_MARKERS = ("senior", "sr", "snr", "lead", "leader",
                     "principal", "head", "director", "expert")

# "lead" is the one marker that is also an ordinary noun in this profile's own
# domain, and the collisions are not hypothetical: "lead time" is core supply-chain
# vocabulary (T4 is a target track) and "lead-to-cash" is a named process (T5).
# Discarding "Lead Time Reduction Analyst" as a senior-grade posting would be the
# gate lying about its evidence in the direction that is invisible — the job lands in
# the deferred list quoting a grade the title never claimed.
#
# Keyed to the following word, because that is what disambiguates: "Lead Data
# Scientist" is a grade, "Lead Time Analyst" is not. Nothing here occurs in the
# 2026-08-22 corpus, so this is a guard rather than a fix.
LEAD_NOUN_FOLLOWERS = ("time", "times", "to", "generation", "gen", "management",
                       "qualification", "nurturing", "scoring", "conversion")


def _title_words(title) -> list:
    """The title split into words on the separators `_norm` preserves.

    Splitting on `-`, `/` and `&` as well as whitespace is what makes "Sr-Manager"
    and "Junior/Senior Analyst" match while "SRE Manager" and "Sri Lanka" do not, and
    what keeps "Leadership Development" out: "leadership" is one word and is not
    "leader". Equivalent to the word-boundary regex this replaced, and used in its
    place because the "lead" disambiguation has to see the *next* word.
    """
    return [word for word in re.split(r"[\s\-/&]+", _norm(title)) if word]


def seniority_verdict(title, description="") -> dict:
    """Does the title itself advertise a grade above this profile's level?

    Purely lexical, and deliberately separate from `experience_verdict`. The two
    catch different things: "Senior Data Analyst" with no years figure anywhere in
    the body is invisible to the years gate, and "4-6+ years of overall experience"
    on a plainly-titled role is invisible to this one. On the 2026-08-22 corpus 99
    titles carry a senior-family marker and only a fraction state years at all.

    `description` is accepted and ignored. A grade word in a body sentence — "you
    will report to a senior manager", "our senior leadership team" — is about
    somebody else, and gating on it would discard postings for the org chart above
    the role rather than for the role. The title is the only place this claim is
    made about the job being advertised.

    Never returns UNKNOWN, and that is the point of reading the title: enrichment
    fetches descriptions, so an UNKNOWN here would ask for a request that could not
    possibly answer it. A job with no title at all is malformed rather than
    ambiguous, and `has_signal` in the caller removes it on other grounds.
    """
    words = _title_words(title)
    for index, word in enumerate(words):
        if word not in SENIORITY_MARKERS:
            continue
        if word == "lead":
            following = words[index + 1] if index + 1 < len(words) else ""
            if following in LEAD_NOUN_FOLLOWERS:
                continue        # "lead time", "lead-to-cash" — a noun, not a grade
        return {"verdict": FAIL, "marker": word,
                "quote": " ".join(str(title or "").split()),
                "reason": f"title advertises a grade above this level ('{word}')"}
    return {"verdict": PASS, "marker": None, "quote": None,
            "reason": "no seniority marker in the title"}


# -------------------------------------------------------- pure-technical gate

def pure_technical_verdict(axes: dict, min_body_domains: int = 2) -> dict:
    """Is this a pure research / core-ML role with no business component?

    Reads the scorer's own axis result rather than re-deriving anything, so the two
    cannot drift, and asks the question at the level of the **role**: a domain word
    in the title, or several independent domain categories described in the body.
    An employer that merely operates in supply chain does not qualify its ML
    Engineer opening — that is the explicit requirement, and the reason a single
    body mention is not enough.

    Returns FAIL for a pure technical role, PASS otherwise. `axes` is the dict
    `ScoringModel.score` returns; a job scored under the old single-axis model has
    no axes and gets UNKNOWN.
    """
    if not axes:
        return {"verdict": UNKNOWN, "marker": None,
                "reason": "no two-axis result to classify from"}
    marker = axes.get("core_tech_marker")
    if not marker:
        return {"verdict": PASS, "marker": None,
                "reason": "no core-tech marker in the title"}
    if axes.get("domain_in_title"):
        return {"verdict": PASS, "marker": marker,
                "reason": "core-tech title carries a business domain: "
                          + ", ".join(axes["domain_in_title"])}
    body = axes.get("domain_from_description") or []
    if len(body) >= min_body_domains:
        return {"verdict": PASS, "marker": marker,
                "reason": f"description describes business work in {len(body)} "
                          "domains: " + ", ".join(body)}
    return {"verdict": FAIL, "marker": marker,
            "reason": f"pure technical role - title matches '{marker}' and "
                      + (f"the only domain evidence is one passing mention "
                         f"({body[0]})" if body
                         else "the posting shows no business domain at all")}


# ------------------------------------------------------------------ combined

def evaluate(job: dict, axes: dict = None, min_body_domains: int = 2) -> dict:
    """Run all five gates over one job and return a single verdict block.

    `overall` is FAIL if any gate failed, PASS if all decided and none failed, and
    UNKNOWN when the text was too thin to judge and nothing failed. The caller uses
    UNKNOWN to *target enrichment*, which is the point of running this before the
    cut rather than after it. Display surfaces render UNKNOWN as "unverified".

    Evidence is graded by provenance *and* by completeness, not just by length.
    `description` is a body this pipeline fetched; `description_snippet` is the ~500
    characters a search card carried. A snippet is enough to convict — it stated
    what it stated — and never enough to acquit, because what it omits is mostly
    what got truncated. So the two gates that can pass on silence, language and
    experience, are capped at UNKNOWN when the snippet is all there is. Without
    that cap a job nobody had read rendered as PASS, indistinguishable from one
    that was actually verified.

    A fetched body carrying `description_truncated` gets the same cap, for the same
    reason. `enrich_linkedin.py` cuts bodies at `max_description_chars(job)` and
    sets that flag so the loss is not invisible; grading on the field the body
    arrived in ignored it, so a body ending mid-sentence in an ellipsis counted as
    the whole posting. On the 2026-08-23 rankset that passed two rows —
    Diligent's "Product Operations Manager" and IHM Business School's "Digital
    Operations & AI Agent Specialist" — as *verified*, on silence, over text whose
    requirements section was below the cut. A long body is better evidence than a
    card snippet, but "better" is not "complete", and only complete acquits.

    That cap was 6000 when those two rows were graded, and both were cut by it. It
    is now 20000 for LinkedIn, because the 6000 was sized against a fetch path that
    could not return more (see `enrich_linkedin.LINKEDIN_DESCRIPTION_CHARS`) — real
    bodies measure 6k-19k. Raising it makes this downgrade fire far less often, but
    does not retire it: a posting longer than 20000 chars is still cut, and the
    asymmetry has to hold for that one too.

    Seniority reads the title, which is never truncated, so it is exempt — and it
    can still turn an otherwise-UNKNOWN job into a FAIL, which saves an enrichment
    request rather than spending one to confirm a discard. pure_technical is exempt
    too: it passes on domains it *found*, never on their absence.
    """
    title = job.get("title") or ""
    description = job.get("description") or ""
    snippet = job.get("description_snippet") or ""
    body = description or snippet
    # Completeness, not just provenance. A truncated body is incomplete evidence
    # even though it came from the field a complete one would arrive in.
    cut_body = bool(description) and bool(job.get("description_truncated"))
    full_text = bool(description) and not cut_body
    caveat = CUT_BODY_CAVEAT if cut_body else SNIPPET_CAVEAT

    language = language_verdict(title, body, full_text=full_text, caveat=caveat)
    experience = experience_verdict(title, body, full_text=full_text, caveat=caveat)
    # Same completeness cap as language and experience, and for the same reason: a
    # snippet that says nothing about sponsorship is not evidence that the role
    # offers it. See the `_unverified` note above.
    sponsorship = sponsorship_verdict(title, body, full_text=full_text, caveat=caveat)
    seniority = seniority_verdict(title, body)
    technical = pure_technical_verdict(axes, min_body_domains)

    verdicts = (language["verdict"], experience["verdict"], sponsorship["verdict"],
                seniority["verdict"], technical["verdict"])
    if FAIL in verdicts:
        overall = FAIL
    elif UNKNOWN in verdicts:
        overall = UNKNOWN
    else:
        overall = PASS

    failed = [name for name, block in
              (("language", language), ("experience", experience),
               ("sponsorship", sponsorship),
               ("seniority", seniority), ("pure_technical", technical))
              if block["verdict"] == FAIL]
    return {
        "overall": overall,
        "failed": failed,
        "language": language,
        "experience": experience,
        "sponsorship": sponsorship,
        "seniority": seniority,
        "pure_technical": technical,
        "evidence_chars": len(body),
        "evidence_source": ("description_truncated" if cut_body
                            else "description" if description
                            else "description_snippet" if snippet else "none"),
    }
