#!/usr/bin/env python3
"""Phase 1b: choose which fetched jobs are worth a model-scored deep rank.

Usage:
    python3 prerank_jobs.py --jobs /tmp/jobsearch_fetched_jobs_2026-08-19.json \
        --rankset /tmp/jobsearch_rankset_2026-08-19.json \
        --deferred /tmp/jobsearch_deferred_2026-08-19.json

Why this phase exists
---------------------
Phase 2 scores every job it is handed in one model pass, measured on 2026-08-19 at
1696s for 25 jobs — roughly 68 seconds per job, not the ~24s earlier comments here
claimed. The 2026-08-18 run fetched 504 jobs; it timed out and produced no documents
at all. Ranking fewer jobs is the only fix that does not cost discovery coverage.

This script picks that subset **without spending a single model call**. It is
deliberately not a fit score:

    prerank  -> "is this worth 68 seconds of real evaluation?"  (free, here)
    Phase 2  -> "how well does Salman actually fit it?"         (the real score)

Nothing here awards or withholds fit points, and nothing here decides whether a
CV gets written. That stays with the ranker and `gate_jobs.py`.

Two scoring models
------------------
The original counts matches against the LinkedIn *search* queries. Those 13 strings
are few because each one costs a request — the wrong vocabulary for scoring, which
costs nothing, and the conflation cost real jobs: an "Advanced PMO Specialist -
Sourcing & Procurement Excellence" scored a literal 0 while "Machine Learning
Engineer" scored 330, because the vocabulary held no word for procurement, process
excellence, continuous improvement or programme management.

The replacement lives in `two_axis_score.py` and scores business-domain and
technical-enabler categories separately, rewarding their overlap. It is enabled by
`scoring.enabled` in the matrix, or forced per-run with `--two-axis`, and it is off
by default so the scheduled production run is unchanged until a full pass has been
reviewed. `--no-two-axis` forces the original model back on.

Two stages, and why enrichment sits between them
------------------------------------------------
Run with `--stage shortlist` this script cuts wide (`prerank.shortlist_budget`,
~80); run with `--stage final` it cuts to `prerank.deep_rank_budget` (25). The
sandbox pipeline uses both, with Phase 1c enrichment in the middle:

    corpus (559)  --stage shortlist-->  shortlist (80)
                  --enrich_linkedin-->  shortlist, now with full descriptions
                  --stage final------->  rankset (25)

Enriching *after* the final cut — the original order — spent the description budget
on jobs that had already survived on their titles, and could not rescue the jobs
that needed it. On the 2026-08-19 corpus 102 postings (18%) matched a business
domain in the title with no AI/data word anywhere in it; their enabler signal was
in the body, unread. The best of them ranked 31st and a 25-slot budget reached
none. Citi's "Digital Transformation Senior Analyst" is the concrete loss: on its
title alone it reads enabler-only and lands 23rd of the 41 alert jobs, while its
real description carries business analysis and continuous improvement — the hybrid
that makes it a match. Cutting first meant never reading the thing that would have
kept it.

`--stage final` may be given `--corpus` as well as `--jobs`. It then writes its
annotations back onto the corpus by `dedup_key` and derives the deferred list from
there, so the funnel stays complete and honest: one file accounts for all 559
jobs, whether a job was cut at the shortlist or at the final cut. Without it the
corpus would still carry stage 1's `selected: true` for a job stage 2 then cut.

Deferred, never dropped
-----------------------
Every job the cut excludes is written to `--deferred` with its pre-rank score and
the reason, and the report renders the near-misses. A pipeline that quietly
narrowed 504 jobs to 15 would be indistinguishable from a thin market, so the
whole funnel stays visible and `--deferred` is the record of it.

The per-track floor, and why this is not a plain top-N
------------------------------------------------------
Vocabulary matches are not comparable across tracks. "AI Engineer" is a literal
query string, so a T1 posting titled *AI Engineer* scores a full phrase hit.
Salman's own current title, *Junior Performance Manager*, overlaps the T5 query
"Performance Management" on the single token "performance" — one point. A plain
top-N by score would therefore hand every slot to T1 and never evaluate a T5
role, which is precisely the bias the five Profile Tracks exist to remove
(`01-candidate-profile.md`: "do NOT treat T1 (AI/ML) as the only real match").

So every track with a matching job is guaranteed `per_track_floor` slots before
the remaining budget goes to the best scores globally. Only jobs actually
attributed to a track can claim those reserved slots — see `select`.

What is screened here, and what is not
--------------------------------------
Under the two-axis model three deterministic hard gates run before any budget is
divided (`hard_gates.py`): a language gate, an experience gate, and a pure-technical
gate. They were moved here from the ranker because on the 2026-08-19 corpus six jobs
each consumed one of the 25 deep-rank slots and one of the 15 enrichment requests
and were then discarded by the model on wording — "At least 5 years", "10+ years
BPM/BPI" — that was already present in the text at this stage. A gate that runs
after the budget is spent cannot save the budget.

They only ever discard on a **quotable** condition, and the quote travels with the
verdict into the deferred list, so every discard is auditable. Three properties keep
them from over-reaching:

  * No evidence is UNKNOWN, not FAIL. An unenriched LinkedIn card has no body text,
    and reading that as a failure would discard most of a corpus unread. UNKNOWN is
    also what enrichment allocation targets: it marks the jobs where spending a
    request buys information.
  * Optional wording always beats required wording. "Hungarian (preferred)" and
    "French is a plus" pass; only "fluent", "required", "mandatory" fail.
  * The employer's country is not a language requirement. A Munich role advertised
    in English passes.

Sponsorship *is* screened here as of the gate tribunal, but only the arithmetically
impossible half of it: a posting that refuses sponsorship outright, or that demands a
passport, citizenship or pre-existing unrestricted work rights, cannot be won by a
Pakistani passport holder no matter how the ranker reads it. Everything softer than
that — a country preference, a relocation question, an unstated permit situation —
still belongs to the real Eligibility Gate in the ranker (prompt Step 3), which
FLAGs rather than drops, because a sponsorship-flagged job still qualifies for
documents. Jobs already in `seen_jobs.json` are excluded outright, since the
ranker discards them at Step 2 anyway and giving them a slot buys nothing.

Alert-matched jobs
------------------
Keys in `job_scraper/alert_matched.json` are force-included up to `alert_budget`,
bypassing the score cut: LinkedIn's own matching surfaced them, which is real
information about what to *look at*. It stays a selection signal only — no points
are added and the 75/60 document gate is untouched. Expiry comes from importing
`gate_jobs.live_alert_keys`, so a stale alert cannot buy a slot forever and the
30-day rule keeps exactly one implementation.
"""

import argparse
import importlib.util
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_MATRIX = REPO / "config" / "search_matrix.json"
DEFAULT_SEEN = REPO / "job_scraper" / "seen_jobs.json"
DEFAULT_ALERTS = REPO / "job_scraper" / "alert_matched.json"

# Fallbacks, used only when config/search_matrix.json carries no `prerank` block.
DEFAULT_DEEP_RANK_BUDGET = 15
DEFAULT_ALERT_BUDGET = 10
DEFAULT_PER_TRACK_FLOOR = 2

# `--stage shortlist` cuts to this instead of the deep-rank budget. It exists to be
# *wide*: the shortlist is what Phase 1c enrichment may read bodies for, and a job
# absent from it can never have its description scored. Enrichment itself is what is
# actually scarce (15 LinkedIn requests), so the shortlist is bounded only by the
# cost of holding and re-scoring it — free — and by staying small enough that the
# priority order inside enrichment still means something.
DEFAULT_SHORTLIST_BUDGET = 80

# A long description hits many vocabulary tokens just by being long, so its
# contribution is capped. The title is the signal; the body is a tiebreak. This
# also keeps portals that emit no description at all (arbeitnow's `search --json`
# drops it) from being penalized for a gap that is not the posting's fault.
TITLE_WEIGHT = 10
MAX_DESCRIPTION_CONTRIBUTION = 20

# Tuple layout for a scored row, shared by score/select/annotate. AXES carries the
# two-axis breakdown, or None under the original model.
SCORE, POSITION, TRACK, JOB, TITLE_HITS, DESC_HITS, AXES = range(7)

# Words that name a *grade*, not a role. Two postings at one company differing only
# by grade are one opening for slot purposes: Deloitte Geneva listed "Senior Manager -
# AI & Data (AI Strategy)" and "Manager - AI & Data – AI Strategy" and each took a
# top-25 slot on 2026-08-19, because exact title keying sees two distinct strings.
# Stripping the grade is what makes them one key. Deliberately not stripped:
# "manager", "analyst", "engineer", "architect" and other role nouns.
GRADE_WORDS = frozenset("""
senior sr snr junior jr jnr mid medior middle principal staff lead leader head chief
associate advanced entry graduate intern trainee level i ii iii iv
""".split())

# Joining words that inflate the similarity denominator without carrying meaning.
TITLE_STOPWORDS = frozenset("of and or the for with a an in to at on".split())

# Spellings of one thing. Contracted rather than expanded because the abbreviation is
# the shorter token and the direction does not matter as long as it is consistent.
TITLE_ABBREVIATIONS = (
    ("artificial intelligence", "ai"),
    ("machine learning", "ml"),
    ("business intelligence", "bi"),
    ("continuous improvement", "ci"),
    ("business process management", "bpm"),
    ("robotic process automation", "rpa"),
    ("supply chain management", "scm"),
)

# Two role signatures this similar, at the same company, are one opening. Integer
# percent so the comparison never depends on float representation.
#
# 80 rather than lower on purpose. A false merge silently costs a genuinely different
# job its slot, while a false split only costs a duplicate check — so the threshold is
# set where the observed duplicates land (Deloitte's two titles reach 100 after grade
# stripping) rather than as low as it could go. "Process Analyst" vs "Process Analyst,
# Automation" scores 67 and stays two roles, which is the intended conservative call.
SIMILARITY_THRESHOLD = 80

# Requisition references in a title: "12345", "req12345", "R-4821". One requisition
# per role is the norm, so a ref is noise for role identity.
_REF = re.compile(r"^[a-z]{0,3}\d{3,}$")

# Single-letter tokens are dropped, which is really a rule about the European
# diversity tag: "(m/f/d)", "(m/w/d)", "(f/m/x)", "(h/f)" normalize to loose letters
# and inflated the denominator enough to score a real duplicate pair at 40. The
# variants are too numerous to enumerate, and length is the property they share. The
# cost is that a "C Developer" or "R Developer" title loses its language token — both
# are pure-technical roles the gates discard anyway, and the company must still match.
_TAG_LETTERS = 1

# Top-level matrix keys that are configuration, not portals. `linkedin` is read
# separately for its per-track vocabulary; `prerank` is this script's own budget
# block. Everything else that is enabled and carries `queries` is treated as a
# portal, so a source added later by /add-portal contributes vocabulary for free.
NON_PORTAL_KEYS = {"linkedin", "prerank"}


def _load_sibling(name: str):
    """Import a sibling script by path, so this works run from any directory."""
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Reused, not reimplemented. `title_match_score` is the scorer Phase 1c already
# uses to allocate its enrichment budget, and `live_alert_keys` is the single
# implementation of the 30-day alert expiry. A second copy of either would drift
# from the original, and then two phases would disagree about the same job.
_enrich = _load_sibling("enrich_linkedin")
_gate = _load_sibling("gate_jobs")
# The two-axis scoring model. Imported unconditionally but *used* only when the
# matrix turns it on (or `--two-axis` forces it), so importing it cannot change what
# the scheduled production run computes.
_two_axis = _load_sibling("two_axis_score")
# The deterministic hard gates. Same conditional treatment as the scoring model:
# imported always, consulted only when the two-axis model is active, because a gate
# that discards jobs is the last thing to switch on without a reviewed sandbox run.
_gates = _load_sibling("hard_gates")

title_match_score = _enrich.title_match_score
# Same normalizer the scorer uses, so near-duplicate keying agrees with matching
# on what counts as the same words. Private by name, but duplicating it here would
# reintroduce exactly the drift the imports above exist to prevent.
_words = _enrich._words


def build_vocabulary(matrix: dict) -> tuple:
    """(tracks, extra) — the attribution vocabulary, and scoring-only extras.

    `tracks` is {track_id: [query, ...]} for the Profile Tracks, and it is the only
    thing track attribution and the per-track floor are computed against. `extra`
    holds portal query strings no track already covers; those widen the *scoring*
    vocabulary so a job matching a portal-only term is not penalized, but they can
    never earn a floor slot.

    Keeping these separate is load-bearing. Folding the portal queries in as a
    synthetic sixth track made that pseudo-track the union of 11 strings —
    including duplicates of "AI Engineer", "Machine Learning Engineer" and "Data
    Scientist" — so it outscored every real track on token count and won the
    attribution for almost any AI/data title. On the 2026-08-18 corpus, KPMG's
    "Data Scientist / Machine Learning Engineer" was reported as `_other` instead
    of T1/T2, and the pseudo-track consumed the floor slots that T1 and T2 were
    supposed to be guaranteed.
    """
    tracks = {}
    linkedin = matrix.get("linkedin", {})
    if linkedin.get("enabled", False):
        for track_id, track in (linkedin.get("tracks") or {}).items():
            if not track.get("enabled", False):
                continue
            queries = [q for q in (track.get("queries") or []) if str(q).strip()]
            if queries:
                tracks[track_id] = queries

    covered = {str(q).strip().lower() for queries in tracks.values() for q in queries}
    extra = {}
    for portal, block in matrix.items():
        if portal in NON_PORTAL_KEYS or portal.startswith("_"):
            continue
        if not isinstance(block, dict) or not block.get("enabled", False):
            continue
        for entry in block.get("queries") or []:
            query = entry.get("q") if isinstance(entry, dict) else entry
            query = str(query).strip() if query else ""
            # Keyed on the folded form, so "Machine Learning" (arbeitnow) and
            # "machine learning" (weworkremotely) are one term rather than two.
            # Both were surviving: the `covered` test folded case but the set stored
            # raw strings, so the phrase entered the vocabulary twice and scored
            # twice. On the 2026-08-19 corpus that was ~200 of "Machine Learning
            # Engineer"'s 330 points — an inflation applied to exactly the titles
            # already crowding out the candidate's real profile.
            folded = query.lower()
            if query and folded not in covered and folded not in extra:
                extra[folded] = query

    return tracks, sorted(extra.values())


def job_text(job: dict) -> str:
    """The posting body available pre-enrichment: full text when we have it."""
    return job.get("description") or job.get("description_snippet") or ""


def match_specificity(title: str, queries: list) -> int:
    """Length of the longest vocabulary term this title actually matched.

    The discriminator for attribution ties. `title_match_score` awards +10 for a
    whole-phrase match and +1 per distinct token, so two tracks that each matched one
    generic token tie at 1 and the tie has to be broken on *which* token. Length is
    the usable proxy for specificity: a longer term is a narrower claim. "performance"
    (11) beats "manager" (7), "improvement" (11) beats "manager", "operations" (10)
    beats "manager" — which is exactly the set of misroutings this fixes.
    """
    padded = _words(title)
    best = 0
    for query in queries:
        phrase = _words(query).strip()
        if not phrase:
            continue
        if f" {phrase} " in padded:
            best = max(best, len(phrase))
        for token in phrase.split():
            if len(token) > 2 and f" {token} " in padded:
                best = max(best, len(token))
    return best


def attribute_track(title: str, tracks: dict) -> tuple:
    """(track, hits) — the track whose vocabulary this title matches most strongly.

    None when the title matches no track, so an off-track posting cannot claim a
    reserved floor slot.

    Ties break on the *most specific* matched term, not on dict order. The old rule
    was a strict `>` over matrix-order iteration, so the earliest track won every
    tie — and because "manager" is a token of T3's "AI Product Manager", every
    "... Manager" title in the corpus tied at one hit and fell to T3. T3_ai_product
    became a magnet holding 13 of the 41 alert jobs on 2026-08-19, among them
    "Quality Performance Manager" and "Continuous Improvement Manager" (both T5) and
    "Operations Manager, FC Operations" (T4). The per-track floor distributes against
    this attribution, so the floor was guaranteeing slots to the wrong tracks.

    Track id is the final tiebreak, so one corpus still yields one attribution.
    """
    best = None
    for track_id, queries in tracks.items():
        hits = title_match_score(title, queries)
        if hits <= 0:
            continue
        key = (-hits, -match_specificity(title, queries), track_id)
        if best is None or key < best[0]:
            best = (key, track_id, hits)
    if best is None:
        return None, 0
    return best[1], best[2]


def score_row(job: dict, position: int, tracks: dict, every_query: list,
              model=None) -> tuple:
    """Build a scored row for one job. Selection arithmetic only, never fit.

    Two scoring models live here. `model=None` is the original: count matches of the
    LinkedIn *search* queries, title weighted 10x, description capped. Passing a
    `two_axis_score.ScoringModel` switches to the two-axis model, which scores
    business-domain and technical-enabler categories and rewards their overlap.

    Track attribution is computed the same way under both, because the per-track
    floor is defined against the Profile Tracks and those are the search queries.
    Only the *score* changes.
    """
    title = job.get("title") or ""
    track, best_title_hits = attribute_track(title, tracks)

    if model is not None:
        axes = model.score(title, job_text(job))
        # title_hits/desc_hits keep their slots so every consumer of the tuple —
        # collapse_near_duplicates, select, the report — is unchanged. Under the
        # two-axis model they carry category counts rather than token counts, which
        # is what the axis log in `prerank` spells out in full.
        return (axes["score"], position, track, job,
                len(axes["domain_in_title"]) + len(axes["enabler_in_title"]),
                axes["description_bonus"], axes)

    title_hits = title_match_score(title, every_query)
    desc_hits = min(title_match_score(job_text(job), every_query),
                    MAX_DESCRIPTION_CONTRIBUTION)
    score = title_hits * TITLE_WEIGHT + desc_hits

    return (score, position, track, job, title_hits, desc_hits, None)


def has_signal(row: tuple) -> bool:
    """Whether this posting matched the scoring vocabulary at all.

    Not the same test as "scores above zero", and conflating the two would mislabel
    jobs under the two-axis model. That model deliberately produces *negative*
    scores: "Machine Learning Engineer" scores −40 because a core-tech title with no
    business domain is pushed down the list. It matched `ai` perfectly well — it is
    ranked last on purpose, which is a different statement from "the vocabulary has
    no word for this job". Recording it as no-signal would put a false reason in the
    deferred list and hide the penalty the axis log exists to show.

    Under the original model a score of 0 does mean no match, since every weight
    there is positive.
    """
    axes = row[AXES]
    if axes is None:
        return row[SCORE] > 0
    return bool(axes["domain_matched"] or axes["enabler_matched"])


def annotate(row: tuple, selected: bool, reason: str, gates: dict = None) -> None:
    """Record on the job itself why it was or was not deep-ranked.

    Under the two-axis model the axis breakdown is recorded too. That is not
    diagnostics for its own sake: the weights are a first pass, and retuning them
    without a per-job record of which categories fired and whether the core-tech
    penalty applied would be guesswork. The report renders these columns.

    `gates` is the `hard_gates.evaluate` block, recorded on every gated job and not
    only on the discards. A PASS with `evidence_chars: 0` is not the same statement
    as a PASS on a 6000-char posting, and the enrichment allocator needs to be able
    to tell them apart — so the verdicts travel with the job either way.
    """
    prerank = {
        "score": row[SCORE],
        "track_guess": row[TRACK],
        "title_hits": row[TITLE_HITS],
        "description_hits": row[DESC_HITS],
        "selected": selected,
        "reason": reason,
    }
    axes = row[AXES]
    if axes is not None:
        prerank["model"] = "two_axis"
        prerank["domain_matched"] = axes["domain_matched"]
        prerank["enabler_matched"] = axes["enabler_matched"]
        prerank["domain_in_title"] = axes["domain_in_title"]
        prerank["enabler_in_title"] = axes["enabler_in_title"]
        prerank["domain_from_description"] = axes["domain_from_description"]
        prerank["enabler_from_description"] = axes["enabler_from_description"]
        prerank["overlap_pairs"] = axes["overlap_pairs"]
        # The pair count and the points it bought. Both, because the weight is under
        # review: `overlap_pairs` alone means re-deriving the contribution from a
        # weight that may have moved since the run, which is the comparison the
        # review needs to avoid.
        prerank["overlap_bonus"] = axes["overlap_bonus"]
        prerank["description_bonus"] = axes["description_bonus"]
        prerank["core_tech_penalty"] = axes["core_tech_penalty"]
        prerank["core_tech_marker"] = axes["core_tech_marker"]
        prerank["hidden_hybrid_bonus"] = axes["hidden_hybrid_bonus"]
        prerank["hybrid_tier"] = axes["hybrid_tier"]
        prerank["hybrid_tier_bonus"] = axes["hybrid_tier_bonus"]
        prerank["core_tech_exempt"] = axes["core_tech_exempt"]
    if gates is not None:
        prerank["gates"] = gates
    row[JOB]["prerank"] = prerank


def gate_reason(verdict: dict) -> str:
    """The human-readable discard reason, with the posting's own words in it.

    A gate that says only "failed the experience filter" is unauditable — the whole
    argument for moving these checks out of the model and into Python is that they
    become checkable, and that means quoting the sentence the decision was made on.
    Six of the 2026-08-19 discards were correct; the seventh being wrong would be
    invisible without the quote.
    """
    parts = []
    for name in verdict["failed"]:
        block = verdict[name]
        detail = block.get("reason") or name
        quote = block.get("quote")
        parts.append(f"{name}: {detail}" + (f' — "{quote.strip()}"' if quote else ""))
    return "hard gate — " + "; ".join(parts)


def annotate_bare(job: dict, selected: bool, reason: str) -> None:
    """Annotate a job that was excluded before it was ever scored."""
    job["prerank"] = {"score": 0, "track_guess": None, "title_hits": 0,
                      "description_hits": 0, "selected": selected, "reason": reason}


def role_signature(title: str) -> frozenset:
    """The set of tokens that identify the *role*, with grade and noise removed.

    Exact title keying collapsed TOMRA's three identical listings but not Deloitte
    Geneva's "Senior Manager - AI & Data (AI Strategy)" and "Manager - AI & Data - AI
    Strategy", which are one opening at two grades and took two of the 25 slots. What
    survives here is the role: grade words, joining words, requisition refs and the
    "(m/f/d)" diversity tag are dropped, and the abbreviations are normalised so "AI"
    and "Artificial Intelligence" are one token.

    A title made *entirely* of stripped words falls back to its unstripped tokens.
    Otherwise "Senior Associate" would reduce to the empty set and match every other
    fully-stripped title at the same company.
    """
    text = _words(title)
    for phrase, short in TITLE_ABBREVIATIONS:
        text = text.replace(f" {phrase} ", f" {short} ")
    tokens = [t for t in text.split() if t not in TITLE_STOPWORDS]
    role = frozenset(t for t in tokens
                     if t not in GRADE_WORDS and not _REF.match(t)
                     and len(t) > _TAG_LETTERS)
    return role or frozenset(tokens)


def signature_similarity(left: frozenset, right: frozenset) -> int:
    """Token-set overlap as an integer percent (Jaccard).

    A set rather than a string comparison because word *order* is not information
    here: "AI & Data - Manager, AI Strategy" and "Manager - AI & Data (AI Strategy)"
    are the same role advertised by two people with different house styles, and any
    key built on sequence reads them as different.
    """
    if not left or not right:
        return 0
    return 100 * len(left & right) // len(left | right)


def collapse_near_duplicates(rows: list, alert_ids: set, enabled: bool = True) -> tuple:
    """Keep one row per (company, role); return (keepers, collapsed).

    One opening routinely enters the corpus several times under different URLs, so
    `aggregate_jobs.py` cannot dedup it — the keys are genuinely distinct. On the
    2026-08-18 corpus that cost 4 of 15 deep-rank slots: TOMRA's "Machine Learning
    Engineer" appeared three times and sennder's twice. Scoring the same role at
    the same company twice returns the same verdict for another 68 seconds.

    Matching is on `role_signature` similarity rather than exact title equality,
    because exact equality missed the case it most needed to catch: Deloitte Geneva's
    two "AI & Data - AI Strategy" postings differ only by grade and each took a
    top-25 slot on 2026-08-19. Grouping is greedy against the first row of each group
    in keeper-preference order, never chained — similarity is not transitive, and
    letting A-matches-B and B-matches-C merge A with C would drift a group away from
    the role it started as.

    Location is deliberately NOT part of the key. The strings are too unreliable
    to join on — TOMRA's three rows read "Mülheim-Kärlich, RP, de", the same again,
    and "Mülheim-Kärlich, RP, Germany", while Deutsche Telekom lists one
    requisition (its ref is in the title) across four Hungarian cities. Keying on
    location would fail to collapse any of those. The cost is that a role genuinely
    open in two cities collapses to one slot, which is why every sibling's location
    and URL is recorded on the keeper as `also_posted` and every collapsed row
    keeps its own deferred entry.

    Keeper preference: alert-matched first (it carries the wider document gate),
    then higher score, then earliest aggregation position for stability. The keeper
    is therefore also the group's representative, so a lower-graded duplicate never
    displaces the posting that scored better.

    `enabled=False` returns every row untouched — see `preference_key` for why the
    slot fixes are held behind the same switch as the scoring model.
    """
    if not enabled:
        return list(rows), []

    # Preference order first, so the row each group forms around is its keeper.
    ordered = sorted(rows, key=lambda row: (0 if id(row[JOB]) in alert_ids else 1,
                                            -row[SCORE], row[POSITION]))
    companies = {}
    for row in ordered:
        job = row[JOB]
        company = _words(job.get("company") or "")
        signature = role_signature(job.get("title") or "")
        for known, members in companies.setdefault(company, []):
            if signature_similarity(known, signature) >= SIMILARITY_THRESHOLD:
                members.append(row)
                break
        else:
            companies[company].append((signature, [row]))

    keepers, collapsed = [], []
    for groups in companies.values():
        for _, group in groups:
            keeper, siblings = group[0], group[1:]
            keepers.append(keeper)
            if not siblings:
                continue
            keeper[JOB]["prerank_also_posted"] = [
                {"location": row[JOB].get("location"), "url": row[JOB].get("url"),
                 "title": row[JOB].get("title")}
                for row in siblings]
            for row in siblings:
                # The keeper's title, recorded on the sibling, so a collapse that
                # merged two differently-worded titles can be checked rather than
                # taken on trust — the whole risk of similarity matching is a merge
                # nobody can see afterwards.
                row[JOB]["prerank_duplicate_of"] = keeper[JOB].get("title")
            collapsed.extend(siblings)

    # Back to corpus order. The cut re-sorts by `preference_key` anyway, but a
    # stable, position-ordered return keeps the deferred list readable and makes
    # the function's output independent of how the groups happened to form.
    keepers.sort(key=lambda row: row[POSITION])
    collapsed.sort(key=lambda row: row[POSITION])
    return keepers, collapsed


def _recency_key(job: dict) -> int:
    """The posting date as a comparable integer: higher is newer, 0 is unknown.

    `date_posted` arrives ISO 8601 with a timezone on LinkedIn
    ("2026-08-19T08:55:16.000Z") and as a bare date on other portals. Keeping only
    the digits and left-aligning to 14 places makes both forms comparable —
    20260819085516 and 20260819000000 — and a bare date sorts as midnight, i.e.
    older than any timestamped posting from the same day, which is the honest
    reading of "we don't know the hour".

    An integer rather than the string, so `-recency` can be used directly in the
    sort key: a real date becomes a large negative number and sorts ahead of a
    missing one, which stays 0 and lands last. Sorting undated postings *first*
    would hand every tie to whichever portal omits the field.
    """
    digits = "".join(c for c in str(job.get("date_posted") or "") if c.isdigit())
    return int(digits[:14].ljust(14, "0")) if digits else 0


def preference_key(row: tuple, alert_ids: set, tie_breaks: bool = True) -> tuple:
    """Sort key for the cut: score, then the C8 tie-breaks, then position.

    The tie-breaks matter more than they sound. Under the two-axis model the
    domain-only jobs cluster into flat bands — on the 2026-08-19 corpus 13 of the 41
    alert jobs scored exactly 30 — so with score alone the order was decided by
    aggregation position, i.e. by which portal happened to answer first. That is not
    a preference, it is an artifact. Order within a band is now:

      1. alert-matched first: LinkedIn's own matching is real information
      2. Hungary or remote-EU next: no new work permit needed (the candidate holds
         a Hungarian permit; everything else is FLAGged for sponsorship, never
         dropped, so this orders rather than excludes)
      3. most recently posted next: a fresher posting is likelier to still be open
      4. aggregation position last, so one corpus still yields one selection

    `tie_breaks=False` restores the pre-C8 key of score-then-position. The
    tie-breaks and the near-duplicate collapse are genuine fixes, but they change
    which jobs win slots — replaying the archived 2026-08-19 run with C8 on and the
    old scoring model shares only 12 of its 25 top slots. So they ride the same
    switch as the scoring model rather than reaching the 08:00 job a day early on a
    different question's approval: `main` computes `slot_fixes = model is not None`,
    so `scoring.enabled` in the matrix (or `--two-axis`) turns all three on together
    and there is nothing separate to flip here.
    """
    job = row[JOB]
    if not tie_breaks:
        return (-row[SCORE], row[POSITION])
    location = (job.get("location") or "").lower()
    remote = "remote" in location or bool(job.get("remote"))
    no_permit_needed = "hungary" in location or "budapest" in location or remote
    return (-row[SCORE],
            0 if id(job) in alert_ids else 1,
            0 if no_permit_needed else 1,
            -_recency_key(job),
            row[POSITION])


def select(rows: list, budget: int, per_track_floor: int, alert_ids=None,
           tie_breaks: bool = True) -> tuple:
    """Split scored rows into (chosen, deferred, stats).

    Claims on the budget, in order:
      1. up to `per_track_floor` per *attributed* track, best first — the
         anti-monopoly rule
      2. whatever budget remains, by score descending

    Only rows with a real track attribution can take a floor slot. A row whose
    title matched no track has no track to protect, and letting it into the
    reserved pass is actively harmful: on the 2026-08-18 corpus an untracked
    bucket handed free slots to "Procurement Counsel" and "Account Executive,
    LATAM" — a lawyer and a salesperson — while 448 scored jobs were deferred.
    Untracked rows still compete normally in pass 2 on score.

    Ties break through `preference_key`, so one corpus always yields one selection;
    a non-deterministic cut would make a bad morning unreproducible.
    """
    alert_ids = alert_ids or set()
    stats = {"floor_slots": 0, "score_slots": 0}
    by_score = sorted(rows, key=lambda row: preference_key(row, alert_ids, tie_breaks))

    chosen, chosen_ids = [], set()

    if per_track_floor > 0:
        taken = {}
        for row in by_score:
            if len(chosen) >= budget:
                break
            track = row[TRACK]
            if track is None or taken.get(track, 0) >= per_track_floor:
                continue
            taken[track] = taken.get(track, 0) + 1
            chosen_ids.add(id(row[JOB]))
            chosen.append(row)
            stats["floor_slots"] += 1

    for row in by_score:
        if len(chosen) >= budget:
            break
        if id(row[JOB]) in chosen_ids:
            continue
        chosen_ids.add(id(row[JOB]))
        chosen.append(row)
        stats["score_slots"] += 1

    deferred = [row for row in by_score if id(row[JOB]) not in chosen_ids]

    # Best first, so the ranker reads the strongest candidates even if cut short.
    chosen.sort(key=lambda row: preference_key(row, alert_ids, tie_breaks))
    return chosen, deferred, stats


def propagate_to_corpus(corpus: dict, jobs: list, warn) -> int:
    """Copy this stage's verdicts back onto the corpus, joined on `dedup_key`.

    Two stages means two files disagreeing unless they are reconciled. After the
    shortlist stage the corpus says `selected: true, "top of the shortlist"` for 80
    jobs; the final stage then keeps 25 of them, and the report reads the corpus. So
    the 55 it cut have to be told, or the funnel prints 559 -> 80 with 25 ranked and
    no account of where the other 55 went.

    `description` travels too: enrichment wrote the full body onto the shortlist copy
    of the job, and it is the corpus that Phase 2 and the report read afterwards.

    Jobs with no `dedup_key` cannot be joined. That is a real gap rather than a
    tolerable one — an unjoined job keeps the shortlist stage's stale verdict — so
    it is counted and warned about rather than passed over.
    """
    by_key = {job["dedup_key"]: job for job in jobs
              if isinstance(job, dict) and job.get("dedup_key")}
    unkeyed = sum(1 for job in jobs
                  if isinstance(job, dict) and not job.get("dedup_key"))
    if unkeyed:
        warn(f"{unkeyed} shortlisted jobs have no dedup_key and could not be joined "
             "back onto the corpus; their corpus entries keep the shortlist verdict")

    joined = 0
    for entry in corpus["results"]:
        if not isinstance(entry, dict):
            continue
        source = by_key.get(entry.get("dedup_key"))
        if source is None:
            continue
        for field in ("prerank", "prerank_also_posted", "description"):
            if field in source:
                entry[field] = source[field]
        joined += 1

    missing = len(by_key) - joined
    if missing > 0:
        warn(f"{missing} shortlisted jobs were not found in the corpus by dedup_key — "
             "the corpus and the shortlist may be from different runs")
    return joined


def deferred_entry(job: dict) -> dict:
    """A deferred job trimmed to what the report's table renders.

    The gate verdicts appear as two compact fields rather than the full block: the
    quoted wording a discard was made on is already inside `reason`, and 500 deferred
    jobs × three nested verdict dicts would bury it. The full block stays on the job
    in the corpus for anything that needs to read the evidence itself.
    """
    prerank = job.get("prerank") or {}
    gates = prerank.get("gates") or {}
    return {
        "key": job.get("dedup_key"),
        "title": job.get("title"),
        "company": job.get("company"),
        "url": job.get("url"),
        "location": job.get("location"),
        "portal": job.get("portal"),
        "prerank_score": prerank.get("score"),
        "track_guess": prerank.get("track_guess"),
        "reason": prerank.get("reason"),
        "gate_overall": gates.get("overall"),
        "gate_failed": gates.get("failed"),
    }


def load_json(path: Path, what: str, warn, required=False):
    if not path.is_file():
        if required:
            print(f"Error: {what} not found at {path}", file=sys.stderr)
        return None
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        warn(f"could not read {what} at {path}: {exc}")
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=Path, required=True,
                        help="aggregate_jobs.py output. Annotated in place.")
    parser.add_argument("--rankset", type=Path, required=True,
                        help="Where to write the jobs Phase 2 should score.")
    parser.add_argument("--deferred", type=Path, default=None,
                        help="Where to write everything the cut excluded.")
    parser.add_argument("--stage", choices=("final", "shortlist"), default="final",
                        help="`shortlist` cuts wide to prerank.shortlist_budget, for "
                             "Phase 1c enrichment to read bodies for; `final` (the "
                             "default) cuts to prerank.deep_rank_budget.")
    parser.add_argument("--corpus", type=Path, default=None,
                        help="The full corpus, when --jobs is a shortlist. This stage's "
                             "annotations are joined back onto it by dedup_key and the "
                             "deferred list is derived from it, so one file still "
                             "accounts for every fetched job.")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX,
                        help="Search matrix: supplies vocabulary and budgets.")
    parser.add_argument("--seen", type=Path, default=DEFAULT_SEEN,
                        help="seen_jobs.json; an already-ranked job costs no slot.")
    parser.add_argument("--alerts", type=Path, default=DEFAULT_ALERTS,
                        help="alert_matched.json. Missing is normal (no Phase 6 yet).")
    parser.add_argument("--budget", type=int, default=None,
                        help="Override prerank.deep_rank_budget.")
    parser.add_argument("--shortlist-budget", type=int, default=None,
                        help="Override prerank.shortlist_budget (--stage shortlist).")
    parser.add_argument("--alert-budget", type=int, default=None,
                        help="Override prerank.alert_budget.")
    parser.add_argument("--per-track-floor", type=int, default=None,
                        help="Override prerank.per_track_floor.")
    parser.add_argument("--expiry-days", type=int, default=_gate.EXPIRY_DAYS,
                        help="An alert older than this stops reserving a slot.")
    parser.add_argument("--today", default=None, help="YYYY-MM-DD; drives alert expiry.")
    model_flags = parser.add_mutually_exclusive_group()
    model_flags.add_argument("--two-axis", dest="two_axis", action="store_true",
                             default=None,
                             help="Force the two-axis domain/enabler scoring model on, "
                                  "whatever scoring.enabled says in the matrix.")
    model_flags.add_argument("--no-two-axis", dest="two_axis", action="store_false",
                             help="Force the original query-match scoring model, "
                                  "whatever scoring.enabled says in the matrix.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report the selection without writing anything.")
    args = parser.parse_args()

    def warn(message):
        print(f"  prerank: {message}", file=sys.stderr)

    try:
        today = (datetime.strptime(args.today, "%Y-%m-%d").date()
                 if args.today else date.today())
    except ValueError:
        print(f"Error: --today must be YYYY-MM-DD, got {args.today!r}", file=sys.stderr)
        return 1

    data = load_json(args.jobs, "the jobs file", warn, required=True)
    if data is None:
        return 1
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        print(f"Error: {args.jobs} has no `results` array — is it aggregate_jobs.py "
              "output?", file=sys.stderr)
        return 1
    jobs = data["results"]

    matrix = load_json(args.matrix, "the search matrix", warn, required=True)
    if matrix is None:
        return 1

    cfg = matrix.get("prerank") or {}

    def budget_for(override, key, default):
        if override is not None:
            return max(0, override)
        try:
            return max(0, int(cfg.get(key, default)))
        except (TypeError, ValueError):
            warn(f"prerank.{key} is not a number — falling back to {default}")
            return default

    shortlisting = args.stage == "shortlist"
    if shortlisting:
        budget = budget_for(args.shortlist_budget, "shortlist_budget",
                            DEFAULT_SHORTLIST_BUDGET)
        # The shortlist alert cap is derived, not configured, because the number that
        # matters is not "how many alerts" but "how many non-alert jobs survive to
        # reach the final cut". The final stage caps alerts at `alert_budget` and then
        # fills `deep_rank_budget - alert_budget` slots from the non-alert pool — so
        # the shortlist has to hand it at least that many non-alert jobs, and every
        # slot above `budget - (deep_rank - alert)` spent on an alert is a slot the
        # final cut cannot fill from anything.
        #
        # This is what the 2026-08-22 live run cost. 97 live alert keys, no cap here,
        # 75 of 80 shortlist slots to alerts, 5 non-alert jobs handed to a stage
        # wanting 15 — a 25-slot deep-rank set that filled 15 while non-alert jobs
        # scoring up to 135 sat in the deferred list behind alert jobs scoring 30.
        # The earlier reasoning for no cap was sound about enrichment and silent about
        # this; both constraints are real, so the cap is set as high as the final cut
        # can tolerate rather than down at `alert_budget`.
        final_budget = budget_for(None, "deep_rank_budget", DEFAULT_DEEP_RANK_BUDGET)
        final_alerts = budget_for(None, "alert_budget", DEFAULT_ALERT_BUDGET)
        derived_cap = max(0, budget - max(0, final_budget - final_alerts))
        alert_budget = (max(0, args.alert_budget)
                        if args.alert_budget is not None else derived_cap)
    else:
        budget = budget_for(args.budget, "deep_rank_budget", DEFAULT_DEEP_RANK_BUDGET)
        alert_budget = budget_for(args.alert_budget, "alert_budget",
                                  DEFAULT_ALERT_BUDGET)
    per_track_floor = budget_for(args.per_track_floor, "per_track_floor",
                                 DEFAULT_PER_TRACK_FLOOR)

    # The corpus, when --jobs is a shortlist rather than everything fetched. Loaded
    # before any scoring so a bad path fails the run instead of losing the join
    # after the work is done.
    corpus = None
    if args.corpus:
        if shortlisting:
            print("Error: --corpus is for --stage final, where --jobs is a shortlist. "
                  "The shortlist stage already reads the corpus as --jobs.",
                  file=sys.stderr)
            return 1
        corpus = load_json(args.corpus, "the corpus", warn, required=True)
        if corpus is None:
            return 1
        if not isinstance(corpus, dict) or not isinstance(corpus.get("results"), list):
            print(f"Error: {args.corpus} has no `results` array — is it "
                  "aggregate_jobs.py output?", file=sys.stderr)
            return 1

    tracks, extra_vocab = build_vocabulary(matrix)
    if not tracks and not extra_vocab:
        print("Error: no enabled queries in the matrix, so there is no vocabulary to "
              "pre-rank against. Selecting 15 of 500 jobs arbitrarily would be worse "
              "than not selecting at all.", file=sys.stderr)
        return 1
    if not tracks:
        warn("no enabled LinkedIn tracks, so no job can be attributed to a track and "
             "the per-track floor is inactive — selection is a plain top-N by score")
    every_query = [q for queries in tracks.values() for q in queries] + extra_vocab

    # A malformed `scoring` block is fatal rather than a silent fallback. Scoring
    # every job 0 looks exactly like a thin market, and this phase exists to make the
    # funnel visible.
    try:
        model = _two_axis.ScoringModel.from_matrix(matrix, force=args.two_axis)
    except ValueError as exc:
        print(f"Error: {args.matrix} `scoring` block is unusable: {exc}",
              file=sys.stderr)
        return 1
    if model is None:
        no_signal_reason = "title and description match no track vocabulary"
    else:
        no_signal_reason = "title and description match no scoring category"
        warn(f"scoring model: two-axis ({len(model.domain)} domain categories, "
             f"{len(model.enabler)} enabler categories); scores are not comparable "
             "with earlier runs")

    seen_keys = set()
    seen = load_json(args.seen, "seen_jobs.json", warn)
    if isinstance(seen, dict) and isinstance(seen.get("seen"), dict):
        seen_keys = set(seen["seen"])
    elif seen is not None:
        warn(f"{args.seen} is not a seen-jobs file; every job will look new")

    store = load_json(args.alerts, "alert_matched.json", warn) or {}
    live_alerts, alert_stats = _gate.live_alert_keys(store, today,
                                                    args.expiry_days, warn)

    stats = {"total": len(jobs), "malformed": 0, "already_seen": 0, "no_signal": 0,
             "near_duplicates": 0, "alert_selected": 0, "alert_over_budget": 0,
             "gate_failed": 0, "gate_unknown": 0,
             "gate_failed_language": 0, "gate_failed_experience": 0,
             "gate_failed_seniority": 0, "gate_failed_pure_technical": 0}

    # The gates run only under the two-axis model, for the same reason the slot fixes
    # do (see `preference_key`), and because `pure_technical_verdict` reads the axis
    # result — under the old model it has nothing to classify from.
    gating = model is not None
    min_body_domains = (model.weights["core_tech_exempt_min_body_domains"]
                        if gating else 2)
    # Keyed by id(job) because a scored row is a tuple and the gate verdict has to
    # survive the later `annotate` calls, which rewrite `prerank` wholesale. Held here
    # rather than on the job so no private key leaks into the output JSON.
    gate_log = {}

    def record(row: tuple, selected: bool, reason: str) -> None:
        """`annotate`, with this job's gate verdict reattached."""
        annotate(row, selected, reason, gate_log.get(id(row[JOB])))

    candidates, alert_ids = [], set()
    for position, job in enumerate(jobs):
        if not isinstance(job, dict):
            stats["malformed"] += 1
            continue
        key = job.get("dedup_key") or ""

        if key and key in seen_keys:
            stats["already_seen"] += 1
            annotate_bare(job, False, "already ranked in a previous run")
            continue

        row = score_row(job, position, tracks, every_query, model=model)
        alerted = bool(key) and key in live_alerts

        # The hard gates run before every budget, and they override the alert
        # exemption below. That is the whole point of moving them here: on the
        # 2026-08-19 corpus six alert-matched jobs each took one of the 25 deep-rank
        # slots and one of the 15 enrichment requests, and were then discarded by the
        # model on wording that was already in the text at this stage. LinkedIn
        # surfacing a job is real information about relevance; it is not information
        # about whether the posting demands fluent Hungarian or eight years.
        gates = None
        if gating:
            gates = _gates.evaluate(job, row[AXES], min_body_domains)
            gate_log[id(job)] = gates
            if gates["overall"] == _gates.FAIL:
                stats["gate_failed"] += 1
                for name in gates["failed"]:
                    stats[f"gate_failed_{name}"] += 1
                record(row, False, gate_reason(gates))
                continue
            if gates["overall"] == _gates.UNKNOWN:
                stats["gate_unknown"] += 1

        # An alert-matched job is kept whatever it scores: LinkedIn surfaced it, so
        # the vocabulary failing to describe it is a gap in the vocabulary.
        if not alerted and not has_signal(row):
            stats["no_signal"] += 1
            record(row, False, no_signal_reason)
            continue

        if alerted:
            alert_ids.add(id(job))
        candidates.append(row)

    if stats["gate_failed"]:
        warn(f"{stats['gate_failed']} jobs failed a hard gate before ranking "
             f"({stats['gate_failed_language']} language, "
             f"{stats['gate_failed_experience']} experience, "
             f"{stats['gate_failed_seniority']} seniority, "
             f"{stats['gate_failed_pure_technical']} pure-technical). Each carries the "
             "quoted wording it was discarded on in its deferred entry.")
    if stats["gate_unknown"]:
        warn(f"{stats['gate_unknown']} jobs could not be gated on the text available "
             "and are UNKNOWN, not passed — their risk is unverified and they are the "
             "jobs enrichment should be spent on")

    if stats["malformed"]:
        warn(f"{stats['malformed']} entries in {args.jobs} were not objects and were "
             "skipped — the corpus may be truncated or corrupt")

    # Collapse before the budget is divided, so no slot is spent twice on one role.
    # Held behind the scoring switch: see `preference_key` for why.
    slot_fixes = model is not None
    keepers, collapsed = collapse_near_duplicates(candidates, alert_ids, slot_fixes)
    for row in collapsed:
        stats["near_duplicates"] += 1
        # Name the keeper. Matching is on role similarity now, not on an exact
        # title, so "same title, different URL" would be a false account of why a
        # differently-worded posting was dropped — and the only way to audit a
        # similarity merge is to be able to read both titles side by side.
        record(row, False, "near-duplicate of \"%s\" at the same company"
                           % (row[JOB].get("prerank_duplicate_of") or "another "
                              "selected posting"))
    if collapsed:
        warn(f"{len(collapsed)} near-duplicate postings collapsed so the deep-rank "
             "budget is not spent twice on one role; each is in the deferred list "
             "naming the posting that absorbed it, and every sibling's title and "
             "location is recorded on the posting that was kept")

    alert_rows = [row for row in keepers if id(row[JOB]) in alert_ids]
    scored = [row for row in keepers if id(row[JOB]) not in alert_ids]

    # Alert-matched jobs claim their slots before the score cut runs; what is left
    # of the deep-rank budget goes to the scored pool. The full tie-break applies
    # here too, and it decides real outcomes: 13 of the 41 alert jobs on the
    # 2026-08-19 corpus score exactly 30 under the two-axis model, so with score
    # alone the 10 reserved slots would be handed out by aggregation order.
    alert_rows.sort(key=lambda row: preference_key(row, alert_ids, slot_fixes))
    alert_selected = alert_rows[:min(alert_budget, budget)]
    for row in alert_selected:
        stats["alert_selected"] += 1
        record(row, True, "LinkedIn alert-matched")
    cut_alerts = alert_rows[len(alert_selected):]
    for row in cut_alerts:
        stats["alert_over_budget"] += 1
        record(row, False,
               f"alert-matched but over the alert budget of {alert_budget}")
    if cut_alerts:
        # The score range, not just the count. A cap that only ever discards jobs
        # scoring 30 is doing what it was added for; a cap discarding a 130 is
        # costing a real opportunity and nothing else in the run would say so. Both
        # ends and the median, because one outlier and a systematically high band are
        # different problems: the first argues for a backfill rule, the second for a
        # bigger shortlist.
        cut_scores = sorted(row[SCORE] for row in cut_alerts)
        best = max(cut_alerts, key=lambda row: row[SCORE])
        stats["alert_over_budget_scores"] = {
            "min": cut_scores[0],
            "max": cut_scores[-1],
            "median": cut_scores[len(cut_scores) // 2],
            "best_title": best[JOB].get("title"),
            "best_company": best[JOB].get("company"),
        }
        warn(f"{stats['alert_over_budget']} alert-matched jobs exceeded the alert "
             f"budget of {alert_budget} and were deferred (scores "
             f"{cut_scores[0]}-{cut_scores[-1]}, median "
             f"{cut_scores[len(cut_scores) // 2]}; best was "
             f"\"{best[JOB].get('title')}\" at {best[JOB].get('company')} on "
             f"{best[SCORE]})")

    remaining = max(0, budget - len(alert_selected))
    chosen, deferred_rows, select_stats = select(scored, remaining, per_track_floor,
                                                alert_ids, slot_fixes)

    for row in chosen:
        record(row, True, "top of the shortlist" if shortlisting
                          else "top of the pre-rank")
    for row in deferred_rows:
        record(row, False, f"below the {'shortlist' if shortlisting else 'deep-rank'} "
                           f"budget of {budget}")
    if deferred_rows:
        warn(f"{len(deferred_rows)} scored jobs were not "
             f"{'shortlisted' if shortlisting else 'deep-ranked'} (budget {budget}). "
             "They are in the deferred list and the report; raise "
             f"prerank.{'shortlist_budget' if shortlisting else 'deep_rank_budget'} "
             "to evaluate more.")

    # Said here rather than left for the final stage to discover, because here it is
    # still actionable: the shortlist is what enrichment reads, so a thin non-alert
    # pool means the requests are about to be spent on a set that cannot fill the
    # rankset. The cap above prevents the alert flood causing this; a genuinely thin
    # corpus can still cause it, and the two look identical downstream.
    if shortlisting:
        need = max(0, final_budget - final_alerts)
        if len(chosen) < need:
            # Which of the two causes it is, named explicitly. They call for opposite
            # fixes — crowding means the cap is still too high, a thin corpus means
            # the searches found too little — and the numbers to tell them apart are
            # only in scope here.
            cause = (f"even with the alert cap at {alert_budget}, which was fully used"
                     if len(alert_selected) >= alert_budget else
                     f"and alert crowding is not the cause: only {len(alert_selected)} "
                     f"of the {alert_budget} capped alert slots were used, so the "
                     "non-alert corpus is genuinely thin")
            warn(f"the shortlist holds only {len(chosen)} non-alert jobs but the final "
                 f"cut needs {need} of them (deep_rank_budget {final_budget} minus "
                 f"alert_budget {final_alerts}), so the rankset will be underfilled "
                 f"{cause}")

    selected_jobs = [row[JOB] for row in alert_selected] + [row[JOB] for row in chosen]

    # Where the funnel is accounted for. With --corpus, this stage's verdicts replace
    # the shortlist stage's on the jobs it saw, and every job the shortlist already
    # cut keeps the reason it was cut for — so the deferred list covers all of them
    # rather than only the shortlist's 80.
    joined = 0
    if corpus is not None:
        joined = propagate_to_corpus(corpus, jobs, warn)
    accounting = corpus["results"] if corpus is not None else jobs
    deferred_jobs = [job for job in accounting
                     if isinstance(job, dict)
                     and not (job.get("prerank") or {}).get("selected")]

    summary = {**stats, **select_stats,
               "selected": len(selected_jobs),
               "deferred": len(deferred_jobs),
               # Which cut this is. `total` counts what this stage was handed, which
               # for the final stage is the shortlist, not the corpus — so the report
               # needs the stage to read the funnel correctly.
               "stage": args.stage,
               "budget": budget,
               "alert_budget": alert_budget,
               "per_track_floor": per_track_floor,
               "alert_live": alert_stats["alert_live"],
               "alert_expired": alert_stats["alert_expired"],
               "tracks": sorted(tracks),
               "extra_vocabulary": len(extra_vocab),
               # Which model produced these scores. Two runs' numbers are only
               # comparable when this agrees, and the before/after report has to be
               # able to say which one it is reading rather than infer it.
               "scoring_model": "two_axis" if model else "query_match"}
    if corpus is not None:
        summary["corpus_total"] = len(corpus["results"])
        summary["corpus_joined"] = joined

    # What the shortlist stage actually hands the final one. `alert_budget` says what
    # the cap was; this says whether it worked. The final cut needs
    # `deep_rank_budget - alert_budget` non-alert jobs and can fill only as many as
    # arrive, so a shortlist reporting fewer than that predicts an underfilled
    # rankset before Phase 1c spends a single request — which on 2026-08-22 nothing
    # did, and the run finished 10 jobs short with no line anywhere saying why.
    if shortlisting:
        summary["non_alert_selected"] = len(chosen)
        summary["non_alert_needed_downstream"] = max(0, final_budget - final_alerts)
        summary["shortlist_alert_cap_derived"] = derived_cap

    # How much of the selected set's score came from the uncapped overlap term. The
    # per-job figure is on every job's `prerank`; this is the aggregate that answers
    # the question the weight is under review for, without anyone having to open the
    # rankset and add it up. Only meaningful under the two-axis model.
    if model and selected_jobs:
        pts = sum((job.get("prerank") or {}).get("overlap_bonus") or 0
                  for job in selected_jobs)
        total = sum(max(0, (job.get("prerank") or {}).get("score") or 0)
                    for job in selected_jobs)
        summary["overlap_bonus_points"] = pts
        summary["overlap_bonus_share_pct"] = round(100 * pts / total, 1) if total else 0.0

    if not selected_jobs and jobs:
        warn("no job matched any track vocabulary, so nothing will be deep-ranked "
             "and no documents will be generated. This is a pre-rank result, not an "
             "empty market — check that config/search_matrix.json still holds the "
             "expected queries.")

    if args.dry_run:
        for job in selected_jobs:
            p = job["prerank"]
            print(f"  prerank: would rank [{p['score']:>4}] "
                  f"{p['track_guess'] or '?':<16} {job.get('title')} "
                  f"@ {job.get('company')}", file=sys.stderr)
        print(json.dumps({**summary, "dry_run": True}))
        return 0

    with open(args.rankset, "w") as handle:
        json.dump({"meta": {**data.get("meta", {}), "prerank": summary},
                   "results": selected_jobs}, handle, indent=2)
        handle.write("\n")

    with open(args.jobs, "w") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")

    if corpus is not None:
        with open(args.corpus, "w") as handle:
            json.dump(corpus, handle, indent=2)
            handle.write("\n")

    if args.deferred:
        with open(args.deferred, "w") as handle:
            json.dump([deferred_entry(j) for j in deferred_jobs], handle, indent=2)
            handle.write("\n")

    print(json.dumps(summary))
    print(f"Pre-rank ({args.stage}): {len(selected_jobs)}/{len(jobs)} selected "
          f"({stats['alert_selected']} alert-matched, {select_stats['floor_slots']} "
          f"by per-track floor, {select_stats['score_slots']} by score); "
          f"{stats['already_seen']} already seen, {stats['no_signal']} no vocabulary "
          f"match, {stats['near_duplicates']} near-duplicates, "
          f"{len(deferred_jobs)} deferred", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
