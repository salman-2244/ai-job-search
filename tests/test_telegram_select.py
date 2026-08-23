"""Tests for the Telegram interactive job selector's pure logic.

Everything here runs without a bot token and without network access:
`scripts/telegram_select.py` imports `telegram` lazily inside
`run_interactive()` precisely so this module can exercise the rest.

Two classes of check earn their place beyond ordinary unit coverage:

  1. **Escaping.** Every field rendered into a Telegram message comes from a
     scraped posting, so a title containing `<b>` must arrive as text, not
     markup, and a `javascript:` URL must never become a tappable link.
  2. **Honest status.** The list shows a language and an experience verdict per
     job. A gate that had no description to read returns UNKNOWN, and that has
     to surface as "unverified" — never silently as a pass.
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "telegram_select.py"

_spec = importlib.util.spec_from_file_location("telegram_select", SCRIPT)
ts = importlib.util.module_from_spec(_spec)
sys.modules["telegram_select"] = ts
_spec.loader.exec_module(ts)

TRACKER_HEADER = (
    "date,company,sector,role,role_type,channel,status,contact_person,"
    "fit_rating,notes,cv_file,cover_letter_file,source"
).split(",")


def rec(**over):
    """A rankset record shaped like the pipeline's real output."""
    out = {
        "company": "ExampleCo",
        "title": "AI Analyst",
        "location": "Budapest",
        "url": "https://example.com/jobs/1",
        "portal": "linkedin-alert",
        "dedup_key": "url:linkedin:1",
        "enriched": True,
        "prerank": {
            "score": 100,
            "hybrid_tier": "strong",
            "gates": {
                "language": {"verdict": "PASS"},
                "experience": {"verdict": "PASS"},
            },
        },
    }
    out.update(over)
    return out


class RanksetBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, records, wrap=True):
        payload = {"meta": {}, "results": records} if wrap else records
        path = self.tmp / "rankset.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def rows(self, records, wrap=True):
        return ts.load_rankset(self.write(records, wrap))

    def one(self, **over):
        return self.rows([rec(**over)])[0]


class TestLoadRankset(RanksetBase):
    def test_sorts_by_score_descending(self):
        rows = self.rows([
            rec(company="Low", prerank={"score": 10}),
            rec(company="High", prerank={"score": 200}),
            rec(company="Mid", prerank={"score": 90}),
        ])
        self.assertEqual([r.company for r in rows], ["High", "Mid", "Low"])

    def test_idx_is_assigned_after_sorting(self):
        """Display numbers must run 1..n down the list, not follow file order."""
        rows = self.rows([
            rec(company="Low", prerank={"score": 10}),
            rec(company="High", prerank={"score": 200}),
        ])
        self.assertEqual([r.idx for r in rows], [0, 1])
        self.assertEqual(rows[0].company, "High")

    def test_accepts_a_bare_array(self):
        self.assertEqual(len(self.rows([rec()], wrap=False)), 1)

    def test_skips_non_dict_records(self):
        self.assertEqual(len(self.rows([rec(), "garbage", None, 42])), 1)

    def test_missing_fields_fall_back_without_raising(self):
        row = self.rows([{}])[0]
        self.assertEqual(row.company, "unknown")
        self.assertEqual(row.title, "untitled")
        self.assertEqual(row.location, "n/a")
        self.assertIsNone(row.score)

    def test_unscored_rows_sort_last(self):
        """A None score must not raise when compared against an int."""
        rows = self.rows([
            rec(company="NoScore", prerank={}),
            rec(company="Scored", prerank={"score": 50}),
        ])
        self.assertEqual([r.company for r in rows], ["Scored", "NoScore"])

    def test_rejects_a_non_list_results_value(self):
        path = self.tmp / "bad.json"
        path.write_text(json.dumps({"results": {"not": "a list"}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "expected a list"):
            ts.load_rankset(path)


class TestGateVerdicts(RanksetBase):
    def test_verdict_vocabulary(self):
        cases = {
            "PASS": "pass",
            "pass": "pass",
            "FAIL": "fail",
            "UNKNOWN": "unverified",
            None: "unverified",
            "": "unverified",
            "something-new": "unverified",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(ts._verdict(raw, enriched=True), expected)

    def test_unknown_gate_surfaces_as_unverified(self):
        row = self.one(
            enriched=False,
            prerank={
                "score": 5,
                "gates": {
                    "language": {"verdict": "UNKNOWN"},
                    "experience": {"verdict": "UNKNOWN"},
                },
            },
        )
        self.assertEqual(row.language, "unverified")
        self.assertEqual(row.experience, "unverified")

    def test_a_missing_gates_block_is_unverified_not_a_pass(self):
        row = self.one(prerank={"score": 5})
        self.assertEqual(row.language, "unverified")
        self.assertEqual(row.experience, "unverified")


class TestRenderJob(RanksetBase):
    def test_escapes_html_in_scraped_fields(self):
        out = ts.render_job(
            self.one(company="<script>alert(1)</script>", title="R&D <b>Lead</b>")
        )
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertIn("R&amp;D", out)
        self.assertTrue(out.startswith("<b>1. "))  # our own markup survives

    def test_includes_every_field_the_list_promises(self):
        out = ts.render_job(self.one())
        for expected in ("ExampleCo", "AI Analyst", "Budapest", "100", "strong",
                         "linkedin-alert", "language pass", "experience pass"):
            with self.subTest(field=expected):
                self.assertIn(expected, out)
        self.assertIn('href="https://example.com/jobs/1"', out)

    def test_numbers_from_one(self):
        rows = self.rows([rec(company="A", prerank={"score": 9}),
                          rec(company="B", prerank={"score": 8})])
        self.assertTrue(ts.render_job(rows[0]).startswith("<b>1. A</b>"))
        self.assertTrue(ts.render_job(rows[1]).startswith("<b>2. B</b>"))

    def test_unsafe_urls_are_rejected(self):
        for url in ("javascript:alert(1)", "data:text/html,<b>x",
                    "file:///etc/passwd", "", "not a url"):
            with self.subTest(url=url):
                self.assertIsNone(ts.safe_url(url))

    def test_http_urls_pass(self):
        for url in ("http://x.com/a", "https://x.com/a?b=c"):
            with self.subTest(url=url):
                self.assertEqual(ts.safe_url(url), url)

    def test_row_with_an_unsafe_url_says_so_instead_of_linking(self):
        out = ts.render_job(self.one(url="javascript:alert(1)"))
        self.assertIn("no usable link", out)
        self.assertNotIn("javascript:", out)


class TestControls(unittest.TestCase):
    def test_toggle_label_reflects_state(self):
        self.assertNotEqual(ts.toggle_label(True), ts.toggle_label(False))
        self.assertIn("Selected", ts.toggle_label(True))
        self.assertIn("Select", ts.toggle_label(False))

    def test_control_shows_the_running_count(self):
        self.assertIn("Selected: <b>2</b>", ts.render_control(5, 2))

    def test_locked_control_states_what_was_submitted(self):
        locked = ts.render_control(5, 2, locked=True)
        self.assertIn("locked", locked.lower())
        self.assertIn("2 of 5", locked)

    def test_locked_control_promises_documents_only_when_generating(self):
        """Under --no-generate, promising documents contradicts the next message."""
        self.assertIn("Generating", ts.render_control(4, 2, locked=True))
        dry = ts.render_control(4, 2, locked=True, generating=False)
        self.assertNotIn("Generating", dry)
        self.assertIn("no documents", dry.lower())


class TestAppendTracker(RanksetBase):
    """The tracker is a real data file; a sandbox run must not be able to touch it."""

    def test_override_path_receives_the_rows(self):
        dest = self.tmp / "sandbox_tracker.csv"
        ts.append_tracker([(self.one(), {"cv_file": "cv/x/main.tex"})], "2026-08-23", dest)
        text = dest.read_text(encoding="utf-8")
        self.assertIn("ExampleCo", text)
        self.assertEqual(text.splitlines()[0].split(","), TRACKER_HEADER)

    def test_override_leaves_the_real_tracker_untouched(self):
        real = REPO / "job_search_tracker.csv"
        before = real.read_bytes() if real.exists() else None
        dest = self.tmp / "sandbox_tracker.csv"
        ts.append_tracker([(self.one(company="FakeCo"), {})], "2026-08-23", dest)
        after = real.read_bytes() if real.exists() else None
        self.assertEqual(before, after)
        self.assertNotIn("FakeCo", (after or b"").decode("utf-8", "replace"))

    def test_appends_rather_than_overwrites(self):
        dest = self.tmp / "t.csv"
        ts.append_tracker([(self.one(company="First"), {})], "2026-08-23", dest)
        ts.append_tracker([(self.one(company="Second"), {})], "2026-08-23", dest)
        lines = dest.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 3)  # header + two rows
        self.assertIn("First", lines[1])
        self.assertIn("Second", lines[2])

    def test_empty_entries_creates_nothing(self):
        dest = self.tmp / "never.csv"
        ts.append_tracker([], "2026-08-23", dest)
        self.assertFalse(dest.exists())


class TestSelectionState(RanksetBase):
    def test_toggle_flips_and_returns_the_new_state(self):
        state = ts.SelectionState(self.tmp / "s.json")
        self.assertIs(state.toggle(3), True)
        self.assertEqual(state.ordered(), [3])
        self.assertIs(state.toggle(3), False)
        self.assertEqual(state.ordered(), [])

    def test_ordered_ignores_click_order(self):
        state = ts.SelectionState(self.tmp / "s.json")
        for i in (7, 2, 5):
            state.toggle(i)
        self.assertEqual(state.ordered(), [2, 5, 7])

    def test_state_round_trips_through_disk(self):
        path = self.tmp / "s.json"
        state = ts.SelectionState(path)
        state.toggle(1)
        state.toggle(4)
        state.job_message_ids[1] = 555
        state.control_message_id = 999
        state.locked = True
        state.save()

        loaded = ts.SelectionState(path)
        loaded.load()
        self.assertEqual(loaded.ordered(), [1, 4])
        self.assertEqual(loaded.job_message_ids, {1: 555})
        self.assertEqual(loaded.control_message_id, 999)
        self.assertIs(loaded.locked, True)

    def test_save_is_atomic_leaving_no_temp_file(self):
        path = self.tmp / "s.json"
        state = ts.SelectionState(path)
        state.toggle(1)
        self.assertEqual([p.name for p in self.tmp.glob("s.json*")], ["s.json"])

    def test_select_all_then_clear(self):
        state = ts.SelectionState(self.tmp / "s.json")
        state.select_all(range(4))
        self.assertEqual(state.ordered(), [0, 1, 2, 3])
        state.clear()
        self.assertEqual(state.ordered(), [])

    def test_works_in_memory_without_a_path(self):
        state = ts.SelectionState(None)
        state.toggle(1)  # save() must be a no-op, not a crash
        self.assertEqual(state.ordered(), [1])

    def test_load_on_a_missing_file_is_a_no_op(self):
        state = ts.SelectionState(self.tmp / "absent.json")
        state.load()
        self.assertEqual(state.ordered(), [])


class TestSlug(RanksetBase):
    def test_slug_is_filesystem_safe(self):
        slug = self.one(
            company="Bosch / Siemens & Co.", title="AI Engineer (m/w/d) — Remote!"
        ).slug
        self.assertNotIn("/", slug)
        self.assertNotIn(" ", slug)
        self.assertTrue(all(c.isalnum() or c == "_" for c in slug), slug)

    def test_slug_survives_empty_input(self):
        self.assertEqual(ts._slug(""), "unknown")
        self.assertEqual(ts._slug("!!!"), "unknown")

    def test_slug_keeps_unicode_words(self):
        self.assertEqual(ts._slug("Müller AG"), "Müller_AG")


class TestJobPayload(RanksetBase):
    def test_prefers_the_full_description(self):
        payload = ts.build_job_payload(
            self.one(description="FULL TEXT", description_snippet="short")
        )
        self.assertEqual(payload["posting_text"], "FULL TEXT")

    def test_falls_back_to_the_snippet(self):
        payload = ts.build_job_payload(self.one(description_snippet="short"))
        self.assertEqual(payload["posting_text"], "short")

    def test_falls_back_to_empty_when_neither_exists(self):
        self.assertEqual(ts.build_job_payload(self.one())["posting_text"], "")

    def test_strengths_and_gaps_stay_empty(self):
        """Prerank produces neither; inventing them would be fabrication."""
        payload = ts.build_job_payload(self.one())
        self.assertEqual(payload["strengths"], [])
        self.assertEqual(payload["gaps"], [])
        self.assertEqual(payload["language_status"], "pass")

    def test_payload_is_json_serialisable(self):
        """It is written to a file the drafter reads, so it must serialise."""
        json.dumps(ts.build_job_payload(self.one()))


class TestExtractJsonObject(unittest.TestCase):
    def test_extracts_an_object_surrounded_by_prose(self):
        text = 'Here you go:\n{"jobs": [{"company": "X"}], "errors": []}\nDone!'
        self.assertEqual(ts.extract_json_object(text)["jobs"][0]["company"], "X")

    def test_handles_nested_braces_and_braces_inside_strings(self):
        got = ts.extract_json_object('{"a": {"b": {"c": 1}}, "s": "has } and { inside"}')
        self.assertEqual(got["a"]["b"]["c"], 1)
        self.assertEqual(got["s"], "has } and { inside")

    def test_skips_a_false_start(self):
        """A non-JSON brace before the real object must not abort the scan."""
        self.assertEqual(
            ts.extract_json_object('note {not json at all\nthen {"ok": true}'),
            {"ok": True},
        )

    def test_handles_escaped_quotes(self):
        got = ts.extract_json_object(r'{"s": "a \" brace } here"}')
        self.assertEqual(got["s"], 'a " brace } here')

    def test_returns_none_when_absent(self):
        self.assertIsNone(ts.extract_json_object("no json here"))
        self.assertIsNone(ts.extract_json_object(""))


class TestTrackerRow(RanksetBase):
    def test_width_matches_the_csv_header(self):
        row = ts.tracker_row(self.one(), {}, "2026-08-23")
        self.assertEqual(len(row), len(TRACKER_HEADER))

    def test_header_matches_the_live_tracker_file(self):
        """Guards against drift if the real CSV's columns ever change."""
        tracker = REPO / "job_search_tracker.csv"
        if not tracker.exists():
            self.skipTest("no tracker on this machine")
        actual = tracker.read_text(encoding="utf-8").splitlines()[0].strip().split(",")
        self.assertEqual(actual, TRACKER_HEADER)

    def test_values_land_in_the_right_columns(self):
        row = ts.tracker_row(
            self.one(),
            {"cv_file": "cv/x/main.tex", "cover_letter_file": "cover_letters/x/cover.tex"},
            "2026-08-23",
        )
        got = dict(zip(TRACKER_HEADER, row))
        self.assertEqual(got["date"], "2026-08-23")
        self.assertEqual(got["company"], "ExampleCo")
        self.assertEqual(got["status"], "drafted")
        self.assertEqual(got["channel"], "telegram-select")
        self.assertEqual(got["cv_file"], "cv/x/main.tex")
        self.assertEqual(got["source"], "https://example.com/jobs/1")

    def test_status_is_drafted_never_applied(self):
        """The pipeline never auto-applies; the tracker must not imply it did."""
        got = dict(zip(TRACKER_HEADER, ts.tracker_row(self.one(), {}, "2026-08-23")))
        self.assertEqual(got["status"], "drafted")

    def test_tolerates_a_drafter_that_reported_no_paths(self):
        got = dict(zip(TRACKER_HEADER, ts.tracker_row(self.one(), {}, "2026-08-23")))
        self.assertEqual(got["cv_file"], "")
        self.assertEqual(got["cover_letter_file"], "")


class TestSummary(RanksetBase):
    def test_reports_mixed_outcomes_honestly(self):
        out = ts.render_summary([
            {
                "row": self.one(company="GoodCo"),
                "ok": True,
                "result": {
                    "cv_file": "cv/g/main.tex",
                    "cover_letter_file": "cover_letters/g/cover.tex",
                },
            },
            {"row": self.one(company="BadCo"), "ok": False, "error": "lualatex failed"},
        ])
        self.assertIn("Generated 1/2", out)
        self.assertIn("GoodCo", out)
        self.assertIn("cv/g/main.tex", out)
        self.assertIn("BadCo", out)
        self.assertIn("lualatex failed", out)

    def test_escapes_company_names(self):
        out = ts.render_summary(
            [{"row": self.one(company="<b>Evil</b>"), "ok": False, "error": "x"}]
        )
        self.assertIn("&lt;b&gt;Evil", out)

    def test_total_failure_claims_no_success(self):
        out = ts.render_summary(
            [{"row": self.one(), "ok": False, "error": "boom"}]
        )
        self.assertIn("Generated 0/1", out)


class TestRealArtifact(unittest.TestCase):
    """Parse a real rankset if the pipeline left one behind.

    The fixtures above encode my understanding of the record shape; this one
    checks that understanding against what the pipeline actually writes.
    """

    def test_real_rankset_parses(self):
        candidates = sorted(Path("/tmp").glob("jobsearch_rankset_*.json"))
        if not candidates:
            self.skipTest("no rankset artifact on this machine")
        rows = ts.load_rankset(candidates[-1])
        self.assertTrue(rows, f"{candidates[-1]} produced no rows")
        for row in rows:
            self.assertTrue(row.company)
            self.assertTrue(row.title)
            self.assertIn(row.language, ("pass", "fail", "unverified"))
            self.assertIn(row.experience, ("pass", "fail", "unverified"))
            ts.render_job(row)  # must not raise


if __name__ == "__main__":
    unittest.main()
