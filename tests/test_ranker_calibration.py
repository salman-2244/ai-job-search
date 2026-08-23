"""Regression guards for the ranker calibration (Phase 1 of the LinkedIn design).

The daily pipeline's ranker is an LLM prompt, not Python — `prompts/pipeline_phase1_rank.md`
IS the implementation, read verbatim by `scripts/run_daily.sh`. So these tests do two things:

1. **Formula regression** — reference implementations of the OLD and NEW scoring specs,
   run over fixture jobs drawn from real postings the old formula buried. This is the
   behavior change the design asked for, made executable: Performance / Demand Planning /
   Supply Chain roles move from Career ~0 to >=90.

2. **Spec guards** — assert the prompt files actually carry the new rules and no longer
   carry the broken ones, so a future edit can't silently reintroduce `Other = 0` or
   grade a posting's seniority label instead of the candidate's real experience.

The old formula (before 2026-08-18) scored:
    Career:     AI/ML/Data/Analytics = 100, Software Engineering = 50, Other = 0
    Experience: Junior/Mid = 100, Senior = 80, Lead/Principal = 60, Intern = 40
    Overall:    Technical*0.35 + Experience*0.30 + Career*0.35   (no Behavioral)

Both were wrong in ways that compounded: Career zeroed out the candidate's own job title
(Performance Manager), and Experience graded the posting's label, so "Senior, 8+ years"
scored 80 — near-perfect — when it is a hard gap.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RANK_PROMPT = REPO / "prompts" / "pipeline_phase1_rank.md"
SANDBOX_PROMPT = REPO / "manual_run_2026-08-19" / "prompts" / "pipeline_phase1_rank.md"
FRAMEWORK = REPO / ".claude" / "skills" / "job-application-assistant" / "04-job-evaluation.md"
PROFILE = REPO / ".claude" / "skills" / "job-application-assistant" / "01-candidate-profile.md"
RANK_COMMAND = REPO / ".claude" / "commands" / "rank.md"

# Real postings surfaced by live probes on 2026-08-18 that the old formula buried at
# Career = 0 ("Other"), plus AI/data roles that must keep scoring high. `asks_years` is
# the posting's stated requirement; `label` is its seniority word.
FIXTURES = [
    # Buried by the old formula — the whole point of the change
    {"title": "CF & Planning Performance Analyst", "company": "bp",
     "track": "T5", "asks_years": 2, "label": "mid"},
    {"title": "Commercial Demand Planning Manager", "company": "Eaton",
     "track": "T4", "asks_years": 3, "label": "mid"},
    {"title": "Performance Manager", "company": "Capri Partners",
     "track": "T5", "asks_years": 3, "label": "mid"},
    {"title": "Supply Chain Analyst", "company": "Synthetic Co",
     "track": "T4", "asks_years": 2, "label": "mid"},
    {"title": "Business Excellence Manager", "company": "Synthetic Co",
     "track": "T5", "asks_years": 4, "label": "mid"},
    {"title": "Digital Transformation Lead", "company": "Synthetic Co",
     "track": "T5", "asks_years": 5, "label": "lead"},
    # Already scored well — must not regress
    {"title": "AI Engineer", "company": "Renegades",
     "track": "T1", "asks_years": 2, "label": "mid"},
    {"title": "Senior Data Scientist", "company": "Proxify AB",
     "track": "T2", "asks_years": 7, "label": "senior"},
    {"title": "AI Product Manager", "company": "Synthetic Co",
     "track": "T3", "asks_years": 4, "label": "mid"},
]

# Track anchors: phrases that MUST appear in the profile's Profile Tracks vocabulary for
# each track to be findable at all. Before 2026-08-18 the pipeline prompt contained none
# of the T4/T5 phrases, which is precisely why those roles scored Career = 0.
TRACK_ANCHORS = {
    "T1": ["AI Engineer", "Machine Learning Engineer", "LLM Engineer"],
    "T2": ["Data Scientist", "Data Analyst", "BI Developer"],
    "T3": ["AI Product Manager", "Intelligent Automation", "Low-Code AI"],
    "T4": ["Supply Chain Analyst", "Demand Planning", "Procurement Analyst",
           "Operations Analyst"],
    "T5": ["Performance Manager", "Performance Analyst", "Process Manager",
           "Business Excellence", "Digital Transformation"],
}


def old_career_score(title: str) -> int:
    """Reference implementation of the OLD Career rule (pipeline_phase1_rank.md:29).

    'AI/ML/Data/Analytics role = 100, Software Engineering = 50, Other = 0'
    """
    low = title.lower()
    if any(k in low for k in ("ai ", "ai/", "artificial intelligence", "machine learning",
                              "ml ", "data scien", "data analy", "analytics")):
        return 100
    if "software engineer" in low or "developer" in low:
        return 50
    return 0


def old_experience_score(label: str) -> int:
    """Reference implementation of the OLD Experience rule (pipeline_phase1_rank.md:28).

    Grades the POSTING'S seniority label, not the candidate's fit — the inversion.
    """
    return {"mid": 100, "junior": 100, "senior": 80, "lead": 60,
            "principal": 60, "intern": 40}.get(label, 100)


def new_career_score(title: str) -> tuple:
    """Reference implementation of the NEW track-based Career rule.

    Returns (score, matched_track). Two or more tracks -> 100; one track -> 95;
    adjacent-but-thin -> 50; unrelated -> 10.
    """
    low = title.lower()
    matched = [
        track for track, anchors in TRACK_ANCHORS.items()
        if any(a.lower() in low for a in anchors)
    ]
    if len(matched) >= 2:
        return 100, "+".join(sorted(matched))
    if len(matched) == 1:
        return 95, matched[0]
    if "software engineer" in low or "devops" in low or "data engineer" in low:
        return 50, "none"
    return 10, "none"


def new_experience_score(asks_years: int, needs_people_mgmt: bool = False) -> int:
    """Reference implementation of the NEW Experience bands.

    Scores the posting's STATED requirement against the candidate's real baseline
    (~3.7 years total, ~1 year post-degree, 0 years line management).
    """
    if needs_people_mgmt:
        return 30
    if asks_years <= 2:
        return 100
    if asks_years <= 4:
        return 85
    if asks_years <= 6:
        return 55
    return 25


def old_overall(technical: int, experience: int, career: int) -> float:
    return technical * 0.35 + experience * 0.30 + career * 0.35


def new_overall(technical: int, experience: int, behavioral: int, career: int) -> float:
    return technical * 0.30 + experience * 0.25 + behavioral * 0.15 + career * 0.30


class CareerRegression(unittest.TestCase):
    """The headline fix: T4/T5 roles move from Career ~0 to >=90."""

    def test_t4_t5_roles_were_zeroed_by_the_old_formula(self):
        buried = [f for f in FIXTURES if f["track"] in ("T4", "T5")]
        self.assertTrue(buried, "fixture set must contain T4/T5 roles to regress against")
        for f in buried:
            with self.subTest(title=f["title"]):
                self.assertEqual(
                    old_career_score(f["title"]), 0,
                    f"{f['title']!r} is a T4/T5 role the old formula should have scored 0; "
                    "if this now scores above 0 the fixture no longer demonstrates the bug",
                )

    def test_t4_t5_roles_now_score_at_least_90(self):
        for f in FIXTURES:
            if f["track"] not in ("T4", "T5"):
                continue
            with self.subTest(title=f["title"]):
                score, track = new_career_score(f["title"])
                self.assertGreaterEqual(
                    score, 90,
                    f"{f['title']!r} must score >=90 under the track rule, got {score} "
                    f"(matched track: {track})",
                )
                self.assertIn(f["track"], track,
                              f"{f['title']!r} should match {f['track']}, matched {track!r}")

    def test_ai_and_data_roles_do_not_regress(self):
        for f in FIXTURES:
            if f["track"] not in ("T1", "T2", "T3"):
                continue
            with self.subTest(title=f["title"]):
                score, track = new_career_score(f["title"])
                self.assertGreaterEqual(
                    score, 90,
                    f"{f['title']!r} scored well before and must still score >=90, "
                    f"got {score} (track {track})",
                )

    def test_every_fixture_matches_its_expected_track(self):
        for f in FIXTURES:
            with self.subTest(title=f["title"]):
                _, track = new_career_score(f["title"])
                self.assertIn(f["track"], track,
                              f"{f['title']!r} expected {f['track']}, matched {track!r}")

    def test_unrelated_roles_still_score_low(self):
        for title in ("Regional Sales Director", "HR Business Partner",
                      "Hardware Test Technician"):
            with self.subTest(title=title):
                score, _ = new_career_score(title)
                self.assertLessEqual(score, 20, f"{title!r} is unrelated; got {score}")


class ExperienceRegression(unittest.TestCase):
    """The old Experience rule was inverted — it graded the posting's label."""

    def test_old_formula_scored_senior_roles_as_near_perfect(self):
        self.assertEqual(
            old_experience_score("senior"), 80,
            "the old rule scored Senior at 80; the fixture must preserve that to regress",
        )

    def test_senior_roles_are_now_scored_as_a_genuine_gap(self):
        self.assertEqual(new_experience_score(asks_years=8), 25)
        self.assertEqual(new_experience_score(asks_years=7), 25)

    def test_bands_are_monotonic_in_years(self):
        scores = [new_experience_score(y) for y in (1, 2, 3, 4, 5, 6, 7, 10)]
        self.assertEqual(scores, sorted(scores, reverse=True),
                         f"Experience must never rise as the years requirement rises: {scores}")

    def test_people_management_requirement_scores_low_regardless_of_years(self):
        self.assertEqual(
            new_experience_score(asks_years=1, needs_people_mgmt=True), 30,
            "0 years line management is a real gap even on a junior posting",
        )

    def test_early_career_postings_score_full_marks(self):
        self.assertEqual(new_experience_score(asks_years=0), 100)
        self.assertEqual(new_experience_score(asks_years=2), 100)


class OverallScoreRegression(unittest.TestCase):
    def test_weights_sum_to_one_in_both_formulas(self):
        self.assertAlmostEqual(old_overall(100, 100, 100), 100.0)
        self.assertAlmostEqual(new_overall(100, 100, 100, 100), 100.0)

    def test_buried_roles_clear_the_60_report_threshold_under_the_new_formula(self):
        """A T4/T5 role with decent technical fit should now be visible, not invisible."""
        for f in FIXTURES:
            if f["track"] not in ("T4", "T5"):
                continue
            with self.subTest(title=f["title"]):
                career, _ = new_career_score(f["title"])
                experience = new_experience_score(f["asks_years"])
                # Technical 70 / Behavioral 70 = a plausible, unexceptional posting
                before = old_overall(70, old_experience_score(f["label"]),
                                     old_career_score(f["title"]))
                after = new_overall(70, experience, 70, career)
                self.assertGreater(
                    after, before,
                    f"{f['title']!r} must score higher under the new formula "
                    f"({after:.1f}) than the old one ({before:.1f})",
                )
                self.assertGreaterEqual(
                    after, 60,
                    f"{f['title']!r} scored {after:.1f}; a solid T4/T5 role should at "
                    "least reach the 60 reporting threshold",
                )


class RankPromptSpecGuards(unittest.TestCase):
    """Pin the prompt text, since the prompt IS the ranker implementation."""

    @classmethod
    def setUpClass(cls):
        cls.prompt = RANK_PROMPT.read_text(encoding="utf-8")

    def test_prompt_file_exists_and_is_read_by_the_runner(self):
        self.assertTrue(RANK_PROMPT.is_file(), "ranker prompt missing")
        runner = (REPO / "scripts" / "run_daily.sh").read_text(encoding="utf-8")
        self.assertIn("prompts/pipeline_phase1_rank.md", runner,
                      "run_daily.sh must still read the ranker prompt")

    def test_old_broken_career_rule_is_gone(self):
        self.assertNotIn(
            "Other = 0", self.prompt,
            "the 'Other = 0' Career rule zeroed out the candidate's own job title",
        )

    def test_old_inverted_experience_rule_is_gone(self):
        self.assertNotIn(
            "Senior = 80", self.prompt,
            "grading the posting's seniority label inverted the Experience dimension",
        )

    def test_old_keyword_counting_instruction_is_gone(self):
        self.assertNotIn(
            "Count keyword matches", self.prompt,
            "unbounded keyword counting makes a long posting beat a good one",
        )

    def test_prompt_uses_the_unified_four_dimension_weighting(self):
        for weight in ("0.30", "0.25", "0.15"):
            self.assertIn(weight, self.prompt,
                          f"weight {weight} missing — prompt must use Technical 30 / "
                          "Experience 25 / Behavioral 15 / Career 30")
        self.assertNotIn("0.35", self.prompt,
                         "0.35 belongs to the superseded 3-dimension weighting")

    def test_prompt_scores_behavioral_fit(self):
        self.assertIn("Behavioral", self.prompt,
                      "Behavioral was silently dropped from the pipeline formula")

    def test_prompt_names_all_five_tracks(self):
        for track in TRACK_ANCHORS:
            self.assertIn(track, self.prompt, f"{track} missing from the ranker prompt")

    def test_prompt_carries_the_t4_t5_vocabulary(self):
        for track in ("T4", "T5"):
            for anchor in TRACK_ANCHORS[track]:
                self.assertIn(
                    anchor, self.prompt,
                    f"{anchor!r} ({track}) missing — without this vocabulary the ranker "
                    "cannot recognize the roles it used to bury",
                )

    def test_prompt_forbids_grading_the_seniority_label(self):
        self.assertIn(
            "Never grade the posting's own seniority label", self.prompt,
            "the prompt must explicitly forbid the inversion it used to commit",
        )

    def test_prompt_requires_the_matched_track_be_recorded(self):
        self.assertIn('"track"', self.prompt,
                      "a low Career score must be auditable against a named track")

    def test_prompt_states_the_document_gate(self):
        self.assertIn("score >= 75", self.prompt, "gate threshold 75 missing")
        self.assertIn("alert_matched.json", self.prompt,
                      "the alert-matched 60 gate must read alert_matched.json")

    def test_prompt_forbids_an_alert_score_bonus(self):
        self.assertIn("Do not add points for it", self.prompt,
                      "alert-match must lower the gate, never add points")

    def test_prompt_tolerates_a_missing_alert_file(self):
        self.assertIn("missing file is normal", self.prompt,
                      "a missing alert_matched.json must degrade gracefully, not fail the phase")

    def test_prompt_keeps_the_untrusted_posting_rule(self):
        self.assertIn("untrusted data", self.prompt,
                      "postings are third-party text and must never be treated as instructions")

    def test_prompt_still_forbids_fetching_urls(self):
        self.assertIn("Do NOT fetch any URLs", self.prompt,
                      "the ranker scores from fetched data only; fetching is /apply's job")

    def test_prompt_writes_the_not_drafted_list(self):
        self.assertIn("<NOT_DRAFTED_FILE_PATH>", self.prompt,
                      "Good Fits that miss the gate must stay visible to the user")


class FrameworkAlignment(unittest.TestCase):
    """The prompt, the framework doc, and the profile must agree."""

    @classmethod
    def setUpClass(cls):
        cls.framework = FRAMEWORK.read_text(encoding="utf-8")
        cls.profile = PROFILE.read_text(encoding="utf-8")
        cls.command = RANK_COMMAND.read_text(encoding="utf-8")

    def test_framework_declares_the_four_weights(self):
        for line in ("Technical Skills: 30%", "Experience Match: 25%",
                     "Behavioral Fit: 15%", "Career Alignment: 30%"):
            self.assertIn(line, self.framework, f"missing weighting line: {line!r}")

    def test_profile_defines_all_five_tracks_with_vocabulary(self):
        self.assertIn("## Profile Tracks", self.profile,
                      "Profile Tracks is the single source of truth for Career scoring")
        for track, anchors in TRACK_ANCHORS.items():
            for anchor in anchors:
                self.assertIn(anchor, self.profile,
                              f"{anchor!r} ({track}) missing from the profile's track vocabulary")

    def test_profile_records_an_experience_baseline(self):
        self.assertIn("## Experience Baseline", self.profile,
                      "Experience scoring needs a stated baseline to score against")
        self.assertIn("0 years formal people", self.profile,
                      "the line-management gap must be stated honestly")

    def test_framework_experience_bands_match_the_prompt(self):
        for band in ("| 100 |", "| 85 |", "| 55 |", "| 25 |", "| 30 |"):
            self.assertIn(band, self.framework,
                          f"Experience band {band!r} missing from the framework doc")

    def test_rank_command_records_the_track_too(self):
        self.assertIn('"track"', self.command,
                      "/rank must record the matched track, same as the pipeline ranker")

    def test_eligibility_is_confirmed_not_assumed(self):
        self.assertIn("confirmed by the candidate on 2026-08-18", self.framework,
                      "the Hungarian-permit status was confirmed and must not read as an assumption")

    def test_framework_and_prompt_agree_on_the_verdict_bands(self):
        prompt = RANK_PROMPT.read_text(encoding="utf-8")
        for text, name in ((self.framework, "framework"), (prompt, "prompt")):
            with self.subTest(spec=name):
                self.assertIn("60-74", text, f"{name} missing the Good Fit band")
                self.assertIn("45-59", text, f"{name} missing the Moderate Fit band")
                self.assertIn("30-44", text, f"{name} missing the Weak Fit band")


class SandboxGateGuards(unittest.TestCase):
    """The C6/C7 gates, pinned in the sandbox prompt that actually carries them.

    Both gates are prompt text, so text is the only place they can be checked. Each
    one exists because a specific job was mishandled on 2026-08-19:

    - DHL required "Fluent English and Hungarian". Hungarian is on the Languages
      table at A2, so the framework's Language Gate returned FLAG for a bar above the
      declared level — and the pipeline drafted it. A hard job condition the candidate
      cannot meet is a discard.
    - Citi asked for "+/- 5 years of experience in Finance industry" and was the #2
      pick at 89. A naive numeric gate discards it, which is why the ambiguity markers
      are enumerated and an automatic exclusion is forbidden.
    """

    @classmethod
    def setUpClass(cls):
        cls.prompt = SANDBOX_PROMPT.read_text(encoding="utf-8")

    def at(self, needle):
        self.assertIn(needle, self.prompt)
        return self.prompt.index(needle)

    def test_the_gates_run_before_scoring(self):
        """A gate that runs after scoring is a filter on work already spent, and the
        discarded job still lands in the rankset."""
        self.assertLess(self.at("### Step 3: Run the gates"),
                        self.at("### Step 4: Score each surviving job"))

    def test_a_required_unspoken_language_is_discarded_not_flagged(self):
        gates = self.prompt[self.at("**Language Gate.**"):self.at("**Experience Gate.**")]
        self.assertIn("`FAIL` — discard", gates)
        self.assertIn("Quote the exact requirement line when you discard.", gates)

    def test_the_superseded_flag_rule_is_named_as_superseded(self):
        """Naming it is what stops a reader reconciling this prompt with the framework
        doc and concluding the older FLAG rule still applies."""
        self.assertIn("This supersedes the older rule that returned `FLAG`", self.prompt)

    def test_a_language_marked_as_an_advantage_still_passes(self):
        self.assertIn("advantage / plus / nice-to-have / preferred", self.prompt)
        self.assertIn('KPMG\'s "Hungarian knowledge is an advantage" still passes', self.prompt)

    def test_only_an_explicit_four_plus_requirement_is_discarded(self):
        self.assertIn("**Explicitly 4+ years**", self.prompt)
        self.assertIn("discard only what is unambiguous", self.prompt)

    def test_every_ambiguity_marker_survives(self):
        for marker in ('`+/-`, "approximately"', '"ideally", "preferably"',
                       "range whose floor is ≤3", "adjacent domain"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.prompt,
                              f"ambiguity marker {marker!r} was approved explicitly")

    def test_an_automatic_exclusion_is_forbidden_outright(self):
        self.assertIn("Never auto-exclude.", self.prompt)
        self.assertIn("Inspect, keep, and say why.", self.prompt)

    def test_the_gate_and_the_experience_dimension_are_kept_distinct(self):
        """Without this the model reads a gate PASS as an experience match and scores a
        kept "+/- 5 years" posting at 100 instead of the 4-6 band's 55."""
        self.assertIn("are not the same test", self.prompt)
        self.assertIn("The gate\ndecides eligibility; the dimension prices the gap.", self.prompt)

    def test_the_prompt_claims_precedence_over_the_framework_doc(self):
        self.assertIn("**this prompt wins.**", self.prompt)
        self.assertIn("there is no live disagreement on the Language Gate", self.prompt)

    def test_the_prompt_does_not_tell_the_model_to_ignore_the_fixed_framework(self):
        """Before 2026-08-21 this prompt overrode the framework's Language Gate wholesale,
        because the framework returned FLAG where this returns FAIL. The framework now
        returns FAIL too, and it is the broader statement — it generalises past the three
        languages named here and adds UNKNOWN. A blanket "ignore it" would now discard the
        better rule."""
        self.assertNotIn("ignore the framework's Language Gate table entirely", self.prompt)
        self.assertIn("never promote no-evidence to `PASS`", self.prompt)

    def test_pure_core_tech_with_no_domain_is_capped(self):
        self.assertIn("at most 55", self.prompt)
        for marker in ("machine learning engineer", "data scientist", "mlops engineer"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.prompt)

    def test_a_genuine_hybrid_is_exempt_from_the_cap(self):
        """The cap is a career-direction correction, not a penalty on the word
        "scientist" — a Supply Chain Data Scientist is exactly the target shape."""
        self.assertIn("Supply Chain Data Scientist", self.prompt)
        self.assertIn("both keep their T2+T4 score of 100", self.prompt)

    def test_the_cap_states_why_it_duplicates_the_prerank_penalty(self):
        self.assertIn("−60 penalty", self.prompt)
        self.assertIn("alert-matched job can still reach you through a reserved slot",
                      self.prompt)

    def test_both_gate_verdicts_are_recorded_on_every_evaluated_job(self):
        step6 = self.prompt[self.at("### Step 6:"):self.at("### Step 7:")]
        self.assertIn("every job you evaluated", step6)
        for field in ("language_gate", "experience_gate", "language_note", "experience_note"):
            with self.subTest(field=field):
                self.assertIn(field, step6)

    def test_a_discard_is_never_silent(self):
        self.assertIn("A discard must never be silent", self.prompt)

    def test_no_new_status_value_is_invented(self):
        """`seen_jobs.json`'s status vocabulary is closed — new/skipped/ranked/expired.
        CHANGELOG #315 removed a phantom `evaluated`; a gate-specific status would be
        the same defect. The verdict belongs in its own field."""
        used = set(re.findall(r'status: "(\w+)"', self.prompt))
        self.assertLessEqual(used, {"ranked"}, f"undeclared status value(s): {used}")
        self.assertIn("do not invent a new status value", self.prompt)

    def test_the_output_carries_the_three_axis_fields(self):
        step7 = self.prompt[self.at("### Step 7:"):self.at("### Step 8:")]
        for field in ("prerank_axes", "domain_matched", "enabler_matched",
                      "core_tech_penalty"):
            with self.subTest(field=field):
                self.assertIn(field, step7,
                              "the axis breakdown is what makes the weights retunable")

    def test_the_axes_are_copied_rather_than_recomputed(self):
        """Re-deriving them in Phase 2 compares the model's reasoning against itself,
        which is precisely the comparison the logging exists to avoid."""
        self.assertIn("copied, not recomputed", self.prompt)


class ProductionSpecUntouched(unittest.TestCase):
    """The sandbox's *prompt* changes must not have reached the 08:00 scheduled run.

    `prompts/pipeline_phase1_rank.md` drives production and still carries none of the
    sandbox gates — the Python gates in `hard_gates.py` run at Phase 1b instead, so
    Phase 2 does not independently re-apply them.

    `04-job-evaluation.md` is the exception, and deliberately so: it is shared with
    /apply, /rank and /interview, and on 2026-08-21 its Language Gate was promoted at
    source to match the implementation. The tests below moved with it — they now pin the
    fixed rule rather than asserting the defect survives.
    """

    @classmethod
    def setUpClass(cls):
        cls.production = RANK_PROMPT.read_text(encoding="utf-8")
        cls.framework = FRAMEWORK.read_text(encoding="utf-8")

    def test_the_production_prompt_carries_none_of_the_sandbox_gates(self):
        for rule in ("Experience Gate", "Career-direction rule", "prerank_axes",
                     "this prompt wins"):
            with self.subTest(rule=rule):
                self.assertNotIn(rule, self.production,
                                 f"{rule!r} reached production before review")

    def test_the_framework_now_fails_a_language_it_used_to_flag(self):
        """Retargeted 2026-08-21. This used to assert the framework still carried
        "**FLAG, then proceed.** Not a fail." — the rule that drafted DHL — as a
        non-vacuity check on the sandbox prompt's override. The framework has since been
        fixed at source, so the assertion now pins the fix instead of the defect."""
        self.assertIn("**FAIL — hard stop.**", self.framework)
        self.assertIn("Hungarian at A2 is not a working level", self.framework)

    def test_the_framework_keeps_flag_for_its_one_genuine_case(self):
        """FLAG was narrowed, not deleted. A language Salman *does* work in, at a bar
        above his declared level, is still a note rather than a discard — deleting the
        verdict outright would silently drop "native-level English" roles."""
        self.assertIn("**FLAG, then proceed.**", self.framework)
        self.assertIn("Requires a language you **do** work in professionally", self.framework)

    def test_the_framework_carries_unknown_as_a_first_class_verdict(self):
        """`hard_gates.py` returns three verdicts; a doc with two of them reads
        no-evidence as PASS, which is the DHL failure in a different place."""
        self.assertIn("UNKNOWN — not a pass and not a failure", self.framework)
        self.assertIn("**`UNKNOWN` is a first-class verdict.**", self.framework)

    def test_the_two_prompts_differ_only_by_the_sandbox_work(self):
        """Cheap drift alarm. The sandbox copy started as production plus four path
        lines; anything beyond the gate work means the copies have diverged for a
        reason nobody recorded."""
        sandbox = SANDBOX_PROMPT.read_text(encoding="utf-8")
        self.assertIn("manual_run_2026-08-19/seen_jobs.json", sandbox)
        self.assertNotIn("manual_run_2026-08-19/", self.production,
                         "production must not read sandbox paths")


if __name__ == "__main__":
    unittest.main()
