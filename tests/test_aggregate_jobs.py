"""Guards for the aggregator's dedup keying (Phase 2 of the LinkedIn design).

The matrix run queries the same 13 LinkedIn searches across 10 country geos, so one
posting is routinely served from several country subdomains (`hu.`, `de.`, `nl.`) with
different trailing slug text and different tracking query strings. Before the canonical
keying, each of those variants was a distinct dedup key, so the same job entered the
unified output several times, was ranked several times, and could be drafted twice.

These tests pin the collapse. They also pin the per-portal count accumulation, which a
matrix run breaks differently: many `linkedin_*` files now map to one portal name.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "aggregate_jobs.py"

_spec = importlib.util.spec_from_file_location("aggregate_jobs", SCRIPT)
agg = importlib.util.module_from_spec(_spec)
sys.modules["aggregate_jobs"] = agg
_spec.loader.exec_module(agg)

JOB_ID = "4123456789"


def linkedin_url(subdomain="www", slug="ai-engineer-at-synthetic-co", suffix=""):
    return f"https://{subdomain}.linkedin.com/jobs/view/{slug}-{JOB_ID}{suffix}"


def portal_file(directory, name, results):
    path = Path(directory) / name
    path.write_text(json.dumps({"meta": {"count": len(results)}, "results": results}))
    return path


class LinkedInKeyCollapse(unittest.TestCase):
    """The headline fix: country subdomains of one job ID are one job."""

    def test_country_subdomains_collapse_to_one_key(self):
        keys = {
            agg.make_dedup_key({"url": linkedin_url(sub)})
            for sub in ("www", "hu", "de", "nl", "uk")
        }
        self.assertEqual(
            len(keys), 1,
            f"one posting from 5 country subdomains produced {len(keys)} keys: {keys}",
        )
        self.assertEqual(keys.pop(), f"url:linkedin:{JOB_ID}")

    def test_differing_slug_text_collapses(self):
        a = agg.make_dedup_key({"url": linkedin_url(slug="ai-engineer-at-synthetic-co")})
        b = agg.make_dedup_key({"url": linkedin_url(slug="ai-engineer")})
        c = agg.make_dedup_key({"url": f"https://www.linkedin.com/jobs/view/{JOB_ID}"})
        self.assertEqual(a, b)
        self.assertEqual(b, c, "a bare job-ID URL must key the same as a slugged one")

    def test_tracking_query_strings_and_trailing_slash_collapse(self):
        variants = [
            linkedin_url(suffix=""),
            linkedin_url(suffix="/"),
            linkedin_url(suffix="?position=1&pageNum=0&refId=abc"),
            linkedin_url(suffix="?trk=public_jobs_topcard"),
            linkedin_url(suffix="#top"),
        ]
        keys = {agg.make_dedup_key({"url": u}) for u in variants}
        self.assertEqual(len(keys), 1, f"variants produced {len(keys)} keys: {keys}")

    def test_distinct_job_ids_stay_distinct(self):
        a = agg.make_dedup_key({"url": linkedin_url()})
        b = agg.make_dedup_key(
            {"url": "https://www.linkedin.com/jobs/view/data-analyst-at-x-9876543210"}
        )
        self.assertNotEqual(a, b, "different postings must never collapse together")

    def test_non_job_linkedin_urls_are_not_treated_as_jobs(self):
        for url in ("https://www.linkedin.com/company/nokia/",
                    "https://www.linkedin.com/jobs/search?keywords=ai",
                    "https://www.linkedin.com/in/someone/"):
            with self.subTest(url=url):
                key = agg.make_dedup_key({"url": url})
                self.assertFalse(
                    key.startswith("url:linkedin:"),
                    f"{url} is not a posting but keyed as one: {key}",
                )

    def test_lookalike_domain_is_not_treated_as_linkedin(self):
        """A canonical key must not be minted for a domain that merely reads similarly."""
        for url in (f"https://notlinkedin.com/jobs/view/x-{JOB_ID}",
                    f"https://linkedin.com.evil.example/jobs/view/x-{JOB_ID}"):
            with self.subTest(url=url):
                key = agg.make_dedup_key({"url": url})
                self.assertFalse(key.startswith("url:linkedin:"),
                                 f"{url} was mistaken for LinkedIn: {key}")

    def test_short_numbers_are_not_job_ids(self):
        key = agg.make_dedup_key({"url": "https://www.linkedin.com/jobs/view/team-42"})
        self.assertFalse(key.startswith("url:linkedin:"),
                         "a 2-digit number is not a LinkedIn job ID")


class NonLinkedInKeys(unittest.TestCase):
    def test_query_strings_are_stripped(self):
        a = agg.make_dedup_key({"url": "https://example.com/jobs/1?utm_source=x"})
        b = agg.make_dedup_key({"url": "https://example.com/jobs/1"})
        self.assertEqual(a, b)

    def test_urlless_jobs_fall_back_to_company_and_title(self):
        key = agg.make_dedup_key({"url": "", "company": "Nokia", "title": "AI Engineer"})
        self.assertEqual(key, "ct:nokia|ai engineer")

    def test_company_title_fallback_is_case_insensitive(self):
        a = agg.make_dedup_key({"url": "", "company": "Nokia", "title": "AI Engineer"})
        b = agg.make_dedup_key({"url": "", "company": " nokia ", "title": "ai engineer"})
        self.assertEqual(a, b)


class EndToEndAggregation(unittest.TestCase):
    """Run the script the way run_daily.sh does: many files, one stdout JSON."""

    def _run(self, files):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), *[str(f) for f in files]],
            capture_output=True, text=True, check=True,
        )
        return json.loads(proc.stdout), proc.stderr

    def test_same_job_from_three_geo_files_appears_once(self):
        with tempfile.TemporaryDirectory() as d:
            files = [
                portal_file(d, "jobsearch_portal_linkedin_t1_ai_engineer_hungary_x.json",
                            [{"title": "AI Engineer", "company": "Synthetic Co",
                              "url": linkedin_url("hu"), "location": "Budapest",
                              "date": "2026-08-14"}]),
                portal_file(d, "jobsearch_portal_linkedin_t1_ai_engineer_germany_x.json",
                            [{"title": "AI Engineer", "company": "Synthetic Co",
                              "url": linkedin_url("de", suffix="?position=3"),
                              "location": "Berlin", "date": "2026-08-14"}]),
                portal_file(d, "jobsearch_portal_linkedin_t2_data_scientist_hungary_x.json",
                            [{"title": "AI Engineer", "company": "Synthetic Co",
                              "url": linkedin_url("www", slug="ai-eng"),
                              "location": "Budapest", "date": "2026-08-14"}]),
            ]
            out, _ = self._run(files)

        self.assertEqual(out["meta"]["total_input"], 3)
        self.assertEqual(out["meta"]["unique"], 1,
                         f"expected 1 unique job, got {out['meta']['unique']}")
        self.assertEqual(out["meta"]["dupes_skipped"], 2)
        self.assertEqual(len(out["results"]), 1)

    def test_portal_counts_accumulate_across_many_files_of_one_portal(self):
        """A matrix run writes many linkedin_* files; the count must sum, not overwrite."""
        with tempfile.TemporaryDirectory() as d:
            files = [
                portal_file(d, f"jobsearch_portal_linkedin_q{i}_x.json",
                            [{"title": f"Role {i}", "company": "Synthetic Co",
                              "url": f"https://www.linkedin.com/jobs/view/r-90000000{i}",
                              "location": "Budapest", "date": "2026-08-14"}])
                for i in range(4)
            ]
            out, _ = self._run(files)

        self.assertEqual(
            out["meta"]["portals"].get("linkedin-search"), 4,
            "4 distinct jobs across 4 linkedin files must report 4, not the last file's 1",
        )
        self.assertEqual(out["meta"]["unique"], 4)

    def test_portals_are_counted_separately(self):
        with tempfile.TemporaryDirectory() as d:
            files = [
                portal_file(d, "jobsearch_portal_linkedin_a_x.json",
                            [{"title": "AI Engineer", "company": "A",
                              "url": linkedin_url(), "location": "Budapest"}]),
                portal_file(d, "jobsearch_portal_freehire_ai_x.json",
                            [{"title": "AI Engineer", "company": "B",
                              "url": "https://freehire.example/jobs/7",
                              "location": "Remote EU"}]),
            ]
            out, _ = self._run(files)

        self.assertEqual(out["meta"]["portals"]["linkedin-search"], 1)
        self.assertEqual(out["meta"]["portals"]["freehire-search"], 1)

    def test_missing_and_unparseable_files_warn_without_failing(self):
        """The pipeline must survive one portal CLI writing garbage."""
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "jobsearch_portal_linkedin_broken_x.json"
            bad.write_text("not json at all")
            good = portal_file(d, "jobsearch_portal_freehire_ai_x.json",
                               [{"title": "Data Analyst", "company": "C",
                                 "url": "https://freehire.example/jobs/9"}])
            out, stderr = self._run([bad, Path(d) / "absent.json", good])

        self.assertEqual(out["meta"]["unique"], 1,
                         "the one good file's job must still come through")
        self.assertIn("failed to parse", stderr)
        self.assertIn("not found", stderr)

    def test_jobs_with_neither_url_nor_company_title_are_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            f = portal_file(d, "jobsearch_portal_linkedin_a_x.json",
                            [{"title": "", "company": "", "url": ""},
                             {"title": "AI Engineer", "company": "D",
                              "url": linkedin_url()}])
            out, _ = self._run([f])
        self.assertEqual(out["meta"]["unique"], 1)


class NormalizeShape(unittest.TestCase):
    def test_description_is_truncated_to_a_snippet(self):
        job = agg.normalize_job({"title": "AI Engineer", "url": linkedin_url(),
                                 "description": "x" * 900}, "linkedin-search")
        self.assertTrue(job["description_snippet"].endswith("..."))
        self.assertLessEqual(len(job["description_snippet"]), 504)

    def test_missing_optional_fields_become_none_not_empty_string(self):
        job = agg.normalize_job({"title": "AI Engineer", "url": linkedin_url()},
                                "linkedin-search")
        for field in ("company", "location", "date_posted", "description_snippet"):
            with self.subTest(field=field):
                self.assertIsNone(job[field])

    def test_portal_is_recorded_on_every_job(self):
        job = agg.normalize_job({"title": "AI Engineer", "url": linkedin_url()},
                                "linkedin-search")
        self.assertEqual(job["portal"], "linkedin-search")

    def test_detect_portal_reads_the_matrix_temp_filenames(self):
        cases = {
            "/tmp/jobsearch_portal_linkedin_t4_supply_ops_hungary_2026-08-18.json":
                "linkedin-search",
            # Phase 0b's file. Its branch must be checked before the plain "linkedin"
            # one, which claims any path *containing* the word.
            "/tmp/jobsearch_portal_linkedin-alert_2026-08-18.json": "linkedin-alert",
            "/tmp/jobsearch_portal_linkedin_alert_2026-08-18.json": "linkedin-alert",
            "/tmp/jobsearch_portal_freehire_perf_2026-08-18.json": "freehire-search",
            "/tmp/jobsearch_portal_arbeitnow_data_2026-08-18.json": "arbeitnow-search",
            "/tmp/jobsearch_portal_wwr_data_2026-08-18.json": "weworkremotely-search",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(agg.detect_portal(Path(path), {}), expected)


class AlertUrlsJoinTheCorpus(unittest.TestCase):
    """Phase 0b's `/comm/` URLs must key to the same job a search page keys to.

    LinkedIn's job-alert emails link `linkedin.com/comm/jobs/view/<id>?midToken=…`.
    Without `/comm/` in the pattern those key as `url:https://www.linkedin.com/comm/...`
    — a different key from the `url:linkedin:<id>` a search page produces for the very
    same posting. That fork is invisible: the alert store would fill with keys no corpus
    job carries, so gate_jobs.py's alert-matched 60 tier would silently never fire.

    scripts/linkedin_alerts.py canonicalizes what it writes, so this is the safety net
    rather than the primary defence — but it is the one that catches a hand-written or
    third-party file arriving with the raw email URL still on it.
    """

    def test_the_comm_path_keys_the_same_as_a_search_url(self):
        alert = ("https://www.linkedin.com/comm/jobs/view/"
                 f"{JOB_ID}?midToken=AQEFAKE&eid=x&trackingId=y")
        self.assertEqual(agg.make_dedup_key({"url": alert}),
                         agg.make_dedup_key({"url": linkedin_url("hu")}))
        self.assertEqual(agg.make_dedup_key({"url": alert}), f"url:linkedin:{JOB_ID}")

    def test_a_comm_url_with_a_slug_still_collapses(self):
        self.assertEqual(
            agg.make_dedup_key({"url": "https://www.linkedin.com/comm/jobs/view/"
                                       f"process-manager-at-red-global-{JOB_ID}"}),
            f"url:linkedin:{JOB_ID}")

    def test_a_non_job_comm_url_is_not_mistaken_for_a_job(self):
        for url in ["https://www.linkedin.com/comm/jobs/alerts/",
                    "https://www.linkedin.com/comm/jobs/search/?keywords=x"]:
            with self.subTest(url=url):
                self.assertNotIn("url:linkedin:", agg.make_dedup_key({"url": url}))

    def test_the_alert_file_wins_the_key_over_a_search_file(self):
        """Dedup keeps the first key seen and the alert file sorts first, so a posting
        found both ways keeps its alert attribution — the more informative of the two.

        This is also why enrich_linkedin.py fetches alert cards first: the winning entry
        is the one with no description, so the search entry's snippet is discarded here.
        """
        with tempfile.TemporaryDirectory() as d:
            files = sorted([
                portal_file(d, "jobsearch_portal_linkedin-alert_2026-08-18.json",
                            [{"title": "Process Manager", "company": "RED Global",
                              "url": f"https://www.linkedin.com/jobs/view/{JOB_ID}",
                              "location": "Budapest, Hungary", "date": "2026-08-18",
                              "description": None}]),
                portal_file(d, "jobsearch_portal_linkedin_t5_process_hungary_2026-08-18.json",
                            [{"title": "Process Manager", "company": "RED Global",
                              "url": linkedin_url("hu", slug="process-manager"),
                              "location": "Budapest", "date": "2026-08-18",
                              "description": "The full posting text. " * 40}]),
            ])
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), *[str(f) for f in files]],
                capture_output=True, text=True, check=True)
            out = json.loads(proc.stdout)

        self.assertEqual(out["meta"]["unique"], 1)
        self.assertEqual(out["meta"]["dupes_skipped"], 1)
        self.assertEqual(out["results"][0]["portal"], "linkedin-alert")
        self.assertEqual(out["results"][0]["dedup_key"], f"url:linkedin:{JOB_ID}")
        self.assertIsNone(out["results"][0]["description_snippet"],
                          "the alert entry wins, so this job reaches the ranker with "
                          "no text at all unless Phase 1c enriches it — which is why "
                          "alert cards are enriched first")

    def test_alert_and_search_portals_are_counted_separately(self):
        """The report distinguishes a job Salman's own alert surfaced from one a query
        found; a shared portal name would erase that."""
        with tempfile.TemporaryDirectory() as d:
            files = [
                portal_file(d, "jobsearch_portal_linkedin-alert_2026-08-18.json",
                            [{"title": "Process Manager", "company": "RED Global",
                              "url": f"https://www.linkedin.com/jobs/view/{JOB_ID}",
                              "location": "Budapest"}]),
                portal_file(d, "jobsearch_portal_linkedin_t1_ai_hungary_2026-08-18.json",
                            [{"title": "AI Engineer", "company": "Synthetic Co",
                              "url": "https://hu.linkedin.com/jobs/view/ai-4000000001",
                              "location": "Budapest"}]),
            ]
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), *[str(f) for f in files]],
                capture_output=True, text=True, check=True)
            out = json.loads(proc.stdout)

        self.assertEqual(out["meta"]["portals"]["linkedin-alert"], 1)
        self.assertEqual(out["meta"]["portals"]["linkedin-search"], 1)


if __name__ == "__main__":
    unittest.main()
