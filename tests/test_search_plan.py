"""Guards for the search-plan builder (Phase 2 of the LinkedIn design).

The plan builder decides how many LinkedIn requests the daily pipeline makes, so the
volume cap is the safety-critical property here: the user chose ~40-60 requests/day,
and `max_requests_per_run` is documented as a HARD STOP rather than a target. These
tests pin the cap, the rotation's coverage guarantee, and the temp-filename uniqueness
that stops two queries from overwriting each other's results.
"""
import importlib.util
import subprocess
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


class GeoFilterTests(unittest.TestCase):
    """`--geo` / GEO_FILTER: the on-demand `/run <geo>` path's narrowing.

    The property that matters is that a narrowed run actually searches the requested
    country *on any date*. Rotation means a given geo is absent from most days'
    windows, so a filter applied to the emitted plan would return nothing on all but a
    few days — a bug that would look like "the bot found no German jobs" rather than
    like a filter that never ran.
    """

    def linkedin(self, plan):
        return [p for p in plan if p[1] == "linkedin"]

    def geos_in(self, plan):
        """Which -l values the plan actually queries."""
        out = set()
        for _name, _portal, args in self.linkedin(plan):
            out.add(args[args.index("-l") + 1])
        return out

    def test_a_rotating_geo_is_searched_on_every_date_when_requested(self):
        m = bsp.load_matrix(MATRIX_PATH)
        # Netherlands is not in always_include_geos, so unfiltered runs reach it only
        # when the window comes round. Under --geo it must be there every time.
        for index in range(0, 40, 7):
            with self.subTest(index=index):
                plan = bsp.build_plan(m, index, only_geos=["Netherlands"], warn=quiet)
                self.assertEqual(self.geos_in(plan), {"Netherlands"},
                                 "a geo-scoped run must query exactly that geo")

    def test_the_filter_does_not_smuggle_in_the_always_include_geo(self):
        """Hungary is always-include, but "run Germany" means Germany.

        Left unhandled this is the likely bug: `always_include_geos` is unioned into
        every plan, so a Germany run would spend part of its cap on Budapest and the
        user would see Hungarian jobs from a request that named another country.
        """
        m = bsp.load_matrix(MATRIX_PATH)
        plan = bsp.build_plan(m, 0, only_geos=["Germany"], warn=quiet)
        self.assertEqual(self.geos_in(plan), {"Germany"})

    def test_several_geos_are_all_searched(self):
        m = bsp.load_matrix(MATRIX_PATH)
        plan = bsp.build_plan(m, 3, only_geos=["Germany", "Austria"], warn=quiet)
        self.assertEqual(self.geos_in(plan), {"Germany", "Austria"})

    def test_geo_names_match_case_insensitively(self):
        m = bsp.load_matrix(MATRIX_PATH)
        for spelling in ("germany", "GERMANY", "Germany"):
            with self.subTest(spelling=spelling):
                plan = bsp.build_plan(m, 0, only_geos=[spelling], warn=quiet)
                self.assertEqual(self.geos_in(plan), {"Germany"},
                                 "typed input should not have to match the config's case")

    def test_a_multi_word_geo_survives_matching(self):
        """"United Kingdom" and "Czech Republic" are the shapes most likely to break."""
        m = bsp.load_matrix(MATRIX_PATH)
        for geo in ("United Kingdom", "Czech Republic", "united_kingdom"):
            with self.subTest(geo=geo):
                plan = bsp.build_plan(m, 0, only_geos=[geo], warn=quiet)
                self.assertEqual(len(self.geos_in(plan)), 1,
                                 f"{geo!r} matched {self.geos_in(plan)}")

    def test_two_spellings_of_one_geo_do_not_plan_it_twice(self):
        m = bsp.load_matrix(MATRIX_PATH)
        once = bsp.build_plan(m, 0, only_geos=["Germany"], warn=quiet)
        twice = bsp.build_plan(m, 0, only_geos=["Germany", "germany"], warn=quiet)
        self.assertEqual(len(self.linkedin(once)), len(self.linkedin(twice)),
                         "duplicate spellings would double the requests")

    def test_an_unknown_geo_plans_no_linkedin_searches_and_warns(self):
        """Failing loudly beats falling back to a full sweep.

        A typo that silently ran all 18 countries would spend the whole day's cap on
        the opposite of what was asked, and the mistake would be invisible in the
        report.
        """
        m = bsp.load_matrix(MATRIX_PATH)
        warnings = []
        plan = bsp.build_plan(m, 0, only_geos=["Narnia"], warn=warnings.append)
        self.assertEqual(self.linkedin(plan), [])
        self.assertTrue(any("narnia" in w.lower() for w in warnings),
                        f"the unknown name must be named back: {warnings}")

    def test_a_partly_unknown_request_still_runs_the_known_half(self):
        m = bsp.load_matrix(MATRIX_PATH)
        warnings = []
        plan = bsp.build_plan(m, 0, only_geos=["Germany", "Narnia"],
                              warn=warnings.append)
        self.assertEqual(self.geos_in(plan), {"Germany"})
        self.assertTrue(any("narnia" in w.lower() for w in warnings))

    def test_the_request_cap_still_binds_under_a_geo_filter(self):
        """--geo narrows the corpus, it does not buy extra requests."""
        m = bsp.load_matrix(MATRIX_PATH)
        cap = m["linkedin"]["max_requests_per_run"]
        reserve = m["linkedin"]["detail_enrich_budget"]
        for only in (["Germany"], ["Germany", "Austria", "Ireland", "Finland",
                                   "Sweden", "Switzerland", "United Kingdom"]):
            with self.subTest(geos=len(only)):
                plan = bsp.build_plan(m, 0, only_geos=only, warn=quiet)
                self.assertLessEqual(len(self.linkedin(plan)), cap - reserve)

    def test_an_oversubscribed_geo_request_is_truncated_not_expanded(self):
        # 4 queries against one geo is 4 requests under a cap of 3, so the plan must
        # lose one rather than the geo filter buying itself extra headroom.
        m = matrix(cap=3, geos=("Hungary", "Germany"), always=("Hungary",),
                   queries=("AI Engineer", "Data Scientist", "ML Engineer",
                            "Data Analyst"))
        m["linkedin"]["detail_enrich_budget"] = 0
        warnings = []
        plan = bsp.build_plan(m, 0, only_geos=["Germany"], warn=warnings.append)
        self.assertEqual(len(self.linkedin(plan)), 3)
        self.assertTrue(any("dropping" in w for w in warnings), warnings)

    def test_a_geo_scoped_plan_is_deterministic(self):
        m = bsp.load_matrix(MATRIX_PATH)
        first = bsp.build_plan(m, 11, only_geos=["Sweden"], warn=quiet)
        second = bsp.build_plan(m, 11, only_geos=["Sweden"], warn=quiet)
        self.assertEqual(first, second)

    def test_the_other_portals_survive_a_geo_scoped_run(self):
        """Their geography is inside their own query args; nothing to filter.

        Dropping them would shrink a `/run Germany` corpus to LinkedIn alone and lose
        the remote-EU listings, which are geographically relevant to any request.
        """
        m = bsp.load_matrix(MATRIX_PATH)
        warnings = []
        plan = bsp.build_plan(m, 0, only_geos=["Germany"], warn=warnings.append)
        others = {p[1] for p in plan if p[1] != "linkedin"}
        self.assertTrue(others, "non-LinkedIn portals should still be planned")
        self.assertTrue(any("--geo narrows LinkedIn only" in w for w in warnings),
                        f"the narrowing's limit should be stated: {warnings}")

    def test_no_filter_is_the_unchanged_full_sweep(self):
        """The default path must be identical to before the flag existed."""
        m = bsp.load_matrix(MATRIX_PATH)
        self.assertEqual(bsp.build_plan(m, 5, warn=quiet),
                         bsp.build_plan(m, 5, only_geos=None, warn=quiet))
        self.assertEqual(bsp.build_plan(m, 5, warn=quiet),
                         bsp.build_plan(m, 5, only_geos=[], warn=quiet))

    def test_names_stay_unique_so_temp_files_cannot_collide(self):
        m = bsp.load_matrix(MATRIX_PATH)
        names = [p[0] for p in bsp.build_plan(m, 0, only_geos=["Germany", "Austria"],
                                              warn=quiet)]
        self.assertEqual(len(names), len(set(names)))


class ResolveGeosTests(unittest.TestCase):
    def test_matched_order_follows_the_matrix_not_the_request(self):
        available = ["Hungary", "Germany", "Netherlands"]
        matched, unknown = bsp.resolve_geos(available, ["Netherlands", "Hungary"])
        self.assertEqual(matched, ["Hungary", "Netherlands"])
        self.assertEqual(unknown, [])

    def test_blank_entries_are_ignored(self):
        """"Germany," and "Germany, " are what a phone keyboard actually sends."""
        matched, unknown = bsp.resolve_geos(["Germany"], ["Germany", "", "  "])
        self.assertEqual(matched, ["Germany"])
        self.assertEqual(unknown, [])

    def test_unknown_names_are_reported(self):
        matched, unknown = bsp.resolve_geos(["Germany"], ["Narnia", "Germany"])
        self.assertEqual(matched, ["Germany"])
        self.assertEqual(unknown, ["narnia"])

    def test_known_geos_reads_the_matrix(self):
        self.assertEqual(bsp.known_geos(matrix(geos=("Hungary", "Germany"))),
                         ["Hungary", "Germany"])
        self.assertEqual(bsp.known_geos({}), [])

    def test_the_real_matrix_exposes_its_geos(self):
        geos = bsp.known_geos(bsp.load_matrix(MATRIX_PATH))
        self.assertIn("Germany", geos)
        self.assertGreaterEqual(len(geos), 10)


class GeoCliTests(unittest.TestCase):
    """The CLI surface `run_daily.sh` and the Stage 3 orchestrator actually call."""

    SCRIPT = REPO / "scripts" / "build_search_plan.py"

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(self.SCRIPT), "--date", "2026-09-07", *args],
            capture_output=True, text=True, cwd=str(REPO))

    def linkedin_rows(self, stdout):
        rows = [r.split("\t") for r in stdout.splitlines() if r]
        return [r for r in rows if len(r) > 1 and r[1] == "linkedin"]

    def test_geo_flag_narrows_the_emitted_plan(self):
        proc = self.run_cli("--geo", "Germany")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rows = self.linkedin_rows(proc.stdout)
        self.assertTrue(rows, proc.stderr)
        for row in rows:
            self.assertEqual(row[row.index("-l") + 1], "Germany")

    def test_a_multi_word_geo_survives_the_shell_boundary(self):
        """The `run_daily.sh` array-vs-word-splitting bug, pinned end to end."""
        proc = self.run_cli("--geo", "United Kingdom")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rows = self.linkedin_rows(proc.stdout)
        self.assertTrue(rows, f"no LinkedIn rows: {proc.stderr}")
        for row in rows:
            self.assertEqual(row[row.index("-l") + 1], "United Kingdom")

    def test_comma_separated_geos_are_split(self):
        proc = self.run_cli("--geo", "Germany,Austria")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        seen = {r[r.index("-l") + 1] for r in self.linkedin_rows(proc.stdout)}
        self.assertEqual(seen, {"Germany", "Austria"})

    def test_list_geos_prints_the_offerable_choices(self):
        proc = self.run_cli("--list-geos")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        listed = [line for line in proc.stdout.splitlines() if line]
        self.assertEqual(listed, bsp.known_geos(bsp.load_matrix(MATRIX_PATH)))

    def test_an_unknown_geo_exits_zero_with_a_warning_not_a_full_sweep(self):
        """Exit 0 matters: run_daily.sh treats a non-zero plan build as FATAL.

        The run should proceed with the other portals and an empty LinkedIn half, and
        the operator should be able to see why from stderr.
        """
        proc = self.run_cli("--geo", "Narnia")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.linkedin_rows(proc.stdout), [])
        self.assertIn("Narnia", proc.stderr + proc.stdout)

    def test_the_scope_is_reported_on_stderr(self):
        proc = self.run_cli("--geo", "Germany")
        self.assertIn("geo-scoped", proc.stderr)

    def test_count_still_works_under_a_geo_filter(self):
        proc = self.run_cli("--geo", "Germany", "--count")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.strip().isdigit(), proc.stdout)


class RealMatrixTests(unittest.TestCase):
    """Integration checks against the committed config."""

    @classmethod
    def setUpClass(cls):
        cls.matrix = bsp.load_matrix(MATRIX_PATH)

    def test_matrix_file_exists(self):
        self.assertTrue(MATRIX_PATH.is_file(), "config/search_matrix.json missing")

    def test_linkedin_volume_matches_the_agreed_moderate_band(self):
        """The cap is an exposure limit, so every raise is a recorded decision.

        40-60 was the original band. Raised to a 40-90 band on 2026-09-06, by
        instruction, because the geo list went from 10 countries to 18 (Spain,
        France, Belgium, Malta, Portugal, Czech Republic, Estonia, Luxembourg
        added). 14 queries x 18 geos is 252 pairs against 21 rotating slots per
        run — 12 runs for full coverage at a cap of 60, where it used to be 6.
        Runs are now on-demand rather than daily, so "12 runs" is no longer
        "12 days" and a country could go a month unqueried. 90 restores roughly
        the old cadence.

        Raising this further needs another decision: requests stay sequential with
        `delay_seconds` between them, and the cap is what keeps one run's traffic
        inside a normal browsing footprint.
        """
        cap = self.matrix["linkedin"]["max_requests_per_run"]
        self.assertGreaterEqual(cap, 40,
                                "the user chose a moderate request/day band")
        self.assertLessEqual(cap, 90,
                             "90 is the agreed ceiling; raising it needs a decision")

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

    def test_the_western_european_expansion_is_present(self):
        """Added 2026-09-06 by instruction, naming each country.

        "Add Spain, France, Belgium, Malta, Netherlands, Portugal, Spain, UK,
        Ireland, Czech Republic, Estonia, Finland, Luxembourg to the location list."
        Netherlands, UK, Ireland and Finland were already there; the rest are new.
        Pinned individually so a config edit that drops one is a test failure and not
        a country that quietly stops being searched.
        """
        geos = set(self.matrix["linkedin"]["geos"])
        for g in ("Spain", "France", "Belgium", "Malta", "Portugal",
                  "Czech Republic", "Estonia", "Luxembourg"):
            with self.subTest(geo=g):
                self.assertIn(g, geos, f"{g} was added by instruction on 2026-09-06")

    def test_the_hungary_bias_is_gone_from_the_rotation(self):
        """Requirement 8: "drop Hungary bias".

        Hungary keeps its `always_include_geos` slot — it is the one market needing no
        sponsorship, which is a reason and not a bias. What must NOT happen is Hungary
        crowding the rotation: every other geo has to be reachable.
        """
        linkedin = self.matrix["linkedin"]
        self.assertEqual(linkedin["always_include_geos"], ["Hungary"],
                         "only Hungary earns a guaranteed slot")
        self.assertGreaterEqual(len(linkedin["geos"]), 18,
                                "the expanded list must survive")

    def test_every_geo_is_reached_within_a_bounded_number_of_runs(self):
        """A country that is never queried is a country Salman never sees jobs from.

        18 geos x 14 queries against a per-run slot count is the reason the cap moved
        to 90. This pins that full coverage is actually reachable, and how long it
        takes, so a future geo addition that pushes it out of range shows up here.
        """
        geos = set(self.matrix["linkedin"]["geos"])
        seen, runs = set(), 0
        for index in range(40):
            runs = index + 1
            for name, _portal, args in bsp.build_plan(self.matrix, index, warn=quiet):
                seen.update(g for g in geos if bsp.slug(g) in name)
            if seen >= geos:
                break
        self.assertEqual(seen, geos,
                         f"never queried within 40 runs: {sorted(geos - seen)}")
        self.assertLessEqual(runs, 20,
                             f"full geo coverage took {runs} runs; the cap exists to "
                             "keep this in a range where no country goes stale")

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
