"""Guards for the seen_jobs.json key migration (Phase 3 of the LinkedIn design).

The history file keyed jobs by raw URL; `aggregate_jobs.py` keys them canonically
(`url:linkedin:<jobId>`, `url:<clean-url>`, `ct:<company>|<title>`). Until the two
agree, every historical entry misses on dedup and re-enters as a new job — which
means re-ranking and possibly re-drafting a CV for something already seen.

The migration's failure mode is worse than not running it, so these tests pin the
two properties that make it safe to run on real data:

  1. **No entry is lost or merged.** A collision aborts rather than picking a winner.
  2. **The URL survives.** Pre-migration entries carry no `url` field, so the key is
     the only copy. `/rank` links that URL and the health check attributes portals by
     its domain; canonicalizing the key without preserving it would break both.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "migrate_seen_jobs.py"

_spec = importlib.util.spec_from_file_location("migrate_seen_jobs", SCRIPT)
mig = importlib.util.module_from_spec(_spec)
sys.modules["migrate_seen_jobs"] = mig
_spec.loader.exec_module(mig)


def quiet(_msg):
    """Swallow warnings in tests that assert on the result, not the messaging."""


def ranked(score=72, verdict="Good Fit", date="2026-08-17", location="FLAG", **extra):
    """An entry in the real pre-migration shape: five fields, no url, no portal."""
    return {"status": "ranked", "rank_score": score, "rank_verdict": verdict,
            "rank_date": date, "location": location, **extra}


class UrlPreservation(unittest.TestCase):
    """The key is the only copy of the URL. Losing it breaks /rank and the health check."""

    def test_raw_url_key_is_preserved_into_a_url_field(self):
        url = "https://hu.linkedin.com/jobs/view/ai-engineer-at-synthetic-4000000000"
        out = mig.migrate_entries({url: ranked()}, quiet)
        (key, entry), = out.items()
        self.assertEqual(key, "url:linkedin:4000000000")
        self.assertEqual(entry["url"], url,
                         "the original URL must survive the key rewrite verbatim")

    def test_query_string_is_kept_in_the_url_but_dropped_from_the_key(self):
        url = "https://ie.example.com/pub__5493999__7127?geoID=296&utm_source=x"
        out = mig.migrate_entries({url: ranked()}, quiet)
        (key, entry), = out.items()
        self.assertNotIn("?", key, "the key must be query-string-free for dedup")
        self.assertEqual(entry["url"], url,
                         "the fetchable URL keeps its query string — some portals "
                         "need it to resolve the posting")

    def test_existing_url_field_wins_over_the_key(self):
        real = "https://example.com/jobs/real"
        out = mig.migrate_entries({"url:example": ranked(url=real)}, quiet)
        (key, entry), = out.items()
        self.assertEqual(entry["url"], real)
        self.assertEqual(key, "url:https://example.com/jobs/real")

    def test_rank_history_fields_are_carried_through_untouched(self):
        url = "https://example.com/jobs/1"
        entry = ranked(score=88, verdict="Strong Fit", date="2026-08-01", location="PASS")
        out = mig.migrate_entries({url: dict(entry)}, quiet)
        migrated = next(iter(out.values()))
        for field, value in entry.items():
            with self.subTest(field=field):
                self.assertEqual(migrated[field], value,
                                 f"{field} must not be altered by a key migration")

    def test_portal_is_not_backfilled(self):
        """SKILL.md:146 says do not backfill portal — domain matching derives it."""
        url = "https://hu.linkedin.com/jobs/view/x-4000000000"
        out = mig.migrate_entries({url: ranked()}, quiet)
        entry = next(iter(out.values()))
        self.assertNotIn("portal", entry,
                         "a guessed portal value would override a live derivation")

    def test_input_is_not_mutated(self):
        url = "https://example.com/jobs/1"
        original = {url: ranked()}
        mig.migrate_entries(original, quiet)
        self.assertEqual(list(original), [url], "the source map must be left alone")
        self.assertNotIn("url", original[url])


class NoLossNoMerge(unittest.TestCase):
    def test_entry_count_is_preserved(self):
        seen = {f"https://example.com/jobs/{i}": ranked() for i in range(40)}
        self.assertEqual(len(mig.migrate_entries(seen, quiet)), 40)

    def test_collision_aborts_rather_than_merging(self):
        """Two country subdomains of one LinkedIn job would merge two rank histories."""
        seen = {
            "https://hu.linkedin.com/jobs/view/a-4000000000": ranked(score=70),
            "https://de.linkedin.com/jobs/view/b-4000000000": ranked(score=90),
        }
        with self.assertRaises(ValueError) as ctx:
            mig.migrate_entries(seen, quiet)
        self.assertIn("collision", str(ctx.exception).lower())
        self.assertIn("4000000000", str(ctx.exception),
                      "the message must name the colliding key so it can be fixed")

    def test_non_dict_entry_is_reported_not_silently_dropped(self):
        warnings = []
        out = mig.migrate_entries({"https://example.com/jobs/1": "corrupt"},
                                  warnings.append)
        self.assertEqual(out, {})
        self.assertTrue(warnings, "a dropped entry must be reported")

    def test_urlless_non_canonical_key_falls_back_to_company_title(self):
        warnings = []
        out = mig.migrate_entries(
            {"nokia_ai_engineer": ranked(company="Nokia", title="AI Engineer")},
            warnings.append)
        self.assertIn("ct:nokia|ai engineer", out)
        self.assertTrue(warnings, "the fallback must be reported, not silent")


class Idempotence(unittest.TestCase):
    """Re-running the migration must never double-prefix a key."""

    def test_migrating_twice_is_a_no_op(self):
        seen = {
            "https://hu.linkedin.com/jobs/view/x-4000000000": ranked(),
            "https://example.com/jobs/1?utm_source=y": ranked(),
        }
        once = mig.migrate_entries(seen, quiet)
        twice = mig.migrate_entries(once, quiet)
        self.assertEqual(once, twice, "the second pass changed the data")

    def test_canonical_keys_are_recognized(self):
        self.assertTrue(mig.is_canonical("url:linkedin:4000000000"))
        self.assertTrue(mig.is_canonical("ct:nokia|ai engineer"))
        self.assertFalse(mig.is_canonical("https://example.com/jobs/1"))


class KeyingMatchesThePipeline(unittest.TestCase):
    """The migration must key exactly as aggregate_jobs.py does, or dedup still misses."""

    def test_it_reuses_the_aggregators_function(self):
        _spec2 = importlib.util.spec_from_file_location(
            "agg_for_compare", REPO / "scripts" / "aggregate_jobs.py")
        agg = importlib.util.module_from_spec(_spec2)
        _spec2.loader.exec_module(agg)

        for url in ("https://hu.linkedin.com/jobs/view/x-4000000000",
                    "https://www.arbeitnow.com/view/some-job-123",
                    "https://weworkremotely.com/remote-jobs/abc?utm_source=z"):
            with self.subTest(url=url):
                out = mig.migrate_entries({url: ranked()}, quiet)
                self.assertIn(agg.make_dedup_key({"url": url}), out)

    def test_aggregator_publishes_the_key_it_dedupes_on(self):
        """The ranker prompt copies `dedup_key` from results; it must be there."""
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "jobsearch_portal_linkedin_probe_x.json"
            f.write_text(json.dumps({"meta": {"count": 1}, "results": [
                {"title": "AI Engineer", "company": "Synthetic Co",
                 "url": "https://hu.linkedin.com/jobs/view/x-4000000000",
                 "location": "Budapest"}]}))
            proc = subprocess.run(
                [sys.executable, str(REPO / "scripts" / "aggregate_jobs.py"), str(f)],
                capture_output=True, text=True, check=True)
        job = json.loads(proc.stdout)["results"][0]
        self.assertEqual(job.get("dedup_key"), "url:linkedin:4000000000",
                         "without dedup_key the ranker cannot reuse the key and must "
                         "re-derive it, which is what drifted the schemas apart")


class CliBehaviour(unittest.TestCase):
    def _write(self, directory, seen):
        path = Path(directory) / "seen_jobs.json"
        path.write_text(json.dumps({"seen": seen}, indent=2))
        return path

    def _run(self, path, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--file", str(path), *extra],
            capture_output=True, text=True)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write(d, {"https://example.com/jobs/1": ranked()})
            before = path.read_text()
            proc = self._run(path, "--dry-run")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(path.read_text(), before, "--dry-run modified the file")
            self.assertIn("Dry run", proc.stderr)

    def test_real_run_rewrites_keys_and_leaves_a_backup(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write(d, {
                "https://hu.linkedin.com/jobs/view/x-4000000000": ranked(),
                "https://example.com/jobs/1?utm_source=y": ranked(),
            })
            proc = self._run(path)
            self.assertEqual(proc.returncode, 0, proc.stderr)

            seen = json.loads(path.read_text())["seen"]
            self.assertEqual(set(seen), {"url:linkedin:4000000000",
                                         "url:https://example.com/jobs/1"})
            backups = list(Path(d).glob("seen_jobs.json.pre-migration-*"))
            self.assertEqual(len(backups), 1, f"expected one backup, got {backups}")
            self.assertEqual(len(json.loads(backups[0].read_text())["seen"]), 2,
                             "the backup must hold the pre-migration entries")

    def test_collision_exits_nonzero_and_leaves_the_file_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write(d, {
                "https://hu.linkedin.com/jobs/view/a-4000000000": ranked(score=70),
                "https://de.linkedin.com/jobs/view/b-4000000000": ranked(score=90),
            })
            before = path.read_text()
            proc = self._run(path)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("ABORTED", proc.stderr)
            self.assertEqual(path.read_text(), before,
                             "an aborted migration must not have written")

    def test_second_real_run_reports_already_migrated(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write(d, {"https://example.com/jobs/1": ranked()})
            self.assertEqual(self._run(path).returncode, 0)
            proc = self._run(path)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Already migrated", proc.stderr)
            backups = list(Path(d).glob("seen_jobs.json.pre-migration-*"))
            self.assertEqual(len(backups), 1,
                             "a no-op run must not pile up backup files")

    def test_missing_file_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            proc = self._run(Path(d) / "absent.json")
            self.assertEqual(proc.returncode, 0, "a fresh install has no history yet")

    def test_malformed_file_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "seen_jobs.json"
            path.write_text("not json")
            self.assertEqual(self._run(path).returncode, 1)

    def test_wrong_shape_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "seen_jobs.json"
            path.write_text(json.dumps({"jobs": []}))
            proc = self._run(path)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("seen", proc.stderr)


class RealHistoryFile(unittest.TestCase):
    """Checks against the live file, skipped where it is absent (fresh clone)."""

    LIVE = REPO / "job_scraper" / "seen_jobs.json"

    def setUp(self):
        if not self.LIVE.is_file():
            self.skipTest("no local job history to check")
        self.seen = json.loads(self.LIVE.read_text())["seen"]

    def test_live_file_migrates_without_collision(self):
        out = mig.migrate_entries(self.seen, quiet)
        self.assertEqual(len(out), len(self.seen),
                         "a live-data collision would merge two jobs' rank history")

    def test_every_live_entry_ends_up_with_a_canonical_key(self):
        for key in mig.migrate_entries(self.seen, quiet):
            with self.subTest(key=key):
                self.assertTrue(mig.is_canonical(key), f"{key} is not canonical")

    def test_every_live_entry_keeps_a_usable_url(self):
        for key, entry in mig.migrate_entries(self.seen, quiet).items():
            if key.startswith("ct:"):
                continue  # url-less by definition
            with self.subTest(key=key):
                self.assertTrue(entry.get("url", "").startswith("http"),
                                f"{key} lost its URL; /rank could not link it")


if __name__ == "__main__":
    unittest.main()
