"""Guards for Phase 1c LinkedIn detail enrichment.

Enrichment is the mechanism behind a promise made to the user: LinkedIn jobs rank
better because the ranker sees the *real* posting, not because anything awards them
points. Two properties keep that promise honest, and both are pinned here:

  1. **The request budget is a hard stop.** Every enrichment is one more request to
     linkedin.com on top of the day's searches. `detail_enrich_budget` bounds it, and
     `build_search_plan.py` reserves that share out of `max_requests_per_run` so the
     two halves together stay inside the ~40-60/day band the user chose.
  2. **Nothing is dropped silently.** Cards cut for budget, skipped as already-seen,
     or failed mid-fetch are counted and reported. A quiet "3 enriched" would read as
     a thin LinkedIn day when the truth is a spent budget or a broken endpoint.

A third property matters for correctness rather than safety: a failed `detail` call
must leave the job rankable from its snippet, never blank it or abort the run.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "enrich_linkedin.py"
MATRIX_PATH = REPO / "config" / "search_matrix.json"

_spec = importlib.util.spec_from_file_location("enrich_linkedin", SCRIPT)
enr = importlib.util.module_from_spec(_spec)
sys.modules["enrich_linkedin"] = enr
_spec.loader.exec_module(enr)

_bsp_spec = importlib.util.spec_from_file_location(
    "build_search_plan_for_enrich", REPO / "scripts" / "build_search_plan.py")
bsp = importlib.util.module_from_spec(_bsp_spec)
_bsp_spec.loader.exec_module(bsp)

QUERIES = ["AI Engineer", "Data Scientist", "Supply Chain Analytics",
           "Performance Management"]


def quiet(_msg):
    """Swallow warnings in tests that assert on the result, not the messaging."""


def card(job_id="4000000000", title="AI Engineer", company="Synthetic Co", **extra):
    """A LinkedIn card in aggregate_jobs.py's output shape."""
    return {"title": title, "company": company,
            "url": f"https://hu.linkedin.com/jobs/view/x-{job_id}",
            "location": "Budapest", "date_posted": "2026-08-17",
            "portal": "linkedin-search", "description_snippet": "short blurb...",
            "dedup_key": f"url:linkedin:{job_id}", **extra}


def alert_card(job_id="4123456781", title="Process Manager", company="RED Global",
               **extra):
    """A Phase 0b card as it looks after aggregation.

    Two differences from `card()` carry the whole meaning: `portal` is
    `linkedin-alert` (the only alert signal that survives normalize_job) and
    `description_snippet` is None, because the alert email carries no posting text.
    """
    return card(job_id=job_id, title=title, company=company,
                portal="linkedin-alert", description_snippet=None, **extra)


def detail(description="Full posting text. " * 20, **extra):
    """A `detail --format json` payload (helpers.ts JobDetail)."""
    return {"id": "4000000000", "title": "AI Engineer", "company": "Synthetic Co",
            "companyUrl": None, "location": "Budapest", "date": "2026-08-17",
            "url": "https://www.linkedin.com/jobs/view/4000000000",
            "description": description, "seniority": "Mid-Senior level",
            "employmentType": "Full-time", "jobFunction": "Engineering",
            "industries": "Telecommunications", "applyUrl": None, **extra}


class JobIdExtraction(unittest.TestCase):
    """The canonical key already holds the ID `detail` wants — use it, don't re-parse."""

    def test_id_comes_from_the_canonical_dedup_key(self):
        self.assertEqual(enr.linkedin_id({"dedup_key": "url:linkedin:4426311357"}),
                         "4426311357")

    def test_url_is_the_fallback_when_no_key_is_present(self):
        job = {"url": "https://de.linkedin.com/jobs/view/ai-engineer-4000000001"}
        self.assertEqual(enr.linkedin_id(job), "4000000001")

    def test_non_linkedin_jobs_have_no_id(self):
        for job in ({"dedup_key": "url:https://apply.workable.com/j/ABC"},
                    {"url": "https://www.arbeitnow.com/view/some-job-123"},
                    {"dedup_key": "ct:nokia|ai engineer"},
                    {}):
            with self.subTest(job=job):
                self.assertEqual(enr.linkedin_id(job), "")

    def test_it_reuses_the_aggregators_keying(self):
        """A second copy of the ID pattern would drift from the one dedup uses."""
        url = "https://nl.linkedin.com/jobs/view/data-scientist-4000000002?trk=x"
        self.assertEqual(f"url:linkedin:{enr.linkedin_id({'url': url})}",
                         enr._agg.make_dedup_key({"url": url}))


class TitleMatching(unittest.TestCase):
    def test_a_full_query_phrase_outranks_scattered_tokens(self):
        exact = enr.title_match_score("Senior Data Scientist", QUERIES)
        scattered = enr.title_match_score("Data Centre Engineer", QUERIES)
        self.assertGreater(exact, scattered,
                           "the phrase match must win the request")

    def test_unrelated_titles_score_zero(self):
        for title in ("Dental Hygienist", "Truck Driver", "Barista", ""):
            with self.subTest(title=title):
                self.assertEqual(enr.title_match_score(title, QUERIES), 0)

    def test_short_tokens_do_not_match_inside_words(self):
        """" ai " must not fire on "Maintenance"."""
        self.assertEqual(enr.title_match_score("Maintenance Planner", ["AI Engineer"]), 0)

    def test_punctuation_and_case_are_ignored(self):
        self.assertEqual(enr.title_match_score("AI ENGINEER", QUERIES),
                         enr.title_match_score("ai-engineer", QUERIES))

    def test_no_queries_means_no_candidates(self):
        self.assertEqual(enr.title_match_score("AI Engineer", []), 0)


class BudgetIsAHardStop(unittest.TestCase):
    def test_targets_never_exceed_the_budget(self):
        jobs = [card(job_id=f"400000000{i}") for i in range(20)]
        for budget in range(0, 12):
            with self.subTest(budget=budget):
                targets, _ = enr.select_targets(jobs, QUERIES, budget, set(), quiet)
                self.assertLessEqual(len(targets), budget)

    def test_over_budget_cards_are_counted_and_reported(self):
        warnings = []
        jobs = [card(job_id=f"400000000{i}") for i in range(10)]
        targets, stats = enr.select_targets(jobs, QUERIES, 3, set(), warnings.append)
        self.assertEqual(len(targets), 3)
        self.assertEqual(stats["over_budget"], 7)
        self.assertTrue(any("budget" in w for w in warnings),
                        f"a bounded run must say what it dropped: {warnings}")

    def test_a_budget_that_fits_everything_warns_about_nothing(self):
        warnings = []
        _, stats = enr.select_targets([card()], QUERIES, 15, set(), warnings.append)
        self.assertEqual(stats["over_budget"], 0)
        self.assertEqual(warnings, [])

    def test_zero_and_negative_budgets_fetch_nothing(self):
        for budget in (0, -5):
            with self.subTest(budget=budget):
                targets, _ = enr.select_targets([card()], QUERIES, budget, set(), quiet)
                self.assertEqual(targets, [])

    def test_search_plan_reserves_the_enrichment_share_of_the_cap(self):
        """Searches + details share one cap, or the day runs 25% over the band."""
        matrix = bsp.load_matrix(MATRIX_PATH)
        cap = matrix["linkedin"]["max_requests_per_run"]
        reserve = matrix["linkedin"]["detail_enrich_budget"]
        searches = [p for p in bsp.build_plan(matrix, 0, warn=quiet)
                    if p[1] == "linkedin"]
        self.assertLessEqual(
            len(searches) + reserve, cap,
            f"{len(searches)} searches + {reserve} details exceeds the cap of {cap}")

    def test_reserve_larger_than_the_cap_still_plans_a_search(self):
        warnings = []
        matrix = {"linkedin": {
            "enabled": True, "max_requests_per_run": 5, "detail_enrich_budget": 50,
            "always_include_geos": ["Hungary"], "geos": ["Hungary", "Germany"],
            "tracks": {"T1": {"enabled": True, "queries": ["AI Engineer"]}}}}
        plan = bsp.build_plan(matrix, 0, warn=warnings.append)
        self.assertEqual(len(plan), 1, "enrichment needs something to enrich")
        self.assertTrue(any("detail_enrich_budget" in w for w in warnings),
                        f"the misconfiguration must be reported: {warnings}")


class SelectionSpendsNothingItCanSkip(unittest.TestCase):
    def test_non_linkedin_jobs_are_never_targeted(self):
        jobs = [{"title": "Data Scientist", "portal": "arbeitnow-search",
                 "url": "https://www.arbeitnow.com/view/x-1",
                 "dedup_key": "url:https://www.arbeitnow.com/view/x-1"}]
        targets, stats = enr.select_targets(jobs, QUERIES, 15, set(), quiet)
        self.assertEqual(targets, [])
        self.assertEqual(stats["linkedin_cards"], 0)

    def test_already_seen_cards_cost_no_request(self):
        """The ranker drops them as duplicates, so their description buys nothing."""
        jobs = [card(job_id="4000000000"), card(job_id="4000000001")]
        targets, stats = enr.select_targets(
            jobs, QUERIES, 15, {"url:linkedin:4000000000"}, quiet)
        self.assertEqual([t[1] for t in targets], ["4000000001"])
        self.assertEqual(stats["already_seen"], 1)

    def test_cards_that_already_carry_a_description_are_skipped(self):
        jobs = [card(description="already full text here")]
        targets, stats = enr.select_targets(jobs, QUERIES, 15, set(), quiet)
        self.assertEqual(targets, [])
        self.assertEqual(stats["already_full"], 1)

    def test_off_track_titles_are_skipped_and_counted(self):
        jobs = [card(title="Dental Hygienist"), card(job_id="4000000001")]
        targets, stats = enr.select_targets(jobs, QUERIES, 15, set(), quiet)
        self.assertEqual(len(targets), 1)
        self.assertEqual(stats["no_title_match"], 1)

    def test_an_unparseable_linkedin_url_is_reported_not_swallowed(self):
        warnings = []
        jobs = [{"title": "AI Engineer", "portal": "linkedin-search",
                 "url": "https://www.linkedin.com/jobs/collections/recommended"}]
        _, stats = enr.select_targets(jobs, QUERIES, 15, set(), warnings.append)
        self.assertEqual(stats["unparseable_id"], 1)
        self.assertTrue(warnings, "a LinkedIn card we cannot enrich must be visible")

    def test_selection_is_deterministic_and_best_first(self):
        jobs = [card(job_id="4000000000", title="Data Centre Engineer"),
                card(job_id="4000000001", title="Data Scientist"),
                card(job_id="4000000002", title="AI Engineer")]
        first = enr.select_targets(jobs, QUERIES, 2, set(), quiet)[0]
        second = enr.select_targets(jobs, QUERIES, 2, set(), quiet)[0]
        self.assertEqual([t[1] for t in first], [t[1] for t in second])
        self.assertNotIn("4000000000", [t[1] for t in first],
                         "the weakest title must lose the budget")


class MergeBehaviour(unittest.TestCase):
    def test_description_and_metadata_land_on_the_job(self):
        job = card()
        self.assertTrue(enr.merge_detail(job, detail(description="Real posting text")))
        self.assertEqual(job["description"], "Real posting text")
        self.assertEqual(job["seniority"], "Mid-Senior level")
        self.assertEqual(job["employmentType"], "Full-time")
        self.assertTrue(job["enriched"])

    def test_the_snippet_survives_so_a_reader_can_compare(self):
        job = card()
        enr.merge_detail(job, detail())
        self.assertEqual(job["description_snippet"], "short blurb...",
                         "enrichment adds evidence, it does not destroy any")

    def test_an_empty_description_leaves_the_job_rankable(self):
        job = card()
        self.assertFalse(enr.merge_detail(job, detail(description=None)))
        self.assertNotIn("description", job)
        self.assertFalse(job["enriched"])
        self.assertEqual(job["description_snippet"], "short blurb...")

    def test_null_metadata_fields_are_not_written(self):
        job = card()
        enr.merge_detail(job, detail(seniority=None, industries="   "))
        self.assertNotIn("seniority", job)
        self.assertNotIn("industries", job)

    def test_a_long_description_is_truncated_visibly(self):
        job = card()
        cap = enr.max_description_chars(job)
        enr.merge_detail(job, detail(description="x" * (cap + 500)))
        self.assertLessEqual(len(job["description"]), cap + 1)
        self.assertTrue(job["description_truncated"],
                        "an invisible cut would look like a short posting")

    def test_a_linkedin_body_under_the_new_cap_is_no_longer_cut(self):
        """The regression the cap lift exists to prevent.

        6,260 / 10,455 / 19,460 chars are the three real bodies measured once the
        extractor foregrounded the tab; every one was being cut at 6000, and a cut
        body is capped at UNKNOWN by `hard_gates._unverified()`. Asserting against a
        literal rather than the old constant is the point - the number has to stay
        dead even if a constant by that name is reintroduced.
        """
        job = card()
        enr.merge_detail(job, detail(description="x" * 19460))
        self.assertEqual(len(job["description"]), 19460)
        self.assertNotIn("description_truncated", job)

    def test_non_linkedin_hosts_keep_the_original_cap(self):
        """The lift is host-aware: only LinkedIn's fetch path changed.

        Nothing routes a non-LinkedIn card through this module today. The case is
        here so that if something ever does, it inherits 6000 rather than silently
        picking up LinkedIn's ceiling.
        """
        job = card()
        job["url"] = "https://arbeitnow.com/view/ai-engineer-123"
        job["dedup_key"] = "url:arbeitnow:123"
        self.assertEqual(enr.max_description_chars(job), enr.MAX_DESCRIPTION_CHARS)
        enr.merge_detail(job, detail(description="x" * 6500))
        self.assertLessEqual(len(job["description"]), enr.MAX_DESCRIPTION_CHARS + 1)
        self.assertTrue(job["description_truncated"])

    def test_a_short_description_is_not_flagged_as_truncated(self):
        job = card()
        enr.merge_detail(job, detail(description="brief but complete"))
        self.assertNotIn("description_truncated", job)


class EnrichmentNeverSubtractsEvidence(unittest.TestCase):
    """A `detail` body shorter than the snippet is refused.

    The snippet is a 500-char *prefix* of the same posting (`aggregate_jobs.py:50`),
    so a shorter `detail` response is a block page or a trimmed render, never a
    shorter posting. Refusing it matters more since enrichment moved ahead of the
    final cut: overwriting 500 chars with 80 lowers the job's own two-axis score and
    can cut a job from the rankset it had already earned on the fuller text. Under
    the old order the same overwrite could only thin the ranker's evidence.
    """

    def long_snippet(self, chars=500):
        return card(description_snippet="x" * chars + "...")

    def test_the_marker_is_not_counted_as_posting_text(self):
        """`...` is aggregate_jobs.py's truncation bookkeeping, not the posting."""
        self.assertEqual(enr.snippet_text(card(description_snippet="abc...")), "abc")
        self.assertEqual(enr.snippet_text(card(description_snippet="abc")), "abc")
        self.assertEqual(enr.snippet_text({}), "")

    def test_a_body_shorter_than_the_snippet_is_refused(self):
        job = self.long_snippet()
        landed = enr.merge_detail(job, detail(description="Sign in to view this job."))
        self.assertFalse(landed)
        self.assertNotIn("description", job)
        self.assertTrue(job["description_degraded"])
        self.assertFalse(job["enriched"])

    def test_the_refusal_says_both_lengths(self):
        """A silent refusal reads as a posting that simply had no body."""
        warnings = []
        enr.merge_detail(self.long_snippet(), detail(description="too short"),
                         warnings.append)
        self.assertEqual(len(warnings), 1)
        self.assertIn("9", warnings[0])
        self.assertIn("500", warnings[0])

    def test_a_longer_body_still_lands(self):
        job = self.long_snippet(chars=100)
        self.assertTrue(enr.merge_detail(job, detail(description="y" * 400)))
        self.assertEqual(job["description"], "y" * 400)
        self.assertNotIn("description_degraded", job)

    def test_an_equal_length_body_lands(self):
        """Only strictly shorter is suspicious: a posting under 500 chars arrives
        whole, and its snippet is the same string."""
        job = card(description_snippet="z" * 40)
        self.assertTrue(enr.merge_detail(job, detail(description="z" * 40)))
        self.assertEqual(job["description"], "z" * 40)

    def test_a_card_with_no_snippet_accepts_anything(self):
        """Alert cards have no snippet at all — any body is more than nothing."""
        job = card(description_snippet=None)
        self.assertTrue(enr.merge_detail(job, detail(description="brief")))
        self.assertEqual(job["description"], "brief")

    def test_metadata_still_lands_when_the_body_is_refused(self):
        """Seniority and employment type came from the same response and are not
        length-suspect; discarding them would waste the request entirely."""
        job = self.long_snippet()
        enr.merge_detail(job, detail(description="short"))
        self.assertEqual(job["seniority"], "Mid-Senior level")

    def test_degraded_is_counted_apart_from_empty(self):
        """They mean different things: empty is a posting with no body to give,
        degraded is a body that was offered and refused."""
        jobs = [self.long_snippet(), card(description_snippet="brief...")]
        targets = [(jobs[0], "4123456781"), (jobs[1], "4123456782")]
        bodies = {"4123456781": detail(description="Sign in to view"),
                  "4123456782": detail(description=None)}
        counts = enr.enrich(targets, 0, quiet, fetch=lambda i: bodies[i],
                            sleep=lambda _: None)
        self.assertEqual(counts["degraded"], 1)
        self.assertEqual(counts["empty"], 1)
        self.assertEqual(counts["enriched"], 0)


class FailureIsNonFatal(unittest.TestCase):
    def test_a_failed_fetch_leaves_the_job_with_its_snippet(self):
        warnings = []
        job = card()

        def boom(_job_id):
            raise enr.DetailError("429 after retries")

        counts = enr.enrich([(job, "4000000000")], 0, warnings.append,
                            fetch=boom, sleep=lambda _s: None)
        self.assertEqual(counts["failed"], 1)
        self.assertNotIn("description", job)
        self.assertEqual(job["description_snippet"], "short blurb...")
        self.assertTrue(any("429" in w for w in warnings),
                        f"the reason must reach the log: {warnings}")

    def test_one_failure_does_not_stop_the_rest(self):
        jobs = [(card(job_id="4000000000"), "4000000000"),
                (card(job_id="4000000001"), "4000000001")]

        def flaky(job_id):
            if job_id == "4000000000":
                raise enr.DetailError("timed out")
            return detail(description="Second job's real text")

        counts = enr.enrich(jobs, 0, quiet, fetch=flaky, sleep=lambda _s: None)
        self.assertEqual((counts["enriched"], counts["failed"]), (1, 1))
        self.assertEqual(jobs[1][0]["description"], "Second job's real text")

    def test_requests_are_spaced_by_the_configured_delay(self):
        slept = []
        jobs = [(card(job_id=f"400000000{i}"), f"400000000{i}") for i in range(3)]
        enr.enrich(jobs, 4.0, quiet, fetch=lambda _i: detail(), sleep=slept.append)
        self.assertEqual(slept, [4.0, 4.0],
                         "one delay between each pair, none before the first")

    def test_cli_error_json_is_surfaced_with_its_code(self):
        stderr = '{"error":"Job not found","code":"NOT_FOUND"}\n'
        self.assertIn("NOT_FOUND", enr._cli_error(stderr))
        self.assertIn("Job not found", enr._cli_error(stderr))

    def test_non_json_stderr_still_yields_something_readable(self):
        self.assertIn("bun: command not found",
                      enr._cli_error("bun: command not found\n"))

    def test_missing_bun_is_a_detail_error_not_a_crash(self):
        original = enr.subprocess.run

        def missing(*_a, **_kw):
            raise FileNotFoundError("bun")

        enr.subprocess.run = missing
        try:
            with self.assertRaises(enr.DetailError) as ctx:
                enr.fetch_detail("4000000000")
            self.assertIn("bun", str(ctx.exception))
        finally:
            enr.subprocess.run = original


class CliBehaviour(unittest.TestCase):
    def _jobs_file(self, directory, jobs):
        path = Path(directory) / "jobs.json"
        path.write_text(json.dumps({"meta": {"unique": len(jobs)}, "results": jobs}))
        return path

    def _run(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                              capture_output=True, text=True)

    def test_dry_run_fetches_nothing_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._jobs_file(d, [card()])
            before = path.read_text()
            proc = self._run("--jobs", path, "--matrix", MATRIX_PATH,
                             "--seen", Path(d) / "absent.json", "--dry-run")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(path.read_text(), before)
            summary = json.loads(proc.stdout)
            self.assertTrue(summary["dry_run"])
            self.assertEqual(summary["targeted"], 1)

    def test_budget_zero_makes_it_a_no_op(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._jobs_file(d, [card()])
            before = path.read_text()
            proc = self._run("--jobs", path, "--matrix", MATRIX_PATH,
                             "--seen", Path(d) / "absent.json", "--budget", 0)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["targeted"], 0)
            self.assertEqual(path.read_text(), before,
                             "nothing to enrich means nothing to rewrite")

    def test_a_jobs_file_with_no_linkedin_cards_is_a_clean_no_op(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._jobs_file(d, [{"title": "Data Analyst",
                                        "portal": "freehire-search",
                                        "url": "https://apply.workable.com/j/AB",
                                        "dedup_key": "url:https://apply.workable.com/j/AB"}])
            proc = self._run("--jobs", path, "--matrix", MATRIX_PATH,
                             "--seen", Path(d) / "absent.json")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["linkedin_cards"], 0)

    def test_missing_jobs_file_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            proc = self._run("--jobs", Path(d) / "absent.json", "--matrix", MATRIX_PATH)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("could not read", proc.stderr)

    def test_wrong_shape_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "jobs.json"
            path.write_text(json.dumps({"jobs": []}))
            proc = self._run("--jobs", path, "--matrix", MATRIX_PATH)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("results", proc.stderr)

    def test_a_missing_seen_file_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._jobs_file(d, [card()])
            proc = self._run("--jobs", path, "--matrix", MATRIX_PATH,
                             "--seen", Path(d) / "absent.json", "--dry-run")
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_summary_on_stdout_is_machine_readable(self):
        """run_daily.sh parses this to build the report's warning line."""
        with tempfile.TemporaryDirectory() as d:
            path = self._jobs_file(d, [card()])
            proc = self._run("--jobs", path, "--matrix", MATRIX_PATH,
                             "--seen", Path(d) / "absent.json", "--budget", 0)
            summary = json.loads(proc.stdout)
            for field in ("targeted", "enriched", "failed", "empty",
                          "over_budget", "already_seen", "linkedin_cards"):
                self.assertIn(field, summary)


class AlertCardsLeadTheirBand(unittest.TestCase):
    """Phase 0b's cards go ahead of search cards *within a band*, and skip the title filter.

    The title-filter exemption is about vocabulary: the alerts are worth reading
    precisely because they use words the 13 track queries do not, so filtering them on
    query overlap would drop nearly every one of them.

    The ordering half is a tie-break, not a tier, and that is a correction. It used to
    be a tier, justified by "a search card arrives with a 500-char snippet and an alert
    card arrives with nothing". The snippet half of that is false: the `linkedin-search`
    CLI's `search` command returns no description, so on the 2026-08-19 corpus all 371
    LinkedIn search cards had a null snippet too. Every card in this loop is equally
    blind, so `TheHalfHybridBandClaimsTheBudgetFirst` decides the order and this only
    breaks ties inside it.

    Dedup order is why alert priority is load-bearing at all: the alert portal file
    sorts ahead of the search files, so when both found the same posting the *alert*
    entry wins the key. Skip the detail call and that job reaches the ranker with less
    evidence than if the alert had never fired.

    Priority is attention, never approval. Phase 2 still scores these on merit and
    gate_jobs.py still decides whether any documents get written.
    """

    def test_the_portal_name_is_the_only_signal_it_needs(self):
        # aggregate_jobs.normalize_job() rebuilds every result into a fixed schema and
        # drops alert_name/alert_track, so `portal` is all that reaches the corpus.
        self.assertTrue(enr.is_alert_sourced(alert_card()))
        self.assertFalse(enr.is_alert_sourced(card()))
        self.assertFalse(enr.is_alert_sourced({}))
        self.assertTrue(enr.is_alert_sourced({"portal": " LinkedIn-Alert "}))

    def test_an_alert_card_is_fetched_even_with_a_zero_scoring_title(self):
        """RED Global's "Process Manager": score 0 against every track query.

        The alerts are worth reading precisely because they use vocabulary the queries
        do not, so filtering them on query overlap would drop nearly every one of them
        — the exact opposite of the priority they are meant to get.
        """
        self.assertEqual(enr.title_match_score("Process Manager", QUERIES), 0)
        jobs = [alert_card(job_id="4123456781", title="Process Manager")]
        targets, stats = enr.select_targets(jobs, QUERIES, 15, set(), quiet)
        self.assertEqual([t[1] for t in targets], ["4123456781"])
        self.assertEqual(stats["no_title_match"], 0)
        self.assertEqual(stats["alert_targets"], 1)

    def test_alert_cards_outrank_a_perfect_title_match(self):
        jobs = [card(job_id="4000000001", title="AI Engineer"),
                alert_card(job_id="4123456781", title="Process Manager"),
                card(job_id="4000000002", title="Data Scientist"),
                alert_card(job_id="4123456784",
                           title="Operations Manager , FC Operations")]
        order = [t[1] for t in enr.select_targets(jobs, QUERIES, 15, set(), quiet)[0]]
        # No card here carries the two-axis keys, so every one is outside the
        # half-hybrid band and the alert tie-break is what orders them. Ordering
        # *within* the search cards is by title score and is covered by
        # test_selection_is_deterministic_and_best_first; asserting it again here
        # would just re-encode the scoring weights.
        self.assertEqual(order[:2], ["4123456781", "4123456784"],
                         "both alert cards must precede both search cards, however "
                         "well the search titles match")
        self.assertEqual(set(order[2:]), {"4000000001", "4000000002"})

    def test_alert_cards_claim_the_budget_before_search_cards(self):
        jobs = [card(job_id=f"400000000{i}", title="AI Engineer") for i in range(3)]
        jobs.append(alert_card(job_id="4123456781", title="Process Manager"))
        targets, stats = enr.select_targets(jobs, QUERIES, 1, set(), quiet)
        self.assertEqual([t[1] for t in targets], ["4123456781"])
        self.assertEqual(stats["over_budget"], 3)
        self.assertEqual(stats["alert_over_budget"], 0)

    def test_a_cut_alert_card_gets_its_own_warning(self):
        """A cut alert card reaches the ranker on a bare title, which is worth saying.

        Not because a cut search card fares better — it does not, LinkedIn search cards
        carry no snippet either — but because the alerts are Salman's own stated
        interests, and "your alert fired and nothing read the posting" is a different
        operational fact from "the budget ran out".
        """
        warnings = []
        jobs = [alert_card(job_id=f"41234567{80 + i}", title="Process Manager")
                for i in range(4)]
        targets, stats = enr.select_targets(jobs, QUERIES, 2, set(), warnings.append)
        self.assertEqual(len(targets), 2)
        self.assertEqual(stats["alert_over_budget"], 2)
        self.assertTrue(any("title alone" in w for w in warnings),
                        "an alert card reaching the ranker with no description at all "
                        "must not be reported as an ordinary budget cut")

    def test_ordering_among_alert_cards_stays_deterministic(self):
        jobs = [alert_card(job_id="4123456783", title="Process Manager"),
                alert_card(job_id="4123456781", title="AI Engineer"),
                alert_card(job_id="4123456782", title="Continuous Improvement Manager")]
        first = [t[1] for t in enr.select_targets(jobs, QUERIES, 3, set(), quiet)[0]]
        second = [t[1] for t in enr.select_targets(jobs, QUERIES, 3, set(), quiet)[0]]
        self.assertEqual(first, second)
        self.assertEqual(first[0], "4123456781",
                         "among alert cards, title match still breaks the tie")

    def test_an_already_seen_alert_card_still_costs_nothing(self):
        """Priority does not override the cheaper skips ahead of it."""
        jobs = [alert_card(job_id="4123456781")]
        targets, stats = enr.select_targets(
            jobs, QUERIES, 15, {"url:linkedin:4123456781"}, quiet)
        self.assertEqual(targets, [])
        self.assertEqual(stats["already_seen"], 1)
        self.assertEqual(stats["alert_targets"], 0)

    def test_an_alert_card_that_already_has_a_description_is_not_refetched(self):
        jobs = [alert_card(job_id="4123456781", description="already full text")]
        targets, stats = enr.select_targets(jobs, QUERIES, 15, set(), quiet)
        self.assertEqual(targets, [])
        self.assertEqual(stats["already_full"], 1)

    def _dry_run(self, directory, matrix_config, jobs):
        matrix = Path(directory) / "matrix.json"
        matrix.write_text(json.dumps(matrix_config))
        jobs_file = Path(directory) / "jobs.json"
        jobs_file.write_text(json.dumps({"meta": {}, "results": jobs}))
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--jobs", str(jobs_file),
             "--matrix", str(matrix), "--seen", str(Path(directory) / "absent.json"),
             "--dry-run"], capture_output=True, text=True)

    def test_disabling_linkedin_stops_alert_requests_too(self):
        """The switch is honored here or it is honored nowhere.

        Before alert cards bypassed the title filter, an empty `queries` list zeroed
        the selection incidentally: every card scored 0 and fell to no_title_match.
        The exemption removed that accident, and run_daily.sh reads
        detail_enrich_budget straight out of the matrix without consulting `enabled`.
        """
        with tempfile.TemporaryDirectory() as d:
            proc = self._dry_run(
                d, {"linkedin": {"enabled": False, "detail_enrich_budget": 15,
                                 "tracks": {"T1": {"enabled": True,
                                                   "queries": ["AI Engineer"]}}}},
                [alert_card(job_id="4123456781")])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["targeted"], 0)
            self.assertIn("disabled", proc.stderr)

    def test_with_no_enabled_tracks_alert_cards_are_still_enriched(self):
        """Host enabled, every track off: search cards have nothing left to match
        against, but alert cards need no match — the title filter exempts them."""
        with tempfile.TemporaryDirectory() as d:
            proc = self._dry_run(
                d, {"linkedin": {"enabled": True, "detail_enrich_budget": 15,
                                 "tracks": {"T1": {"enabled": False,
                                                   "queries": ["AI Engineer"]}}}},
                [alert_card(job_id="4123456781"),
                 card(job_id="4000000001", title="AI Engineer")])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            summary = json.loads(proc.stdout)
            self.assertEqual(summary["targeted"], 1)
            self.assertEqual(summary["alert_targets"], 1)
            self.assertEqual(summary["no_title_match"], 1)

    def test_the_summary_reports_the_alert_counters(self):
        """run_daily.sh and the report read these; absent keys would read as zeroes."""
        with tempfile.TemporaryDirectory() as d:
            jobs_file = Path(d) / "jobs.json"
            jobs_file.write_text(json.dumps(
                {"meta": {}, "results": [alert_card(job_id="4123456781")]}))
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--jobs", str(jobs_file),
                 "--matrix", str(MATRIX_PATH), "--seen", str(Path(d) / "absent.json"),
                 "--dry-run"], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            summary = json.loads(proc.stdout)
            self.assertEqual(summary["alert_targets"], 1)
            self.assertIn("alert_over_budget", summary)


class TheHalfHybridBandClaimsTheBudgetFirst(unittest.TestCase):
    """The band enrich-before-cut exists to rescue, and its place at the head of the queue.

    A domain-only card is one the two-axis pre-rank matched on a business domain and
    on no technical enabler: "Continuous Improvement Manager", "Advanced PMO
    Specialist - Sourcing & Procurement Excellence", "Quality Performance Manager".
    The AI/data half of the profile is either in the body or not there at all, and
    only a request can tell which. On the 2026-08-19 corpus that was 102 postings,
    18% of everything fetched, and their best rank was 31st — so a 25-slot cut
    reached none of them and no later re-score could help, because the text had never
    been fetched.

    They go first, ahead of every card outside the band, because a request here buys
    an answer while a request on a card that already showed both halves only confirms
    one. That is a correction: the band used to sit *behind* every alert card, on the
    theory that an alert card had no snippet and a domain-only search card had 500
    chars. Both are blind in fact — LinkedIn's `search` command returns no description
    — and the cost of the wrong order was the whole budget. On the 2026-08-19
    shortlist, 26 alert cards took all 15 requests, five of them for cards showing
    both halves or neither, and the band got nothing.
    """

    def domain_card(self, job_id="4000000010", title="Continuous Improvement Manager",
                    domain=("continuous_improvement",), enabler=(), **extra):
        return card(job_id=job_id, title=title,
                    prerank={"score": 30, "domain_matched": list(domain),
                             "enabler_matched": list(enabler)}, **extra)

    def test_domain_only_is_domain_without_enabler(self):
        self.assertTrue(enr.is_domain_only(self.domain_card()))
        self.assertFalse(
            enr.is_domain_only(self.domain_card(domain=("process",), enabler=("ai",))),
            "a hybrid title already showed both axes; its body has less to add")
        self.assertFalse(enr.is_domain_only(self.domain_card(domain=())),
                         "no domain either — nothing to confirm")

    def test_the_original_scoring_model_produces_no_domain_only_cards(self):
        """So this band is inert on the production path until two-axis is turned on.

        The query-match model writes `prerank` without the axis keys. Treating that
        absence as "no enabler found" would silently re-prioritize every card in the
        scheduled 08:00 run, which is the one thing the sandbox work must not do.
        """
        self.assertFalse(enr.is_domain_only(
            card(prerank={"score": 30, "track_guess": "T5_process_perf",
                          "selected": True})))
        self.assertFalse(enr.is_domain_only(card()), "no prerank block at all")

    def test_a_domain_only_card_is_fetched_even_with_a_zero_scoring_title(self):
        """The exemption that makes the band mean anything.

        "Advanced PMO Specialist - Sourcing & Procurement Excellence" scores 0
        against all 13 track queries and matches the procurement and process
        categories. Asking the search queries for permission to read its body
        re-imposes the exact vocabulary gap the two-axis model was built to escape.
        """
        title = "Advanced PMO Specialist - Sourcing & Procurement Excellence"
        self.assertEqual(enr.title_match_score(title, QUERIES), 0)
        jobs = [self.domain_card(job_id="4000000011", title=title,
                                 domain=("procurement", "process"))]
        targets, stats = enr.select_targets(jobs, QUERIES, 15, set(), quiet)
        self.assertEqual([t[1] for t in targets], ["4000000011"])
        self.assertEqual(stats["no_title_match"], 0)
        self.assertEqual(stats["domain_only_targets"], 1)
        self.assertEqual(stats["half_hybrid_targets"], 1)

    def test_the_band_comes_before_alert_and_plain_cards_alike(self):
        jobs = [card(job_id="4000000001", title="AI Engineer"),
                self.domain_card(job_id="4000000010"),
                alert_card(job_id="4123456781", title="Process Manager"),
                card(job_id="4000000002", title="Data Scientist")]
        order = [t[1] for t in enr.select_targets(jobs, QUERIES, 15, set(), quiet)[0]]
        self.assertEqual(order[0], "4000000010",
                         "the only card whose missing half a request would settle")
        self.assertEqual(order[1], "4123456781",
                         "outside the band, the alert tie-break orders the rest")
        self.assertEqual(set(order[2:]), {"4000000001", "4000000002"})

    def test_a_domain_only_card_claims_the_budget_before_a_search_card(self):
        jobs = [card(job_id=f"400000000{i}", title="AI Engineer") for i in range(3)]
        jobs.append(self.domain_card(job_id="4000000010"))
        targets, stats = enr.select_targets(jobs, QUERIES, 1, set(), quiet)
        self.assertEqual([t[1] for t in targets], ["4000000010"])
        self.assertEqual(stats["over_budget"], 3)
        self.assertEqual(stats["domain_only_targets"], 1)

    def test_a_domain_only_card_outranks_an_alert_card_outside_the_band(self):
        """The regression the 2026-08-19 shortlist exposed, pinned.

        With alerts as an absolute tier, this assertion was inverted and the effect at
        corpus scale was total: 26 alert cards consumed all 15 requests and every
        half-hybrid card was cut. The alert card here shows both halves already, so its
        request would confirm what the title said; the domain-only card's request is
        the one that can change an answer.
        """
        jobs = [self.domain_card(job_id="4000000010"),
                alert_card(job_id="4123456781", title="Process Manager",
                           prerank={"score": 90, "domain_matched": ["process"],
                                    "enabler_matched": ["automation"]})]
        targets, stats = enr.select_targets(jobs, QUERIES, 1, set(), quiet)
        self.assertEqual([t[1] for t in targets], ["4000000010"])
        self.assertEqual(stats["half_hybrid_targets"], 1)
        self.assertEqual(stats["alert_targets"], 0)

    def test_an_alert_card_inside_the_band_still_leads_it(self):
        """Alert priority survives as the tie-break it now is."""
        jobs = [self.domain_card(job_id="4000000010"),
                alert_card(job_id="4123456781", title="Quality Performance Manager",
                           prerank={"score": 30, "domain_matched": ["performance"],
                                    "enabler_matched": []})]
        targets, stats = enr.select_targets(jobs, QUERIES, 1, set(), quiet)
        self.assertEqual([t[1] for t in targets], ["4123456781"])
        self.assertEqual(stats["alert_targets"], 1)
        self.assertEqual(stats["half_hybrid_targets"], 1)

    def test_a_cut_domain_only_card_gets_its_own_warning(self):
        """The shortlist widened the net for these. Cutting one here means the net
        caught it and nothing read it, so it is re-scored on its title regardless —
        worth saying out loud rather than folding into the budget count."""
        warnings = []
        jobs = [self.domain_card(job_id=f"40000000{10 + i}") for i in range(4)]
        targets, _ = enr.select_targets(jobs, QUERIES, 2, set(), warnings.append)
        self.assertEqual(len(targets), 2)
        self.assertTrue(any("domain-only" in w for w in warnings), warnings)

    def test_ordering_within_the_band_stays_deterministic(self):
        jobs = [self.domain_card(job_id="4000000012",
                                 title="Quality Performance Manager"),
                self.domain_card(job_id="4000000011",
                                 title="Performance Management Lead"),
                self.domain_card(job_id="4000000013",
                                 title="Continuous Improvement Lead")]
        first = [t[1] for t in enr.select_targets(jobs, QUERIES, 3, set(), quiet)[0]]
        second = [t[1] for t in enr.select_targets(jobs, QUERIES, 3, set(), quiet)[0]]
        self.assertEqual(first, second)
        self.assertEqual(first[0], "4000000011",
                         "inside the band, title match still breaks the tie")

    def test_the_cheaper_skips_still_come_first(self):
        """Priority buys a place in the queue, never a wasted request."""
        already = self.domain_card(job_id="4000000010", description="full text")
        seen = self.domain_card(job_id="4000000011")
        targets, stats = enr.select_targets(
            [already, seen], QUERIES, 15, {"url:linkedin:4000000011"}, quiet)
        self.assertEqual(targets, [])
        self.assertEqual(stats["already_full"], 1)
        self.assertEqual(stats["already_seen"], 1)
        self.assertEqual(stats["domain_only_targets"], 0)


class BothDirectionsShareTheBand(unittest.TestCase):
    """The other half of the same band, and the one the old allocator could not see.

    Citi's "Digital Transformation Senior Analyst" matches the `digital` enabler and
    no business domain on its title. It landed 23rd of the 41 alert jobs, while its
    real description carries business analysis and continuous improvement — the hybrid
    that makes it a match. The band that existed looked only for a missing *enabler*,
    so a card missing the *domain* fell through and competed with jobs whose bodies had
    nothing left to reveal.

    The rule is symmetric because the target profile is a combination: exactly one
    half present means one request decides it. Both halves present is a case already
    made; neither half present is nothing to complete.
    """

    def half(self, domain=(), enabler=(), job_id="4000000020",
             title="Digital Transformation Senior Analyst", **extra):
        return card(job_id=job_id, title=title,
                    prerank={"score": 30, "domain_matched": list(domain),
                             "enabler_matched": list(enabler)}, **extra)

    def test_missing_half_names_which_side_is_absent(self):
        self.assertEqual(enr.missing_half(self.half(enabler=("digital",))), "domain")
        self.assertEqual(enr.missing_half(self.half(domain=("process",))), "enabler")

    def test_a_card_with_both_halves_is_not_in_the_band(self):
        """It already made its case; a request would confirm, not discover."""
        self.assertEqual(
            enr.missing_half(self.half(domain=("process",), enabler=("ai",))), "")

    def test_a_card_with_neither_half_is_not_in_the_band(self):
        """Nothing to complete. It falls through to the title-match ordering."""
        self.assertEqual(enr.missing_half(self.half()), "")

    def test_the_original_scoring_model_produces_no_half_hybrid_cards(self):
        """So the band stays inert on the production path until two-axis is on.

        The query-match model writes `prerank` without the axis keys. Reading that
        absence as "no enabler found" would re-prioritize every card in the scheduled
        08:00 run — the one thing the sandbox work must not do.
        """
        self.assertEqual(enr.missing_half(
            card(prerank={"score": 30, "track_guess": "T5_process_perf"})), "")
        self.assertEqual(enr.missing_half(card()), "", "no prerank block at all")

    def test_the_citi_card_claims_the_budget_before_a_plain_search_card(self):
        """The concrete rescue. On title alone this card loses; it should not."""
        jobs = [card(job_id="4000000001", title="AI Engineer"),
                self.half(enabler=("digital",), job_id="4000000020")]
        targets, stats = enr.select_targets(jobs, QUERIES, 1, set(), quiet)
        self.assertEqual([t[1] for t in targets], ["4000000020"])
        self.assertEqual(stats["missing_domain_targets"], 1)
        self.assertEqual(stats["half_hybrid_targets"], 1)

    def test_both_directions_sit_in_one_band_ahead_of_everything_else(self):
        jobs = [card(job_id="4000000001", title="AI Engineer"),
                self.half(enabler=("digital",), job_id="4000000020"),
                self.half(domain=("continuous_improvement",), job_id="4000000021",
                          title="Continuous Improvement Manager"),
                alert_card(job_id="4123456781", title="Process Manager")]
        order = [t[1] for t in enr.select_targets(jobs, QUERIES, 15, set(), quiet)[0]]
        self.assertEqual(set(order[:2]), {"4000000020", "4000000021"},
                         "neither direction may be separated from the other")
        self.assertEqual(order[2], "4123456781")
        self.assertEqual(order[3], "4000000001")

    def test_the_counts_break_the_band_down_by_direction(self):
        """One number for the band hides which side of the profile is being read."""
        jobs = [self.half(enabler=("digital",), job_id="4000000020"),
                self.half(domain=("process",), job_id="4000000021"),
                self.half(domain=("procurement",), job_id="4000000022")]
        _, stats = enr.select_targets(jobs, QUERIES, 15, set(), quiet)
        self.assertEqual(stats["half_hybrid_targets"], 3)
        self.assertEqual(stats["missing_domain_targets"], 1)
        self.assertEqual(stats["missing_enabler_targets"], 2)
        self.assertEqual(stats["domain_only_targets"], 2,
                         "the old key keeps counting the enabler-missing band")

    def test_a_cut_half_hybrid_card_gets_its_own_warning(self):
        warnings = []
        jobs = [self.half(enabler=("digital",), job_id=f"40000000{20 + i}")
                for i in range(4)]
        targets, _ = enr.select_targets(jobs, QUERIES, 2, set(), warnings.append)
        self.assertEqual(len(targets), 2)
        self.assertTrue(any("half-hybrid" in w for w in warnings), warnings)


class UnverifiedGatesGoFirstWithinABand(unittest.TestCase):
    """A request that answers two questions beats one that answers half of one.

    The hard gates run at the shortlist stage, before this budget is divided, so a
    FAIL never reaches here — it was discarded on quotable wording. What does reach
    here is UNKNOWN: the language and experience risk is unverified because there was
    no body to read. That request buys the hybrid answer *and* the eligibility answer.

    Below the rank cut this is a tie-break and not a tier, and that is what these
    tests pin. On an unenriched corpus nearly every card is UNKNOWN — 65 of the 80
    shortlisted on 2026-08-22 — so promoting on it alone would order the queue by
    nothing. *Inside* the cut it is the first tier, because "unverified and about to be
    deep-ranked" is a far smaller set; `VerificationComesBeforeDiscovery` pins that
    half. Every fixture here therefore passes `cut=0`, which empties the verification
    tier and leaves the discovery ordering these tests were written for.
    """

    def gated(self, overall, job_id, title="Continuous Improvement Manager",
              domain=("continuous_improvement",)):
        return card(job_id=job_id, title=title,
                    prerank={"score": 30, "domain_matched": list(domain),
                             "enabler_matched": [],
                             "gates": {"overall": overall, "failed": []}})

    def test_gate_unknown_reads_the_prerank_verdict(self):
        self.assertTrue(enr.gate_unknown(self.gated("UNKNOWN", "4000000030")))
        self.assertFalse(enr.gate_unknown(self.gated("PASS", "4000000031")))
        self.assertFalse(enr.gate_unknown(card()), "no gates block recorded at all")

    def test_an_unknown_card_outranks_a_passing_one_in_the_same_band(self):
        jobs = [self.gated("PASS", "4000000031"),
                self.gated("UNKNOWN", "4000000030")]
        targets, stats = enr.select_targets(jobs, QUERIES, 1, set(), quiet, 0)
        self.assertEqual([t[1] for t in targets], ["4000000030"])
        self.assertEqual(stats["gate_unknown_targets"], 1)

    def test_it_does_not_promote_across_the_band_boundary(self):
        """An UNKNOWN plain search card must not overtake a half-hybrid card.

        Outside the rank cut, that is. Otherwise the tie-break becomes the band, and on
        a corpus where every card is UNKNOWN that erases the band this stage exists to
        serve. Inside the cut the priority is deliberately inverted — see
        `VerificationComesBeforeDiscovery.test_the_tier_outranks_the_half_hybrid_band`.
        """
        plain = card(job_id="4000000001", title="AI Engineer",
                     prerank={"score": 30, "domain_matched": [],
                              "enabler_matched": [],
                              "gates": {"overall": "UNKNOWN", "failed": []}})
        half = self.gated("PASS", "4000000030")
        targets, _ = enr.select_targets([plain, half], QUERIES, 1, set(), quiet, 0)
        self.assertEqual([t[1] for t in targets], ["4000000030"])

    def test_it_does_not_promote_past_the_alert_tie_break_either(self):
        """Outside the cut, inside the band, alert-sourced is the stronger key.

        Both cards are in the band, so the gate signal is all that could separate
        them — and it must not, because an alert card is one Salman's own alert
        matched and on an unenriched corpus the UNKNOWN would otherwise decide
        every pairing.
        """
        jobs = [self.gated("UNKNOWN", "4000000030"),
                alert_card(job_id="4123456781", title="Process Manager",
                           prerank={"score": 30, "domain_matched": ["process"],
                                    "enabler_matched": [],
                                    "gates": {"overall": "PASS", "failed": []}})]
        targets, _ = enr.select_targets(jobs, QUERIES, 1, set(), quiet, 0)
        self.assertEqual([t[1] for t in targets], ["4123456781"])

    def test_the_order_is_stable_across_runs(self):
        jobs = [self.gated("UNKNOWN", "4000000030"),
                self.gated("UNKNOWN", "4000000031",
                           title="Performance Management Lead", domain=("performance",)),
                self.gated("PASS", "4000000032", title="Quality Performance Manager",
                           domain=("performance",))]
        first = [t[1] for t in enr.select_targets(jobs, QUERIES, 3, set(), quiet, 0)[0]]
        second = [t[1] for t in enr.select_targets(jobs, QUERIES, 3, set(), quiet, 0)[0]]
        self.assertEqual(first, second)
        self.assertEqual(first[-1], "4000000032",
                         "the card the gates could already read goes last")


class VerificationComesBeforeDiscovery(unittest.TestCase):
    """The first claim on the budget: jobs about to be ranked on an unread eligibility.

    A shortlisted job with a top-`deep_rank_budget` pre-rank score and an `UNKNOWN`
    hard-gate verdict is one Phase 2 will score, and `gate_jobs.py` may draft
    documents for, on a language and tenure risk nobody has read. One request settles
    it. Anything else this budget could buy is a *discovery*, and a discovery about a
    job that will not be ranked this run buys nothing this run.

    Concrete numbers from the 2026-08-22 shortlist, which is what motivated the
    reorder: 80 jobs, 65 UNKNOWN, only 3 carrying a body — and 11 of the top 25 by
    pre-rank score were UNKNOWN with nothing to read, against a budget of 15. So the
    whole cut was verifiable with requests left over, and before this change the
    allocator spent them on discovery instead and handed the ranker 11 UNKNOWNs.

    The tier is bounded to the cut for the reason the band exists at all: unverified
    alone was 65 of 80, so an unbounded verification tier would consume every request
    and starve discovery exactly as the alert tier did on 2026-08-19.
    """

    def top(self, job_id="4000000040", score=145, overall="UNKNOWN",
            title="Supply Chain AI & Automation Analyst", **extra):
        """A high-scoring card whose gates nobody could read."""
        return card(job_id=job_id, title=title,
                    prerank={"score": score, "domain_matched": ["supply_chain"],
                             "enabler_matched": ["ai", "automation"],
                             "gates": {"overall": overall, "failed": []}}, **extra)

    def freehire(self, score=200):
        """An in-cut UNKNOWN this loop can never fetch, whatever the budget."""
        return {"title": "AI Analyst", "portal": "freehire",
                "url": "https://freehire.example/j/1",
                "dedup_key": "url:https://freehire.example/j/1",
                "prerank": {"score": score, "gates": {"overall": "UNKNOWN"}}}

    def alerted(self, job_id="4000000050", score=0, portal="linkedin-search",
                **extra):
        """A row the *pre-ranker* counted against its alert budget.

        Scored 0 on purpose, and the portal deliberately says `linkedin-search`:
        both are the 2026-08-22 corpus. Every alert row there scored 0-30 against
        non-alert rows up to 135, and 3 of the 42 were found by a search and merely
        matched by an alert, so `portal` would miss them.
        """
        return card(job_id=job_id, portal=portal,
                    prerank={"score": score, "reason": "LinkedIn alert-matched",
                             "track_guess": None,
                             "gates": {"overall": "UNKNOWN", "failed": []}}, **extra)

    def tracked(self, track, job_id="4000000060", score=50, **extra):
        """A non-alert row the pre-ranker attributed to a track."""
        return card(job_id=job_id,
                    prerank={"score": score, "reason": "top of the shortlist",
                             "track_guess": track,
                             "gates": {"overall": "UNKNOWN", "failed": []}}, **extra)

    def test_the_score_read_is_the_preranks_not_the_title_heuristic(self):
        """Two different numbers live in this file; the cut is decided by the other one.

        `title_match_score` allocates requests against the 13 LinkedIn track queries;
        `prerank.score` is what `prerank_jobs.py` cut the shortlist on. They disagree
        sharply — the PMO title below scores 0 on the queries — and the ranker reads
        the pre-rank one, so that is the one the verification tier must order by.
        """
        title = "Advanced PMO Specialist - Sourcing & Procurement Excellence"
        self.assertEqual(enr.title_match_score(title, QUERIES), 0)
        self.assertEqual(enr.shortlist_score(self.top(score=145)), 145)

    def test_a_missing_or_unparseable_score_is_zero_not_a_crash(self):
        self.assertEqual(enr.shortlist_score(card()), 0)
        self.assertEqual(enr.shortlist_score(card(prerank={})), 0)
        self.assertEqual(enr.shortlist_score(card(prerank={"score": None})), 0)
        self.assertEqual(enr.shortlist_score(card(prerank={"score": "high"})), 0)

    def test_negative_prerank_scores_survive_unclamped(self):
        """The two-axis model scores a core-tech title with no domain *below* zero.

        Clamping here would tie every negative score to every unscored card and let
        aggregation order decide which of them the cut contains.
        """
        self.assertEqual(enr.shortlist_score(card(prerank={"score": -40})), -40)

    def test_the_cut_is_the_highest_scores_and_ties_go_to_file_order(self):
        jobs = [self.top(job_id="4000000041", score=30),
                self.top(job_id="4000000042", score=145),
                self.top(job_id="4000000043", score=30)]
        cut = enr.rank_cut(jobs, 2)
        self.assertIn(id(jobs[1]), cut, "the top score is always in")
        self.assertIn(id(jobs[0]), cut, "the earlier of the tied pair takes the slot")
        self.assertNotIn(id(jobs[2]), cut)

    def test_a_zero_cut_disables_the_tier_entirely(self):
        """The escape hatch the discovery tests use, and a guard against an empty file."""
        self.assertEqual(enr.rank_cut([self.top()], 0), set())
        self.assertEqual(enr.rank_cut([], 25), set())

    def test_non_linkedin_jobs_still_occupy_cut_slots(self):
        """The cut being modelled is portal-blind: a freehire job displaces a card.

        Skipping them would inflate the tier, promoting a LinkedIn card that the real
        final cut never reaches.
        """
        freehire = self.freehire()
        jobs = [self.top(job_id="4000000044", score=145), freehire]
        self.assertEqual(enr.rank_cut(jobs, 1), {id(freehire)})

    def test_alert_rows_enter_the_cut_on_attribution_not_on_score(self):
        """The defect the 2026-08-22 replay caught, at its narrowest.

        A top-N-by-score model of the cut hit 14 of the real 25 rows and reached 3 of
        its 13 UNKNOWNs, because `prerank_jobs.py` splits the alert pool out and takes
        it *before* score is consulted. On that corpus every alert row scored 0-30 and
        non-alert rows reached 135, so score order puts all ten outside — skipping
        exactly the rows most likely to be UNKNOWN.
        """
        alert = self.alerted(score=0)
        rich = self.top(job_id="4000000045", score=145)
        cut = enr.rank_cut([rich, alert], 1, alert_budget=10, floor=0)
        self.assertEqual(cut, {id(alert)},
                         "a 0-scoring alert row outranks a 145-scoring search card")

    def test_the_alert_pool_is_capped_at_the_alert_budget(self):
        """Past `alert_budget` the pre-ranker stops taking them, and so must this."""
        alerts = [self.alerted(job_id=f"40000000{n:02d}") for n in range(50, 54)]
        rich = self.top(job_id="4000000046", score=145)
        cut = enr.rank_cut(alerts + [rich], 3, alert_budget=2, floor=0)
        self.assertEqual(cut, {id(alerts[0]), id(alerts[1]), id(rich)},
                         "two alerts by file order, then score fills the rest")

    def test_the_alert_pool_never_overruns_the_whole_cut(self):
        """`alert_budget` above `cut` must not return more jobs than the cut holds."""
        alerts = [self.alerted(job_id=f"40000000{n:02d}") for n in range(50, 55)]
        self.assertEqual(len(enr.rank_cut(alerts, 2, alert_budget=10, floor=0)), 2)

    def test_each_track_holds_floor_slots_before_score_fills_the_rest(self):
        """`select`'s own floor pass, which also runs before score.

        Without it a plain top-N hands every remaining slot to whichever track scored
        best, and the model diverges from the cut on the tracks it starved.
        """
        t4a = self.tracked("T4_supply_chain_ops", "4000000061", score=100)
        t4b = self.tracked("T4_supply_chain_ops", "4000000062", score=90)
        t4c = self.tracked("T4_supply_chain_ops", "4000000063", score=80)
        t2 = self.tracked("T2_data_analytics", "4000000064", score=10)
        cut = enr.rank_cut([t4a, t4b, t4c, t2], 3, alert_budget=0, floor=1)
        self.assertIn(id(t2), cut,
                      "T2 holds its floor slot against a T4 card scoring 8x more")
        self.assertNotIn(id(t4c), cut)

    def test_an_unattributed_track_is_ineligible_for_a_floor_slot(self):
        """`track_guess: None` is the pre-ranker saying *no track matched*.

        Defaulting it to a track would hand reserved slots to the untracked bucket,
        which is the 2026-08-18 failure where "Procurement Counsel" and "Account
        Executive, LATAM" took slots the floor was holding for real tracks.
        """
        untracked = self.tracked(None, "4000000065", score=5)
        t4a = self.tracked("T4_supply_chain_ops", "4000000066", score=100)
        t4b = self.tracked("T4_supply_chain_ops", "4000000067", score=90)
        cut = enr.rank_cut([untracked, t4a, t4b], 2, alert_budget=0, floor=1)
        self.assertEqual(cut, {id(t4a), id(t4b)},
                         "no floor slot for None; score decides, and it scores 5")

    def test_the_floor_pass_can_be_disabled_without_disabling_the_cut(self):
        """`floor=0` degrades to alert-pool-then-score, which the tie tests rely on."""
        t4a = self.tracked("T4_supply_chain_ops", "4000000068", score=100)
        t4b = self.tracked("T4_supply_chain_ops", "4000000069", score=90)
        t2 = self.tracked("T2_data_analytics", "4000000070", score=10)
        self.assertEqual(enr.rank_cut([t4a, t4b, t2], 2, alert_budget=0, floor=0),
                         {id(t4a), id(t4b)})

    def test_a_job_is_never_counted_twice_when_keys_collide(self):
        """Membership is `id()`, not `dedup_key`.

        A shortlist carries duplicate keys when two portals found one posting, and
        de-duplicating by key here would silently shrink the cut below `cut`.
        """
        a = self.top(job_id="4000000071", score=145)
        b = self.top(job_id="4000000071", score=140)
        self.assertEqual(b["dedup_key"], a["dedup_key"])
        self.assertEqual(len(enr.rank_cut([a, b], 2, alert_budget=0, floor=0)), 2)

    def test_prerank_alerted_reads_the_reason_the_shortlist_wrote(self):
        """Not `portal`, which disagrees, and not `alert_matched.json`, which drifts."""
        self.assertTrue(enr.prerank_alerted(self.alerted()))
        self.assertFalse(enr.prerank_alerted(
            self.tracked("T4_supply_chain_ops")))
        self.assertFalse(enr.prerank_alerted(card()))
        self.assertFalse(enr.prerank_alerted(card(prerank={})))
        self.assertTrue(enr.prerank_alerted(
            card(prerank={"reason": "LINKEDIN ALERT-MATCHED"})),
            "case-insensitive, so a capitalisation change upstream cannot silently "
            "empty the alert pool")
        self.assertFalse(enr.prerank_alerted(
            card(portal="linkedin-alert", prerank={"reason": "top of the shortlist"})),
            "portal alone is not the pre-ranker's notion of an alert row")

    def test_prerank_track_passes_none_through_untouched(self):
        self.assertEqual(enr.prerank_track(
            self.tracked("T4_supply_chain_ops")), "T4_supply_chain_ops")
        self.assertIsNone(enr.prerank_track(self.tracked(None)))
        self.assertIsNone(enr.prerank_track(card()))
        self.assertIsNone(enr.prerank_track(card(prerank={})))

    def test_the_tier_outranks_the_half_hybrid_band(self):
        """The inversion the reorder is for, at its narrowest.

        Below the cut the band wins — `UnverifiedGatesGoFirstWithinABand` pins that.
        Inside it, a job the ranker is about to read wins instead.
        """
        top = self.top(job_id="4000000040", score=145)
        half = card(job_id="4000000010", title="Continuous Improvement Manager",
                    prerank={"score": 30, "domain_matched": ["continuous_improvement"],
                             "enabler_matched": [],
                             "gates": {"overall": "PASS", "failed": []}})
        targets, stats = enr.select_targets([half, top], QUERIES, 1, set(), quiet, 25)
        self.assertEqual([t[1] for t in targets], ["4000000040"])
        self.assertEqual(stats["verify_targets"], 1)
        self.assertEqual(stats["half_hybrid_targets"], 0)

    def test_the_tier_outranks_an_alert_card_too(self):
        """Alert priority is a tie-break, and this tier sits above it.

        An alert card whose gates already read PASS has had its eligibility question
        answered; the request would buy nothing it does not already have.
        """
        top = self.top(job_id="4000000040", score=145)
        alerted = alert_card(job_id="4123456781", title="Process Manager",
                             prerank={"score": 30, "domain_matched": ["process"],
                                      "enabler_matched": [],
                                      "gates": {"overall": "PASS", "failed": []}})
        targets, stats = enr.select_targets([alerted, top], QUERIES, 1, set(), quiet, 25)
        self.assertEqual([t[1] for t in targets], ["4000000040"])
        self.assertEqual(stats["verify_targets"], 1)

    def test_inside_the_tier_the_budget_is_spent_in_score_order(self):
        """"Enrich in score order until the budget runs out" — not spread thin.

        File order is deliberately the reverse of score order, so an allocator that
        merely preserved aggregation order would pass the count assertion and fail
        this one.
        """
        jobs = [self.top(job_id="4000000040", score=60),
                self.top(job_id="4000000041", score=105),
                self.top(job_id="4000000042", score=145)]
        targets, stats = enr.select_targets(jobs, QUERIES, 2, set(), quiet, 25)
        self.assertEqual([t[1] for t in targets], ["4000000042", "4000000041"])
        self.assertEqual(stats["verify_targets"], 2)
        self.assertEqual(stats["verify_over_budget"], 1)

    def test_a_verified_job_in_the_cut_is_not_re_read(self):
        """PASS means the gates already had a body. The tier is about UNKNOWN only."""
        passing = self.top(job_id="4000000040", score=145, overall="PASS")
        unknown = self.top(job_id="4000000041", score=30)
        targets, stats = enr.select_targets([passing, unknown], QUERIES, 1, set(),
                                            quiet, 25)
        self.assertEqual([t[1] for t in targets], ["4000000041"])
        self.assertEqual(stats["verify_targets"], 1)

    def test_the_tier_is_bounded_by_the_cut_not_by_unverified_alone(self):
        """The bound that stops this becoming the whole allocator.

        Two UNKNOWN cards, a cut of 1: only the higher-scoring one is in the tier, and
        the other competes on the discovery rules like any card.
        """
        jobs = [self.top(job_id="4000000040", score=145),
                self.top(job_id="4000000041", score=10)]
        _, stats = enr.select_targets(jobs, QUERIES, 15, set(), quiet, 1)
        self.assertEqual(stats["rank_cut"], 1)
        self.assertEqual(stats["unverified_in_cut"], 1)
        self.assertEqual(stats["verify_targets"], 1)
        self.assertEqual(stats["gate_unknown_targets"], 2,
                         "both are still UNKNOWN; only one is in the tier")

    def test_a_high_score_earns_a_read_whatever_the_track_queries_think(self):
        """The tier exempts from the title filter, like the other two exemptions.

        RED Global's "Process Manager" scores 0 against all 13 track queries. If the
        filter applied, the highest-scoring unverified job in the cut could be dropped
        for using vocabulary the search queries do not have — the vocabulary gap the
        two-axis model exists to escape.
        """
        self.assertEqual(enr.title_match_score("Process Manager", QUERIES), 0)
        job = card(job_id="4000000045", title="Process Manager",
                   prerank={"score": 145, "gates": {"overall": "UNKNOWN",
                                                    "failed": []}})
        targets, stats = enr.select_targets([job], QUERIES, 15, set(), quiet, 25)
        self.assertEqual([t[1] for t in targets], ["4000000045"])
        self.assertEqual(stats["no_title_match"], 0)
        self.assertEqual(stats["verify_targets"], 1)

    def test_the_cheaper_skips_still_come_first(self):
        """Priority buys a place in the queue, never a wasted request."""
        already = self.top(job_id="4000000040", description="full body text")
        seen = self.top(job_id="4000000041")
        targets, stats = enr.select_targets(
            [already, seen], QUERIES, 15, {"url:linkedin:4000000041"}, quiet, 25)
        self.assertEqual(targets, [])
        self.assertEqual(stats["already_full"], 1)
        self.assertEqual(stats["already_seen"], 1)
        self.assertEqual(stats["verify_targets"], 0)

    def test_an_unreachable_in_cut_job_is_counted_apart_from_the_budget(self):
        """No budget raise verifies a freehire posting — this loop cannot fetch it.

        Reporting it as an ordinary budget shortfall would send the reader to
        `detail_enrich_budget` for a number that would not change the outcome.
        """
        jobs = [self.freehire(), self.top(job_id="4000000040", score=145)]
        _, stats = enr.select_targets(jobs, QUERIES, 15, set(), quiet, 25)
        self.assertEqual(stats["unverified_in_cut"], 2)
        self.assertEqual(stats["unreachable_in_cut"], 1)
        self.assertEqual(stats["verify_targets"], 1)

    def test_a_card_already_carrying_a_body_counts_as_unreachable_too(self):
        """Same reasoning, different cause: the gates read it and still said UNKNOWN.

        Another request re-fetches text the pre-ranker has already seen, so the
        verdict would not move.
        """
        jobs = [self.top(job_id="4000000040", score=145, description="full body text")]
        _, stats = enr.select_targets(jobs, QUERIES, 15, set(), quiet, 25)
        self.assertEqual(stats["unverified_in_cut"], 1)
        self.assertEqual(stats["unreachable_in_cut"], 1)

    def test_a_cut_verification_target_gets_its_own_warning(self):
        """The worst cut this stage can make must not read as a routine budget note."""
        warnings = []
        jobs = [self.top(job_id=f"400000004{i}", score=145 - i) for i in range(4)]
        targets, stats = enr.select_targets(jobs, QUERIES, 2, set(),
                                            warnings.append, 25)
        self.assertEqual(len(targets), 2)
        self.assertEqual(stats["verify_over_budget"], 2)
        self.assertTrue(any("unverified language/experience gates" in w
                            for w in warnings), warnings)

    def test_the_warning_names_a_budget_that_would_actually_verify_the_cut(self):
        """It must ask for the *reachable* count, not the raw unverified count."""
        warnings = []
        jobs = [self.freehire()] + [self.top(job_id=f"400000004{i}", score=145 - i)
                                    for i in range(3)]
        enr.select_targets(jobs, QUERIES, 1, set(), warnings.append, 25)
        self.assertTrue(any("at least 3" in w for w in warnings),
                        f"4 unverified, 1 unreachable, so 3 requests: {warnings}")

    def test_leftover_budget_still_reaches_discovery(self):
        """Verification first is not verification only.

        The 2026-08-22 arithmetic in miniature: the tier fits inside the budget and
        what remains goes to the half-hybrid band, which is the goal the user asked to
        keep as the second priority rather than drop.
        """
        top = self.top(job_id="4000000040", score=145)
        half = card(job_id="4000000010", title="Continuous Improvement Manager",
                    prerank={"score": 30, "domain_matched": ["continuous_improvement"],
                             "enabler_matched": [],
                             "gates": {"overall": "PASS", "failed": []}})
        plain = card(job_id="4000000001", title="AI Engineer",
                     prerank={"score": 20, "domain_matched": ["process"],
                              "enabler_matched": ["ai"],
                              "gates": {"overall": "PASS", "failed": []}})
        targets, stats = enr.select_targets([plain, half, top], QUERIES, 2, set(),
                                            quiet, 25)
        self.assertEqual([t[1] for t in targets], ["4000000040", "4000000010"])
        self.assertEqual(stats["verify_targets"], 1)
        self.assertEqual(stats["half_hybrid_targets"], 1)

    def test_the_discovery_ordering_below_the_tier_is_unchanged(self):
        """The reorder adds a tier above discovery; it must not reshuffle discovery.

        A high pre-rank score must not reach past the band once the verification tier
        is done with it, or an alert card showing both halves would outrank a
        half-hybrid card whose missing half only a request can find — the exact trade
        the band exists to refuse.
        """
        rich = alert_card(job_id="4123456781", title="Process Manager",
                          prerank={"score": 145, "domain_matched": ["process"],
                                   "enabler_matched": ["ai"],
                                   "gates": {"overall": "PASS", "failed": []}})
        half = card(job_id="4000000010", title="Continuous Improvement Manager",
                    prerank={"score": 30, "domain_matched": ["continuous_improvement"],
                             "enabler_matched": [],
                             "gates": {"overall": "PASS", "failed": []}})
        targets, stats = enr.select_targets([rich, half], QUERIES, 1, set(), quiet, 25)
        self.assertEqual([t[1] for t in targets], ["4000000010"])
        self.assertEqual(stats["verify_targets"], 0, "both gates already read PASS")

    def test_the_selection_is_deterministic(self):
        jobs = [self.top(job_id="4000000040", score=60),
                self.top(job_id="4000000041", score=145),
                self.top(job_id="4000000042", score=105)]
        first = [t[1] for t in enr.select_targets(jobs, QUERIES, 3, set(), quiet, 25)[0]]
        second = [t[1] for t in enr.select_targets(jobs, QUERIES, 3, set(), quiet, 25)[0]]
        self.assertEqual(first, second)

    def test_the_budget_is_still_a_hard_stop_with_the_tier_active(self):
        jobs = [self.top(job_id=f"40000000{40 + i}", score=145 - i) for i in range(9)]
        for budget in range(0, 6):
            with self.subTest(budget=budget):
                targets, _ = enr.select_targets(jobs, QUERIES, budget, set(),
                                                quiet, 25)
                self.assertLessEqual(len(targets), budget)


class PipelineWiring(unittest.TestCase):
    """The script only earns its keep if the pipeline actually calls it."""

    SCRIPT_TEXT = (REPO / "scripts" / "run_daily.sh").read_text()

    def test_run_daily_invokes_enrichment(self):
        self.assertIn("scripts/enrich_linkedin.py", self.SCRIPT_TEXT)

    def test_enrichment_runs_before_ranking(self):
        enrich_at = self.SCRIPT_TEXT.index("scripts/enrich_linkedin.py")
        rank_at = self.SCRIPT_TEXT.index("prompts/pipeline_phase1_rank.md")
        self.assertLess(enrich_at, rank_at,
                        "enriching after ranking would change nothing")

    def test_enrichment_runs_after_aggregation(self):
        aggregate_at = self.SCRIPT_TEXT.index("scripts/aggregate_jobs.py")
        enrich_at = self.SCRIPT_TEXT.index("scripts/enrich_linkedin.py")
        self.assertLess(aggregate_at, enrich_at,
                        "there is no jobs file to enrich before aggregation")

    def test_the_ranker_prompt_tells_the_model_to_use_the_full_description(self):
        prompt = (REPO / "prompts" / "pipeline_phase1_rank.md").read_text()
        self.assertIn("Phase 1c", prompt)
        self.assertIn("description", prompt)

    def test_a_failure_appends_a_warning_the_report_will_show(self):
        """A silent enrichment failure would misread as a thin LinkedIn day."""
        self.assertIn('>> "$WARN_FILE"', self.SCRIPT_TEXT)
        self.assertIn("Phase 1c FAILED", self.SCRIPT_TEXT)


if __name__ == "__main__":
    unittest.main()
