"""Guards for the search-plan builder (Phase 2 of the LinkedIn design).

The plan builder decides how many LinkedIn requests the daily pipeline makes, so the
volume cap is the safety-critical property here: the user chose ~40-60 requests/day,
and `max_requests_per_run` is documented as a HARD STOP rather than a target. These
tests pin the cap, the rotation's coverage guarantee, and the temp-filename uniqueness
that stops two queries from overwriting each other's results.
"""
import importlib.util
import sys
import unittest
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MATRIX_PATH = REPO / "config" / "search_matrix.json"

_spec = importlib.util.spec_from_file_location(
    "build_search_plan", REPO / "scripts" / "build_search_plan.py"
)
bsp = importlib.util.module_from_spec(_spec)
sys.modules["build_search_plan"] = bsp
_spec.loader.exec_module(bsp)


def quiet(_msg):
    """Swallow warnings in tests that assert on the plan, not on the messaging."""


def matrix(cap=4, geos=("Hungary", "Germany", "Netherlands"),
           always=("Hungary",), queries=("AI Engineer", "Data Scientist")):
    """A minimal synthetic matrix, so unit tests don't drift with the real config."""
    return {
        "linkedin": {
            "enabled": True,
            "max_requests_per_run": cap,
            "jobage_days": 14,
            "limit_per_query": 10,
            "always_include_geos": list(always),
            "geos": list(geos),
            "tracks": {"T1_ai_ml": {"enabled": True, "queries": list(queries)}},
        }
    }


class SlugTests(unittest.TestCase):
    def test_slug_is_filesystem_safe(self):
        self.assertEqual(bsp.slug("AI Engineer"), "ai_engineer")
        self.assertEqual(bsp.slug("Supply Chain Analytics"), "supply_chain_analytics")
        self.assertEqual(bsp.slug("European Union"), "european_union")

    def test_slug_strips_characters_that_would_break_a_path(self):
        for raw in ("AI/ML Engineer", "Data Scientist (Senior)", "R&D Analyst",
                    "../etc/passwd"):
            with self.subTest(raw=raw):
                s = bsp.slug(raw)
                self.assertRegex(s, r"^[a-z0-9_]+$",
                                 f"{raw!r} -> {s!r} is not path-safe")
                self.assertNotIn("..", s)
                self.assertNotIn("/", s)

    def test_slug_never_returns_empty(self):
        self.assertEqual(bsp.slug("!!!"), "q")
        self.assertEqual(bsp.slug(""), "q")


class VolumeCapTests(unittest.TestCase):
    """The cap is the user's chosen exposure limit. It must never be exceeded."""

    def test_plan_never_exceeds_the_cap(self):
        for cap in range(1, 15):
            with self.subTest(cap=cap):
                plan = bsp.build_plan(matrix(cap=cap), 0, warn=quiet)
                linkedin = [p for p in plan if p[1] == "linkedin"]
                self.assertLessEqual(
                    len(linkedin), cap,
                    f"cap {cap} exceeded with {len(linkedin)} LinkedIn requests",
                )

    def test_cap_holds_across_many_days(self):
        m = matrix(cap=5)
        for index in range(0, 400, 7):
            with self.subTest(day_index=index):
                plan = bsp.build_plan(m, index, warn=quiet)
                self.assertLessEqual(len([p for p in plan if p[1] == "linkedin"]), 5)

    def test_always_include_set_larger_than_cap_is_truncated_and_reported(self):
        warnings = []
        # 3 queries x 2 always-geos = 6 requests against a cap of 4
        m = matrix(cap=4, geos=("Hungary", "Germany"), always=("Hungary", "Germany"),
                   queries=("AI Engineer", "Data Scientist", "Demand Planning"))
        plan = bsp.build_plan(m, 0, warn=warnings.append)
        self.assertEqual(len(plan), 4, "truncation must respect the cap exactly")
        self.assertTrue(any("always_include_geos" in w for w in warnings),
                        f"the drop must be reported, got warnings: {warnings}")

    def test_no_silent_truncation(self):
        """Whenever coverage is bounded, the plan must say so on stderr."""
        warnings = []
        bsp.build_plan(matrix(cap=3), 0, warn=warnings.append)
        self.assertTrue(warnings, "a bounded run must report what it dropped")

    def test_disabled_portal_produces_no_requests(self):
        m = matrix()
        m["linkedin"]["enabled"] = False
        self.assertEqual(bsp.build_plan(m, 0, warn=quiet), [])

    def test_disabled_track_produces_no_requests(self):
        m = matrix()
        m["linkedin"]["tracks"]["T1_ai_ml"]["enabled"] = False
        self.assertEqual(bsp.build_plan(m, 0, warn=quiet), [])


class RotationTests(unittest.TestCase):
    def test_always_include_geo_appears_every_run(self):
        m = matrix(cap=4)
        for index in range(20):
            with self.subTest(day_index=index):
                names = [p[0] for p in bsp.build_plan(m, index, warn=quiet)]
                self.assertTrue(any("hungary" in n for n in names),
                                "the primary market must be queried on every run")

    def test_rotation_eventually_covers_every_pair(self):
        m = matrix(cap=4)
        seen = set()
        for index in range(10):
            seen.update(p[0] for p in bsp.build_plan(m, index, warn=quiet))
        expected = {
            f"linkedin_t1_ai_ml_{bsp.slug(q)}_{bsp.slug(g)}"
            for g in ("Hungary", "Germany", "Netherlands")
            for q in ("AI Engineer", "Data Scientist")
        }
        self.assertEqual(seen, expected,
                         f"rotation never reached: {sorted(expected - seen)}")

    def test_rotation_is_deterministic_for_a_given_day(self):
        m = matrix(cap=4)
        self.assertEqual(bsp.build_plan(m, 42, warn=quiet),
                         bsp.build_plan(m, 42, warn=quiet))

    def test_rotation_advances_between_days(self):
        m = matrix(cap=4)
        day_a = [p[0] for p in bsp.build_plan(m, 0, warn=quiet)]
        day_b = [p[0] for p in bsp.build_plan(m, 1, warn=quiet)]
        self.assertNotEqual(day_a, day_b,
                            "consecutive runs must query different rotating pairs")

    def test_day_index_is_monotonic_from_the_epoch(self):
        self.assertEqual(bsp.day_index(date(2026, 1, 1)), 0)
        self.assertLess(bsp.day_index(date(2026, 1, 1)),
                        bsp.day_index(date(2026, 8, 18)))

    def test_uncapped_matrix_emits_every_pair_without_warning(self):
        warnings = []
        plan = bsp.build_plan(matrix(cap=100), 0, warn=warnings.append)
        self.assertEqual(len(plan), 6, "3 geos x 2 queries")
        self.assertEqual(warnings, [],
                         f"nothing was dropped, so nothing to warn about: {warnings}")


class PlanShapeTests(unittest.TestCase):
    def test_names_are_unique_so_temp_files_never_collide(self):
        plan = bsp.build_plan(bsp.load_matrix(MATRIX_PATH), 0, warn=quiet)
        names = [p[0] for p in plan]
        self.assertEqual(
            len(names), len(set(names)),
            "duplicate names would make one query overwrite another's results",
        )

    def test_every_invocation_is_a_search_with_json_output(self):
        plan = bsp.build_plan(bsp.load_matrix(MATRIX_PATH), 0, warn=quiet)
        self.assertTrue(plan, "the committed matrix must plan at least one request")
        for name, _portal, args in plan:
            with self.subTest(name=name):
                self.assertEqual(args[0], "search",
                                 "only the search subcommand is planned")
                self.assertEqual(args[-2:], ["--format", "json"],
                                 "aggregate_jobs.py parses JSON only")
                self.assertIn("-q", args, "every query needs a search term")

    def test_no_tabs_anywhere_since_the_output_is_tsv(self):
        plan = bsp.build_plan(bsp.load_matrix(MATRIX_PATH), 0, warn=quiet)
        for name, portal, args in plan:
            with self.subTest(name=name):
                self.assertNotIn("\t", name)
                self.assertNotIn("\t", portal)
                for a in args:
                    self.assertNotIn("\t", a,
                                     f"a tab in arg {a!r} would corrupt the TSV")

    def test_portal_filter_selects_one_portal(self):
        m = bsp.load_matrix(MATRIX_PATH)
        plan = bsp.build_plan(m, 0, only_portal="linkedin", warn=quiet)
        self.assertTrue(plan)
        self.assertTrue(all(p[1] == "linkedin" for p in plan))


class RealMatrixTests(unittest.TestCase):
    """Integration checks against the committed config."""

    @classmethod
    def setUpClass(cls):
        cls.matrix = bsp.load_matrix(MATRIX_PATH)

    def test_matrix_file_exists(self):
        self.assertTrue(MATRIX_PATH.is_file(), "config/search_matrix.json missing")

    def test_linkedin_volume_matches_the_agreed_moderate_band(self):
        cap = self.matrix["linkedin"]["max_requests_per_run"]
        self.assertGreaterEqual(cap, 40,
                                "the user chose a moderate ~40-60 request/day band")
        self.assertLessEqual(cap, 60,
                             "60 is the agreed ceiling; raising it needs a decision")

    def test_requests_are_delayed(self):
        self.assertGreaterEqual(
            self.matrix["linkedin"]["delay_seconds"], 2,
            "a delay between sequential requests is part of the agreed mitigation",
        )

    def test_all_five_tracks_are_present_and_enabled(self):
        tracks = self.matrix["linkedin"]["tracks"]
        for track in ("T1_ai_ml", "T2_data_bi", "T3_ai_product",
                      "T4_supply_ops", "T5_process_perf"):
            with self.subTest(track=track):
                self.assertIn(track, tracks, f"{track} missing from the matrix")
                self.assertTrue(tracks[track]["enabled"], f"{track} is disabled")
                self.assertTrue(tracks[track]["queries"], f"{track} has no queries")

    def test_t4_and_t5_queries_target_the_previously_buried_roles(self):
        """The matrix must actually search for the roles the old ranker zeroed out."""
        tracks = self.matrix["linkedin"]["tracks"]
        t4 = " ".join(tracks["T4_supply_ops"]["queries"]).lower()
        t5 = " ".join(tracks["T5_process_perf"]["queries"]).lower()
        self.assertIn("supply chain", t4)
        self.assertIn("demand planning", t4)
        self.assertIn("performance", t5)
        self.assertIn("process", t5)

    def test_hungary_is_the_always_included_geo(self):
        self.assertIn("Hungary", self.matrix["linkedin"]["always_include_geos"],
                      "Hungary is the only geo needing no visa sponsorship")

    def test_always_include_geos_are_a_subset_of_geos(self):
        geos = set(self.matrix["linkedin"]["geos"])
        for g in self.matrix["linkedin"]["always_include_geos"]:
            self.assertIn(g, geos,
                          f"{g!r} is always-included but absent from the geo list")

    def test_geos_cover_the_profile_priority_markets(self):
        geos = set(self.matrix["linkedin"]["geos"])
        for g in ("Hungary", "Germany", "Austria", "Finland", "Sweden",
                  "Netherlands", "Ireland", "Switzerland", "United Kingdom"):
            with self.subTest(geo=g):
                self.assertIn(g, geos, f"{g} is a stated priority market")

    def test_todays_plan_respects_the_cap(self):
        plan = bsp.build_plan(self.matrix, bsp.day_index(date.today()), warn=quiet)
        linkedin = [p for p in plan if p[1] == "linkedin"]
        self.assertLessEqual(len(linkedin),
                             self.matrix["linkedin"]["max_requests_per_run"])

    def test_enrichment_budget_is_bounded(self):
        budget = self.matrix["linkedin"]["detail_enrich_budget"]
        self.assertGreater(budget, 0)
        self.assertLessEqual(
            budget, 25,
            "detail enrichment costs one request each; keep it bounded",
        )


if __name__ == "__main__":
    unittest.main()
