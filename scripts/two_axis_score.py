#!/usr/bin/env python3
"""Two-axis relevance scoring for Phase 1b pre-ranking.

Why this module exists
----------------------
`prerank_jobs.py` scored each posting against the 13 LinkedIn *search* queries in
`config/search_matrix.json`. Those 13 strings are few because **each one costs a
LinkedIn request** — a correct constraint on discovery, and the wrong vocabulary
for scoring, which costs nothing at all. Reusing one budget as the other conflated
them, and the 2026-08-19 corpus (559 jobs) shows the price:

    Advanced PMO Specialist - Sourcing & Procurement Excellence   score 0, dropped
    Continuous Improvement Manager                                score 20, cut
    Quality Performance Manager                                   score 20, cut
    Machine Learning Engineer                                     score 330, ranked
    Data Scientist / Machine Learning Engineer                    score 450, ranked #1

A 16-450x bias against the candidate's actual profile, because the vocabulary held
no term at all for procurement, continuous improvement, operational excellence,
business process, PMO or program management. 24 of the 25 jobs that reached the
model ranker were pure-tech titles.

The model
---------
Two axes, scored as *categories* rather than titles, so no example role is
hardcoded and a new posting wording is matched by kind:

    Axis A, business domain  : supply_chain procurement process
                               continuous_improvement performance operations
                               program business_analysis
    Axis B, technical enabler: ai automation data_bi digital

    score = W_domain  * |domain categories in title|
          + W_enabler * |enabler categories in title|
          + W_overlap * min(|domain ANYWHERE|, |enabler ANYWHERE|)   <- hybrid bonus
          + W_strong_hybrid, once, when a domain meets ai/automation
          + min(description bonus, cap)
          + W_hidden_hybrid, when the body revealed a hybrid the title concealed
          + core-tech penalty, when a core-tech marker fires with no *role-level* domain

The overlap term is the point. It makes "Business Process Analyst - AI Automation"
outrank "Data Scientist" *structurally*, not by weight tuning, which is what the
candidate profile actually asks for: business impact through AI, not research.

Why overlap reads the whole posting, not just the title
-------------------------------------------------------
The first cut computed overlap from the *title* counts alone, so enrichment could
never create a hybrid: a bare title with one domain and one enabler scored 85,
while a posting whose domain was proven over 6000 characters of body text scored
exactly 45 and was cut. Measured on the 2026-08-19 corpus, **every**
description-only-domain job quantized onto 45, and HARMAN's "Business Analyst
(Supply Chain)" — 5 domain categories, 2 enablers — tied a bare title at 85 with
24 points discarded. Enrich-before-cut and two-axis scoring were undermining each
other. Overlap now reads `domain_matched` / `enabler_matched`, the combined
evidence, so a description-only match is never structurally weaker than a
title-only one; the title keeps its edge through the per-category weights above.

For the same reason the hidden-hybrid bonus is added **outside** the description
cap. Inside it, the cap swallowed the bonus whenever the body also revealed a
couple of categories — which is precisely the case the bonus exists to reward.

Anchors and weak terms, and why the split is load-bearing
--------------------------------------------------------
The first prototype matched bare words anywhere in the posting. It fired the
`operations` category on Cognite's boilerplate "Cognite **operates** at the
forefront of industrial digitalization" — the old model's disease in new clothes.
Measured over the corpus, `data_bi` was 90% driven by the bare word "data" and
`operations` 100% by "operations"/"operational" appearing in prose.

So each category holds two lists:

    anchors  specific enough to trust anywhere in the posting
             ("supply chain", "process excellence", "business intelligence")
    weak     generic, counted ONLY in the title, where the employer chose the
             word deliberately rather than a copywriter reaching for a synonym
             ("data", "operations", "strategy")
    exclude  phrases that must NOT fire the category, masked out of the text
             before matching. `warehouse` is a real supply-chain anchor and
             "enterprise **data warehouse**" is not a supply-chain job; the
             exclusion is scoped per category so masking it here cannot also
             strip the bare word "data" from the `data_bi` axis.

Bounded description bonus, and why it is not per-hit
----------------------------------------------------
Weighting description text per hit created *portal* bias instead of relevance:
only freehire and weworkremotely supply snippets, so freehire took 15 of the top
25 while being 18% of the corpus, and LinkedIn took 5 while being 66%. Rewarding a
posting for which site it came from is not relevance. The title axis therefore
scores on its own and the body contributes a **bounded bonus**, with a one-off
component for the case that matters most: the description revealing a hybrid the
title concealed.

This module is pure. It reads no files, holds no vocabulary of its own, and writes
nothing — the vocabulary lives in the matrix's `scoring` block so it can be retuned
without a code change, and a malformed block is a hard error rather than a silent
zero.
"""

import re

# Weights for the first pass, approved 2026-08-19. Deliberately round: these are
# first-fit numbers to be retuned once a full run's axis log has been read, not
# fitted constants. Any of them may be overridden from the matrix.
DEFAULT_WEIGHTS = {
    "domain": 30,                 # per domain category found in the title
    "enabler": 20,                # per enabler category found in the title
    "overlap": 35,                # per matched domain/enabler pair: the hybrid bonus
    "core_tech_penalty": -60,     # core-tech marker in title AND no role-level domain
    "description_bonus_cap": 25,  # hard ceiling on everything the body can add
    "description_domain": 8,      # per domain category only the body revealed
    "description_enabler": 5,     # per enabler category only the body revealed
    "hidden_hybrid_bonus": 15,    # the body revealed a hybrid the title concealed
    # The priority hierarchy, expressed as weights rather than as a list of
    # example titles. A business domain paired with AI or automation is the top
    # of the stated target profile; the same domain paired only with data/BI or
    # digital is the tier below it, so it gets the smaller bonus. Both are added
    # once, not per pair, so a verbose posting cannot stack them.
    "strong_hybrid_bonus": 40,    # any domain + (ai | automation)
    "medium_hybrid_bonus": 20,    # any domain + (data_bi | digital), no ai/automation
    # A domain proven only by the body, with no domain word in the title at all,
    # is weaker evidence that the *role* is a business role. Applied to the
    # strong/medium bonus only, so the overlap term stays title/body-neutral.
    "body_only_domain_scale": 70,   # percent
    # Categories that make a domain "business-grade" for the strong/medium tiers
    # and for the core-tech exemption. Names, not titles: retunable in the matrix.
    "core_tech_exempt_min_body_domains": 2,
}

# Which enabler categories count as the top tier of the hierarchy. Kept beside the
# weights rather than hardcoded in `score` so the matrix can retune the tiering
# without a code change.
STRONG_ENABLERS = ("ai", "automation")

# Every non-comment key the `scoring` block may carry, so a typo is reported
# instead of silently ignored. A misspelled weight that scores every job 0 looks
# exactly like a thin market, which is the failure mode this phase exists to make
# visible. Any `_`-prefixed key is prose: that is how the whole matrix carries its
# documentation, and the readers already skip those keys.
_ALLOWED_KEYS = {"enabled", "weights", "domain", "enabler", "core_tech_only",
                 "strong_enablers"}


def normalize(text) -> str:
    """Lowercase, space-pad, and collapse punctuation to spaces.

    `&` and `+` survive because they carry meaning in this vocabulary: "S&OP",
    "FP&A", "C++". Padding both ends lets a phrase test be a plain substring check
    on whole words, so "ai" cannot match inside "maintain" and "bi" cannot match
    inside "ambition".
    """
    return " " + re.sub(r"[^a-z0-9&+]+", " ", str(text or "").lower()).strip() + " "


class ScoringModel:
    """The two-axis vocabulary and weights, loaded from the matrix."""

    __slots__ = ("domain", "enabler", "core_tech", "weights", "strong_enablers")

    def __init__(self, domain: dict, enabler: dict, core_tech: list, weights: dict,
                 strong_enablers=STRONG_ENABLERS):
        self.domain = domain        # {category: (anchors, weak, exclude)}, normalized
        self.enabler = enabler
        self.core_tech = core_tech  # normalized title markers
        self.weights = weights
        self.strong_enablers = frozenset(strong_enablers)

    # ---------------------------------------------------------------- loading

    @classmethod
    def from_matrix(cls, matrix: dict, force=None):
        """Build the model, or return None when two-axis scoring is off.

        `force` overrides the matrix's own `enabled` flag: True turns the model on
        for a run whose config still says off, False turns it off outright. That is
        how the sandbox exercises the new model while `config/search_matrix.json`
        keeps `enabled: false`, so the scheduled 08:00 production run keeps the old
        behaviour until the new one has been reviewed.

        Raises ValueError when scoring is on but the block cannot be used. Silently
        falling back would score every job 0, which reads as an empty market.
        """
        if force is False:
            return None
        block = matrix.get("scoring")
        if not isinstance(block, dict):
            if force is True:
                raise ValueError(
                    "two-axis scoring was requested but config/search_matrix.json "
                    "has no `scoring` block to score against")
            return None
        if force is not True and not block.get("enabled", False):
            return None

        unknown = {k for k in block if not k.startswith("_")} - _ALLOWED_KEYS
        if unknown:
            raise ValueError("unknown key(s) in the matrix `scoring` block: "
                             + ", ".join(sorted(unknown)))

        domain = cls._categories(block.get("domain"), "scoring.domain")
        enabler = cls._categories(block.get("enabler"), "scoring.enabler")
        if not domain or not enabler:
            raise ValueError(
                "two-axis scoring needs both `scoring.domain` and `scoring.enabler` "
                "to be non-empty — with one axis missing every job scores on the "
                "other alone, which is the single-axis bias this model replaces")

        weights = dict(DEFAULT_WEIGHTS)
        for key, value in (block.get("weights") or {}).items():
            if key not in DEFAULT_WEIGHTS:
                raise ValueError(f"unknown weight `scoring.weights.{key}`")
            try:
                weights[key] = int(value)
            except (TypeError, ValueError):
                raise ValueError(f"scoring.weights.{key} must be a whole number, "
                                 f"got {value!r}")
        if weights["description_bonus_cap"] < 0:
            raise ValueError("scoring.weights.description_bonus_cap cannot be "
                             "negative — use 0 to ignore descriptions entirely")

        core = [normalize(m) for m in (block.get("core_tech_only") or [])
                if str(m).strip()]

        strong = block.get("strong_enablers")
        if strong is None:
            strong = STRONG_ENABLERS
        elif not isinstance(strong, list) or not all(isinstance(s, str) for s in strong):
            raise ValueError("scoring.strong_enablers must be a list of enabler "
                             "category names")
        else:
            unknown = set(strong) - set(enabler)
            if unknown:
                raise ValueError(
                    "scoring.strong_enablers names categor(ies) absent from "
                    "scoring.enabler: " + ", ".join(sorted(unknown)))
        return cls(domain, enabler, core, weights, strong)

    @staticmethod
    def _categories(raw, where: str) -> dict:
        """Normalize one axis into {category: (anchors, weak, exclude)}.

        A category may be given as a bare list, which means "all anchors" — the
        common case for a category with no generic single words needing a guard.
        """
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ValueError(f"{where} must be an object of category -> terms")

        out = {}
        for name, spec in raw.items():
            if isinstance(spec, list):
                anchors, weak, exclude = spec, [], []
            elif isinstance(spec, dict):
                # `_`-prefixed keys are prose. That is how the whole matrix carries
                # its documentation, and a category needs it as much as the block
                # does — the `exclude` lists in particular are meaningless without
                # the corpus case that motivated them.
                unknown = ({k for k in spec if not k.startswith("_")}
                           - {"anchors", "weak", "exclude"})
                if unknown:
                    raise ValueError(f"{where}.{name} has unknown key(s): "
                                     + ", ".join(sorted(unknown)))
                anchors = spec.get("anchors") or []
                weak = spec.get("weak") or []
                exclude = spec.get("exclude") or []
            else:
                raise ValueError(f"{where}.{name} must be a list of terms, or an "
                                 "object with `anchors`, `weak` and `exclude`")
            anchors = [normalize(t) for t in anchors if str(t).strip()]
            weak = [normalize(t) for t in weak if str(t).strip()]
            exclude = [normalize(t) for t in exclude if str(t).strip()]
            if not anchors and not weak:
                raise ValueError(f"{where}.{name} has no terms")
            out[name] = (anchors, weak, exclude)
        return out

    # ---------------------------------------------------------------- scoring

    @staticmethod
    def _mask(text: str, exclude: list) -> str:
        """Blank out this category's excluded phrases before matching.

        Masking is per category and per call, never global: "data warehouse" must
        stop firing `supply_chain`, and stripping it from the shared text would
        also delete the bare word "data" that `data_bi` legitimately matches on.
        The replacement keeps the surrounding padding so a phrase test stays a
        whole-word substring check.
        """
        if not exclude:
            return text
        for phrase in exclude:
            if phrase in text:
                text = text.replace(phrase, " ")
        return text

    def _axis_match(self, axis: dict, ntitle: str, nbody: str) -> tuple:
        """(categories in the title, categories only the body revealed).

        Weak terms are tested against the title only — that is the anchor/weak
        split. A category already found in the title is never counted again from
        the body: the title is the stronger evidence, and double-counting would
        reward a verbose posting over a precise one.
        """
        in_title, in_body = set(), set()
        for name, (anchors, weak, exclude) in axis.items():
            title = self._mask(ntitle, exclude)
            if any(t in title for t in anchors) or any(t in title for t in weak):
                in_title.add(name)
            elif any(t in self._mask(nbody, exclude) for t in anchors):
                in_body.add(name)
        return in_title, in_body

    def score(self, title, description="") -> dict:
        """Score one posting. Returns the score plus why, for the axis log.

        The `why` half is not decoration: the weights are a first pass, and
        retuning them without a per-job record of which categories fired would be
        guesswork. Every field here is written through to the job's `prerank`
        object by the caller and rendered in the report.
        """
        ntitle, nbody = normalize(title), normalize(description)
        w = self.weights

        dom_t, dom_b = self._axis_match(self.domain, ntitle, nbody)
        enb_t, enb_b = self._axis_match(self.enabler, ntitle, nbody)
        all_dom, all_enb = dom_t | dom_b, enb_t | enb_b

        score = w["domain"] * len(dom_t) + w["enabler"] * len(enb_t)

        # A core-tech title needs a *role-level* business component to be exempt,
        # not merely an employer that operates in one. "Machine Learning Engineer"
        # at a logistics company must not qualify just because the boilerplate says
        # logistics — so a single passing mention in the body no longer exempts it.
        # Exemption requires a domain word in the TITLE, or several independent
        # domain categories in the body, which is what a genuinely hybrid posting
        # looks like when it describes business process work in its own duties.
        marker = next((m for m in self.core_tech if m in ntitle), None)
        exempt = bool(dom_t) or len(dom_b) >= w["core_tech_exempt_min_body_domains"]
        penalised = bool(marker) and not exempt

        # Every hybrid reward below is premised on the posting carrying a real
        # business domain. When the penalty fires we have just ruled that it does
        # not, so paying the bonuses anyway would refund most of the penalty:
        # sennder's ML Engineer banked overlap + hidden + tier off one boilerplate
        # mention of "logistics" and landed on 46 instead of being demoted.
        hybrid_ok = not penalised

        # Overlap on the COMBINED evidence. Reading title counts only meant a
        # posting that proved its hybrid over 6000 characters scored below a bare
        # two-word title, and enrichment could never promote anything.
        overlap = min(len(all_dom), len(all_enb)) if hybrid_ok else 0
        # Recorded as points, not just as a pair count. The term is deliberately
        # outside `description_bonus_cap` — it is computed on the combined title+body
        # evidence, so nothing bounds it — and on the 2026-08-21 corpus it was 39-44%
        # of each top-5 score and 31% of all points in the top 25. Whether that is
        # too much of the total is a question about real runs, so the share is logged
        # per job and the weight is left alone until there is data to bound it with.
        overlap_bonus = w["overlap"] * overlap
        if overlap:
            score += overlap_bonus

        bonus = 0
        if dom_b or enb_b:
            bonus = (w["description_domain"] * len(dom_b)
                     + w["description_enabler"] * len(enb_b))
        bonus = min(bonus, w["description_bonus_cap"])
        score += bonus

        # Added OUTSIDE the cap, deliberately. The case enrich-before-cut exists
        # to catch is the title reading domain-only or enabler-only and the body
        # proving it is both — and inside the cap that bonus was swallowed exactly
        # when the body was rich enough to prove it. On the 2026-08-19 corpus
        # HARMAN lost all 15 points of it plus 9 points of category bonus this way.
        hidden = 0
        if hybrid_ok and all_dom and all_enb and not (dom_t and enb_t):
            hidden = w["hidden_hybrid_bonus"]
            score += hidden

        # The priority hierarchy. A business domain paired with AI or automation is
        # the top of the target profile; the same domain with only data/BI or
        # digital is the tier below. Awarded once, not per pair, so length cannot
        # buy rank. Scaled down when NO domain word appears in the title at all —
        # body-only domain evidence is weaker evidence about the role itself, which
        # keeps the title's edge without reintroducing the 85/45 cliff.
        tier, tier_bonus = None, 0
        if hybrid_ok and all_dom and all_enb:
            if all_enb & self.strong_enablers:
                tier, tier_bonus = "strong", w["strong_hybrid_bonus"]
            else:
                tier, tier_bonus = "medium", w["medium_hybrid_bonus"]
            if not dom_t:
                tier_bonus = tier_bonus * w["body_only_domain_scale"] // 100
            score += tier_bonus

        if penalised:
            score += w["core_tech_penalty"]

        return {
            "score": int(score),
            "domain_matched": sorted(all_dom),
            "enabler_matched": sorted(all_enb),
            "domain_in_title": sorted(dom_t),
            "enabler_in_title": sorted(enb_t),
            "domain_from_description": sorted(dom_b),
            "enabler_from_description": sorted(enb_b),
            "overlap_pairs": overlap,
            "overlap_bonus": overlap_bonus,
            "description_bonus": bonus,
            "hidden_hybrid_bonus": hidden,
            "hybrid_tier": tier,
            "hybrid_tier_bonus": tier_bonus,
            "core_tech_penalty": penalised,
            "core_tech_marker": marker.strip() if marker else None,
            "core_tech_exempt": bool(marker) and exempt,
        }
