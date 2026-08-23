"""Guards for the two-axis relevance model.

The model replaces a scorer that counted matches against the 13 LinkedIn *search*
queries. Those 13 strings are few for a good reason — each one costs a request — but
reusing them to *score* conflated a request budget with a vocabulary, and the
2026-08-19 corpus showed the price: "Advanced PMO Specialist - Sourcing & Procurement
Excellence" scored a literal 0 while "Machine Learning Engineer" scored 330, because
the vocabulary held no word for procurement, process excellence, continuous
improvement or programme management. 24 of the 25 jobs that reached the ranker were
pure-tech titles.

Every test below names the posting that exposed the behaviour it pins. Three of them
guard regressions found in the *prototypes*, not in production, and those are the ones
most worth keeping:

  1. Weak terms fired on prose. The first prototype matched the bare word
     "operations" anywhere and so scored Cognite's boilerplate "Cognite **operates**
     at the forefront of industrial digitalization" as an operations role — the old
     model's disease in new clothing.
  2. Per-hit description weighting produced *portal* bias, not relevance. Only
     freehire and weworkremotely ship snippets, so freehire took 15 of the top 25
     while being 18% of the corpus and LinkedIn took 5 while being 66%.
  3. A silent fallback on a malformed config would score every job 0, which is
     indistinguishable from a thin market — the exact failure this phase exists to
     make visible.
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "two_axis_score.py"
MATRIX_PATH = REPO / "config" / "search_matrix.json"

_spec = importlib.util.spec_from_file_location("two_axis_score", SCRIPT)
tas = importlib.util.module_from_spec(_spec)
sys.modules["two_axis_score"] = tas
_spec.loader.exec_module(tas)


def block(**overrides):
    """A minimal but realistic `scoring` block, so tests state their own inputs."""
    out = {
        "enabled": True,
        "domain": {
            "supply_chain": {"anchors": ["supply chain", "logistics"],
                             "weak": ["inventory"]},
            "process": {"anchors": ["process excellence", "business process"],
                        "weak": ["process"]},
            "operations": {"anchors": ["operations analyst"],
                           "weak": ["operations", "operational"]},
        },
        "enabler": {
            "ai": {"anchors": ["artificial intelligence", "machine learning"],
                   "weak": ["ai"]},
            "data_bi": {"anchors": ["business intelligence", "power bi"],
                        "weak": ["data", "bi"]},
        },
        "core_tech_only": ["machine learning engineer", "data scientist"],
    }
    out.update(overrides)
    return out


def model(**overrides):
    return tas.ScoringModel.from_matrix({"scoring": block(**overrides)})


class Enablement(unittest.TestCase):
    """The model must be opt-in, because it changes every score in the corpus."""

    def test_absent_block_means_no_model(self):
        self.assertIsNone(tas.ScoringModel.from_matrix({}))

    def test_enabled_false_means_no_model(self):
        """`config/search_matrix.json` ships `enabled: false` on purpose.

        `scripts/prerank_jobs.py` is called by the scheduled 08:00 production run as
        well as by the sandbox runner. Shipping the block off is what lets the new
        model be exercised without changing what production computes before it has
        been reviewed.
        """
        self.assertIsNone(tas.ScoringModel.from_matrix({"scoring": block(enabled=False)}))

    def test_force_true_overrides_a_disabled_block(self):
        self.assertIsNotNone(
            tas.ScoringModel.from_matrix({"scoring": block(enabled=False)}, force=True))

    def test_force_false_overrides_an_enabled_block(self):
        self.assertIsNone(
            tas.ScoringModel.from_matrix({"scoring": block()}, force=False))

    def test_forcing_on_without_a_block_is_an_error_not_a_silent_zero(self):
        with self.assertRaises(ValueError):
            tas.ScoringModel.from_matrix({}, force=True)


class Configuration(unittest.TestCase):
    """A malformed block is fatal. A silent zero would read as an empty market."""

    def test_a_misspelled_key_is_reported(self):
        with self.assertRaises(ValueError) as ctx:
            tas.ScoringModel.from_matrix({"scoring": block(weigths={})})
        self.assertIn("weigths", str(ctx.exception))

    def test_prose_keys_are_allowed(self):
        """The whole matrix documents itself with `_`-prefixed keys.

        Rejecting them broke loading the real matrix on the first attempt:
        `_weights_comment`, `_anchor_weak_comment` and `_core_tech_comment` are
        documentation the readers already skip.
        """
        self.assertIsNotNone(tas.ScoringModel.from_matrix(
            {"scoring": block(_weights_comment="prose", _anchor_weak_comment="prose")}))

    def test_a_misspelled_weight_is_reported(self):
        with self.assertRaises(ValueError) as ctx:
            tas.ScoringModel.from_matrix({"scoring": block(weights={"domian": 30})})
        self.assertIn("domian", str(ctx.exception))

    def test_a_non_numeric_weight_is_reported(self):
        with self.assertRaises(ValueError):
            tas.ScoringModel.from_matrix({"scoring": block(weights={"domain": "lots"})})

    def test_a_negative_description_cap_is_reported(self):
        """A negative cap would silently *subtract* for having a description."""
        with self.assertRaises(ValueError):
            tas.ScoringModel.from_matrix(
                {"scoring": block(weights={"description_bonus_cap": -5})})

    def test_one_axis_alone_is_refused(self):
        """Scoring on a single axis is the bias this model exists to replace."""
        with self.assertRaises(ValueError):
            tas.ScoringModel.from_matrix({"scoring": block(enabler={})})

    def test_a_category_may_be_a_bare_list_of_anchors(self):
        m = tas.ScoringModel.from_matrix({"scoring": block(
            domain={"procurement": ["procurement", "sourcing"]})})
        self.assertEqual(m.score("Sourcing Manager")["domain_matched"], ["procurement"])

    def test_an_empty_category_is_reported(self):
        with self.assertRaises(ValueError):
            tas.ScoringModel.from_matrix({"scoring": block(domain={"process": []})})

    def test_weights_override_the_defaults_one_key_at_a_time(self):
        m = tas.ScoringModel.from_matrix({"scoring": block(weights={"domain": 1})})
        self.assertEqual(m.weights["domain"], 1)
        self.assertEqual(m.weights["overlap"], tas.DEFAULT_WEIGHTS["overlap"],
                         "an unmentioned weight must keep its default")


class Normalization(unittest.TestCase):

    def test_short_terms_cannot_match_inside_a_longer_word(self):
        """`ai` inside "maintain" and `bi` inside "ambition" are not signals.

        Both are real words in real postings, and both would fire an enabler
        category on a job with no AI or BI content at all.
        """
        m = model()
        for title in ("Maintenance Technician", "Ambition Group Recruiter"):
            with self.subTest(title=title):
                self.assertEqual(m.score(title)["enabler_matched"], [])

    def test_ampersand_and_plus_survive_normalization(self):
        """"S&OP", "FP&A" and "C++" carry meaning in this vocabulary."""
        self.assertEqual(tas.normalize("S&OP / FP&A, C++"), " s&op fp&a c++ ")

    def test_punctuation_does_not_defeat_a_phrase_match(self):
        m = model()
        for title in ("Supply-Chain Analyst", "Supply/Chain Analyst",
                      "SUPPLY  CHAIN Analyst"):
            with self.subTest(title=title):
                self.assertIn("supply_chain", m.score(title)["domain_matched"])

    def test_missing_text_is_not_an_error(self):
        m = model()
        self.assertEqual(m.score(None, None)["score"], 0)


class AnchorsAndWeakTerms(unittest.TestCase):
    """The split that keeps boilerplate out of the score."""

    def test_a_weak_term_in_the_body_does_not_match(self):
        """The Cognite regression, verbatim from the posting.

        "Cognite operates at the forefront of industrial digitalization" is a
        copywriter's verb, not a statement that this is an operations role. Measured
        corpus-wide, the `operations` category was 100% driven by
        "operations"/"operational" appearing in prose.
        """
        got = model().score(
            "Senior Software Engineer",
            "Cognite operates at the forefront of industrial digitalization. "
            "Our data and operational excellence ambitions are industry-leading.")
        self.assertEqual(got["domain_matched"], [],
                         "prose must not earn a domain category")

    def test_the_same_weak_term_in_the_title_does_match(self):
        """In a title the employer chose the word deliberately."""
        self.assertIn("operations", model().score("Operations Manager")["domain_matched"])

    def test_an_anchor_in_the_body_does_match(self):
        """"supply chain" is specific enough to trust anywhere."""
        got = model().score("Analyst",
                            "You will report into our supply chain organisation.")
        self.assertEqual(got["domain_from_description"], ["supply_chain"])
        self.assertEqual(got["domain_in_title"], [])

    def test_a_title_match_is_not_counted_again_from_the_body(self):
        """Otherwise a verbose posting outranks a precise one for being verbose."""
        got = model().score("Supply Chain Manager",
                            "supply chain " * 50)
        self.assertEqual(got["domain_in_title"], ["supply_chain"])
        self.assertEqual(got["domain_from_description"], [])


class Overlap(unittest.TestCase):
    """The hybrid bonus: the candidate profile, encoded structurally."""

    def test_a_hybrid_outranks_a_pure_tech_title(self):
        """Not by weight tuning — by the overlap term existing at all.

        "I don't just build AI solutions, I build the right AI solutions, grounded in
        business context" is the profile. A business+AI title must beat a research
        title without anyone hand-tuning a number to make it so.
        """
        m = model()
        hybrid = m.score("Business Process Analyst - AI Automation")
        pure = m.score("Machine Learning Engineer")
        self.assertGreater(hybrid["score"], pure["score"])
        self.assertEqual(hybrid["overlap_pairs"], 1)

    def test_overlap_counts_pairs_not_categories(self):
        got = model().score("Supply Chain Process Analyst - AI & Business Intelligence")
        self.assertEqual(got["overlap_pairs"], 2,
                         "two domains and two enablers make two pairs")

    def test_a_domain_only_title_still_scores(self):
        """The 102 jobs (18% of corpus) whose enabler is only in the body.

        "Continuous Improvement Manager" and "Quality Performance Manager" have no
        AI/data word in the title and must not be zeroed for it — they are exactly
        the band that enrich-before-cut exists to rescue.
        """
        self.assertGreater(model().score("Business Process Manager")["score"], 0)

    def test_overlap_reads_the_whole_posting_not_just_the_title(self):
        """The 85/45 defect. HARMAN's posting is the motivating case.

        Computing overlap from title counts alone meant enrichment could never
        create a hybrid: a bare "Data Analyst - AI" scored 85 while a posting that
        proved supply-chain content over 6000 characters scored exactly 45 and was
        cut. Every description-only-domain job in the 2026-08-19 corpus quantized
        onto that single value.
        """
        got = model().score("Data Analyst",
                            "You will support our supply chain organisation.")
        self.assertEqual(got["overlap_pairs"], 1,
                         "one domain from the body, one enabler from the title")
        self.assertGreater(got["score"], 0)

    def test_a_description_only_hybrid_is_not_weaker_than_a_title_only_one(self):
        """The parity requirement, stated directly.

        The title keeps an edge through the per-category weights — it should not
        also win by having the structural bonus withheld from the body.
        """
        m = model()
        body = m.score("Business Analyst",
                       "You will own demand planning across our supply chain and "
                       "partner with the process excellence team on continuous "
                       "improvement, using Power BI and machine learning.")
        title_only = m.score("Data Analyst - AI")
        self.assertGreater(body["score"], title_only["score"])


class OverlapIsLoggedNotChanged(unittest.TestCase):
    """`overlap_bonus` records what the uncapped term paid. It changes no score.

    The term is deliberately outside `description_bonus_cap` — it is computed on the
    combined title+body evidence, so nothing bounds it — and on the 2026-08-21 corpus
    it was 39-44% of each top-5 score and 31% of all points in the top 25. That is a
    large enough share to be worth a decision, and the decision was to wait for more
    real runs rather than guess a cap now. So the contribution is recorded per job and
    the weight is left alone.

    Both halves matter. If the field were wrong the review would be reading fiction;
    if adding it had moved a score, the runs being compared would not be comparable.
    """

    def test_the_bonus_is_the_weight_times_the_pairs(self):
        m = model()
        for title, pairs in (("Business Process Analyst - AI Automation", 1),
                             ("Supply Chain Process Analyst - AI & Business "
                              "Intelligence", 2)):
            with self.subTest(title=title):
                got = m.score(title)
                self.assertEqual(got["overlap_pairs"], pairs)
                self.assertEqual(got["overlap_bonus"], 35 * pairs)

    def test_a_body_earned_pair_is_recorded_the_same_way(self):
        """Enrichment's whole purpose is to create pairs, so it must be visible here.

        A domain read out of a fetched description buys exactly what a title domain
        buys; a review that could not see that would misread the enrichment budget.
        """
        got = model().score("Data Analyst",
                            "You will support our supply chain organisation.")
        self.assertEqual(got["overlap_pairs"], 1)
        self.assertEqual(got["overlap_bonus"], 35)

    def test_a_penalised_job_records_no_bonus_it_did_not_receive(self):
        """sennder's ML Engineer. The penalty zeroes the pair count, so the points too.

        Reporting points here would show the review a contribution that was never
        added, and the score would disagree with the sum of its parts.
        """
        got = model().score("Machine Learning Engineer",
                            "Europe's leading digital road freight logistics network.")
        self.assertTrue(got["core_tech_penalty"])
        self.assertEqual(got["overlap_bonus"], 0)

    def test_logging_the_contribution_did_not_move_any_score(self):
        """The numbers are literals on purpose — they predate the field.

        Every other test here pins an ordering so the weights stay tunable. These four
        pin values, because the guarantee under test is that *this change* was inert:
        the instruction was to log the contribution and not change the scorer. A future
        deliberate retune should fail this test and update it in the same commit.
        """
        m = model()
        self.assertEqual(m.score("Business Process Analyst - AI Automation")["score"],
                         125)
        self.assertEqual(m.score("Supply Chain Process Analyst - AI & Business "
                                 "Intelligence")["score"], 210)
        self.assertEqual(m.score("Data Analyst",
                                 "You will support our supply chain "
                                 "organisation.")["score"], 92)
        self.assertEqual(m.score("Business Process Manager")["score"], 30)


class HybridTiers(unittest.TestCase):
    """The stated priority hierarchy, as weights rather than title exceptions."""

    def test_domain_plus_ai_outranks_domain_plus_data(self):
        """Top of the hierarchy is business + AI/automation, not business + BI."""
        m = model()
        strong = m.score("Supply Chain Manager - Artificial Intelligence")
        medium = m.score("Supply Chain Manager - Business Intelligence")
        self.assertEqual(strong["hybrid_tier"], "strong")
        self.assertEqual(medium["hybrid_tier"], "medium")
        self.assertGreater(strong["score"], medium["score"])

    def test_a_domain_with_no_enabler_gets_no_tier(self):
        got = model().score("Supply Chain Manager")
        self.assertIsNone(got["hybrid_tier"])
        self.assertEqual(got["hybrid_tier_bonus"], 0)

    def test_a_pure_enabler_title_gets_no_tier(self):
        got = model().score("AI Engineer")
        self.assertIsNone(got["hybrid_tier"])

    def test_the_tier_is_awarded_once_not_per_pair(self):
        """Otherwise a posting buys rank with length."""
        m = model()
        one = m.score("Supply Chain Analyst - AI")
        many = m.score("Supply Chain Process Operations Analyst - AI & Data")
        self.assertEqual(one["hybrid_tier_bonus"], many["hybrid_tier_bonus"])

    def test_a_body_only_domain_earns_a_scaled_tier(self):
        """Weaker evidence about the role, but never zero — that was the 45 cliff."""
        m = model()
        titled = m.score("Supply Chain Analyst - AI")
        bodied = m.score("AI Analyst", "supporting our supply chain organisation")
        self.assertEqual(bodied["hybrid_tier"], "strong")
        self.assertGreater(bodied["hybrid_tier_bonus"], 0)
        self.assertLess(bodied["hybrid_tier_bonus"], titled["hybrid_tier_bonus"])


class PerCategoryExclusions(unittest.TestCase):
    """Generic technical terminology must not manufacture a business domain."""

    def test_data_warehouse_does_not_fire_the_supply_chain_domain(self):
        m = model(domain={"supply_chain": {"anchors": ["supply chain", "warehouse"],
                                           "exclude": ["data warehouse"]}},
                  enabler={"data_bi": {"weak": ["data"], "anchors": ["power bi"]}})
        got = m.score("Senior Solutions Architect - Enterprise Data Warehouse")
        self.assertEqual(got["domain_matched"], [],
                         "'Data Warehouse' is not a supply-chain job")

    def test_a_real_warehouse_role_still_matches(self):
        m = model(domain={"supply_chain": {"anchors": ["supply chain", "warehouse"],
                                           "exclude": ["data warehouse"]}})
        self.assertEqual(m.score("Warehouse Operations Manager")["domain_matched"],
                         ["supply_chain"])

    def test_masking_is_scoped_to_the_category_that_declared_it(self):
        """Masking "data warehouse" globally would also delete `data_bi`'s "data"."""
        m = model(domain={"supply_chain": {"anchors": ["warehouse"],
                                          "exclude": ["data warehouse"]}},
                  enabler={"data_bi": {"anchors": ["business intelligence"],
                                       "weak": ["data"]}})
        got = m.score("Data Warehouse Engineer")
        self.assertEqual(got["domain_matched"], [])
        self.assertEqual(got["enabler_matched"], ["data_bi"])


class TheWorkflowAnchorIsAmbiguous(unittest.TestCase):
    """The same word names business-process work and a CI/CD pipeline.

    Found by the 2026-08-19 replay, not by inspection: Deutsche Telekom's "AI SWE /
    Agentic SDLC Workflow Engineer" reached #10 of the top 25 on a `process`+`ai`
    hybrid, because "Workflow" is a `process` anchor and "AI" is an enabler. It is a
    software-engineering role and the hybrid is not real.

    Dropping the anchor is the wrong repair — "workflow automation" and "approval
    workflow owner" are how genuine process postings describe themselves, and the
    #1 pick of the same run (MSCI's "Business Analyst - Managed Services") matches
    `process` on exactly that word. So the engineering senses are masked instead,
    which is the mechanism `supply_chain` already uses for "data warehouse".
    """

    PROCESS = {"process": {"anchors": ["business process", "workflow"],
                           "weak": ["process"],
                           "exclude": ["workflow engineer", "sdlc workflow",
                                       "workflow orchestration", "ci/cd workflow",
                                       "github workflow", "workflow engine"]}}
    ENABLER = {"ai": {"anchors": ["artificial intelligence"], "weak": ["ai"]}}

    def score(self, title, body=""):
        return model(domain=self.PROCESS, enabler=self.ENABLER).score(title, body)

    def test_the_deutsche_telekom_posting_no_longer_reads_as_a_process_hybrid(self):
        got = self.score("AI SWE / Agentic SDLC Workflow Engineer - T Cloud Public")
        self.assertEqual(got["domain_matched"], [],
                         "an SDLC workflow is a build pipeline, not a business process")
        self.assertEqual(got["overlap_pairs"], 0, "so there is no hybrid to reward")
        self.assertEqual(got["enabler_matched"], ["ai"], "it is still an AI role")

    def test_business_workflow_language_still_matches(self):
        """The MSCI reading the mask must not take with it."""
        got = self.score("Business Analyst - Managed Services",
                         "You will own approval workflow design with process owners.")
        self.assertEqual(got["domain_matched"], ["process"])

    def test_workflow_automation_still_matches(self):
        self.assertEqual(self.score("Workflow Automation Specialist")["domain_matched"],
                         ["process"])

    def test_a_masked_phrase_does_not_hide_a_real_anchor_beside_it(self):
        """Masking removes the phrase, never the sentence around it.

        A posting that is genuinely both — a workflow engineer on a business-process
        team — keeps its match through the other anchor.
        """
        got = self.score("Workflow Engineer",
                         "Join our business process excellence team.")
        self.assertEqual(got["domain_matched"], ["process"])


class DescriptionBonus(unittest.TestCase):
    """Bounded, because per-hit weighting bought portal bias instead of relevance."""

    def test_the_bonus_is_capped(self):
        m = model()
        spam = m.score("Warehouse Associate",
                       "supply chain process excellence business intelligence "
                       "artificial intelligence operations analyst " * 50)
        self.assertLessEqual(spam["description_bonus"],
                             m.weights["description_bonus_cap"])

    def test_a_long_description_cannot_beat_a_real_title_match(self):
        m = model()
        titled = m.score("Supply Chain Process Analyst - Business Intelligence")
        buried = m.score("Warehouse Associate",
                         "supply chain process excellence business intelligence " * 80)
        self.assertGreater(titled["score"], buried["score"],
                           "otherwise the portals that ship snippets win on format")

    def test_the_hidden_hybrid_bonus_fires_only_when_the_title_concealed_it(self):
        """Citi's "Digital Transformation Senior Analyst" is the motivating case.

        On title alone it reads enabler-only and falls to 23rd among the 41 alert
        jobs. Its real description carries business_analysis and continuous
        improvement, which is what makes it a hybrid — and what the bonus rewards.
        """
        m = model()
        revealed = m.score("Data Analyst", "our process excellence team")
        already = m.score("Business Process Data Analyst", "our process excellence team")
        self.assertGreater(revealed["description_bonus"], 0)
        self.assertEqual(already["description_bonus"], 0,
                         "the title already showed both axes; nothing was hidden")

    def test_the_hidden_hybrid_bonus_is_not_swallowed_by_the_cap(self):
        """It is added outside the cap, which is the whole point of separating them.

        Inside the cap the bonus vanished exactly when the body was rich enough to
        prove the hybrid — HARMAN's posting lost all 15 points of it that way.
        """
        m = model(weights={"description_bonus_cap": 5})
        got = m.score("Business Analyst",
                      "demand planning across our supply chain, with the process "
                      "excellence team, using business intelligence")
        self.assertEqual(got["description_bonus"], 5, "the cap still binds the body")
        self.assertEqual(got["hidden_hybrid_bonus"],
                         m.weights["hidden_hybrid_bonus"],
                         "and the hybrid bonus survives it intact")

    def test_zero_cap_ignores_descriptions_entirely(self):
        m = tas.ScoringModel.from_matrix(
            {"scoring": block(weights={"description_bonus_cap": 0})})
        self.assertEqual(m.score("Analyst", "supply chain")["description_bonus"], 0)


class CoreTechPenalty(unittest.TestCase):
    """Career direction, applied unless the ROLE carries a business component."""

    def test_a_pure_core_tech_title_is_penalised(self):
        got = model().score("Machine Learning Engineer")
        self.assertTrue(got["core_tech_penalty"])
        self.assertEqual(got["core_tech_marker"], "machine learning engineer")
        self.assertLess(got["score"], 0)

    def test_a_genuine_hybrid_is_not_penalised(self):
        """"Supply Chain Data Scientist" is the role, not the thing being avoided."""
        got = model().score("Supply Chain Data Scientist")
        self.assertFalse(got["core_tech_penalty"])
        self.assertTrue(got["core_tech_exempt"],
                        "the marker still matched; the title's domain exempted it")
        self.assertEqual(got["core_tech_marker"], "data scientist",
                         "the marker is reported even when exempt, so the report can "
                         "show why a core-tech title was kept")
        self.assertGreater(got["score"], 0)

    def test_one_passing_domain_mention_in_the_body_does_not_exempt(self):
        """The employer's industry is not the job's content.

        "Machine Learning Engineer at a logistics company" must not qualify merely
        because the company operates in supply chain — the role itself has to carry
        the business/process component. One body category is boilerplate; several
        independent ones are a posting describing business work in its own duties.
        """
        got = model().score("Machine Learning Engineer",
                            "We are Europe's leading digital road freight logistics "
                            "network. You will own our forecasting models.")
        self.assertTrue(got["core_tech_penalty"])
        self.assertEqual(got["domain_from_description"], ["supply_chain"])

    def test_several_body_domains_do_exempt(self):
        """A body that describes business process duties is role-level evidence."""
        got = model().score(
            "Data Scientist",
            "You will own demand planning and supply chain forecasting, partner with "
            "our process excellence team on continuous improvement, and support "
            "procurement analytics.")
        self.assertFalse(got["core_tech_penalty"])
        self.assertTrue(got["core_tech_exempt"])

    def test_the_penalty_suppresses_the_hybrid_rewards_it_contradicts(self):
        """A penalised job must not be refunded by the bonuses it just failed.

        Every hybrid reward is premised on a real business domain. Paying overlap,
        the hidden bonus and the tier bonus off the same boilerplate mention the
        penalty just rejected returned sennder's ML Engineer to a positive score.
        """
        got = model().score("Machine Learning Engineer",
                            "Europe's leading digital road freight logistics network.")
        self.assertTrue(got["core_tech_penalty"])
        self.assertEqual(got["overlap_pairs"], 0)
        self.assertEqual(got["hidden_hybrid_bonus"], 0)
        self.assertIsNone(got["hybrid_tier"])
        self.assertLess(got["score"], 0)

    def test_no_marker_means_no_penalty(self):
        got = model().score("Process Improvement Manager")
        self.assertFalse(got["core_tech_penalty"])
        self.assertIsNone(got["core_tech_marker"])
        self.assertFalse(got["core_tech_exempt"])


class RealMatrix(unittest.TestCase):
    """The shipped config must stay loadable, and its state must be deliberate."""

    @classmethod
    def setUpClass(cls):
        cls.matrix = json.loads(MATRIX_PATH.read_text())

    def test_the_shipped_block_is_valid(self):
        forced = tas.ScoringModel.from_matrix(self.matrix, force=True)
        self.assertIsNotNone(forced)
        self.assertGreaterEqual(len(forced.domain), 8)
        self.assertGreaterEqual(len(forced.enabler), 4)

    def test_the_shipped_block_is_live(self):
        """Turned on 2026-08-22, after the sandbox pass was reviewed and approved.

        This assertion is inverted from what it said while the model was on trial
        ("until a full sandbox pass has been reviewed, production must not change").
        It is kept rather than deleted because the state is a decision either way:
        the block silently reverting to `enabled: false` would put production back on
        the query-match scorer that scored "Advanced PMO Specialist - Sourcing &
        Procurement Excellence" a literal 0, and nothing else would report it.
        """
        self.assertIsNotNone(tas.ScoringModel.from_matrix(self.matrix))

    def test_the_named_target_roles_outrank_the_pure_tech_ones(self):
        """The whole point, measured on the titles from the 2026-08-19 diagnosis.

        The comments record what the old model scored; the assertions pin the
        ordering rather than the numbers, so the weights can be retuned without
        rewriting the test.
        """
        m = tas.ScoringModel.from_matrix(self.matrix, force=True)
        target = [
            "Consultant, Digital, Data & AI in Procurement / Supply Chain",  # was 150
            "Process & BI Manager, Inventory Management",                    # was 30
            "Digital Process Analyst",                                       # was 30
            "Automation Business Analyst",                                   # was 30
            "Advanced PMO Specialist - Sourcing & Procurement Excellence",   # was 0
            "Continuous Improvement Manager",                                # was 20
            "Quality Performance Manager",                                   # was 20
        ]
        pure = ["Machine Learning Engineer",          # was 330
                "Data Scientist",                     # was 120
                "Research Scientist, Deep Learning"]  # was 20
        worst_target = min(m.score(t)["score"] for t in target)
        best_pure = max(m.score(t)["score"] for t in pure)
        self.assertGreater(worst_target, best_pure)

    def test_the_penalty_spares_the_real_hybrids(self):
        m = tas.ScoringModel.from_matrix(self.matrix, force=True)
        for title in ("Supply Chain Data Scientist",
                      "Data Scientist, Supply Chain Optimization",
                      "AI Process Automation Lead"):
            with self.subTest(title=title):
                self.assertGreater(m.score(title)["score"], 0)


if __name__ == "__main__":
    unittest.main()
