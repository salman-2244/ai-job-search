"""The daily report must show hard-gate status, and never as a bare PASS.

Why this file exists at all: on the 2026-08-23 run, 11 of the 25 deep-ranked rows
carried `gates.overall == "PASS"` earned from a ~500-character search-card snippet
nobody had read. `hard_gates.py` now caps those at UNKNOWN, and
`tests/test_hard_gates.py` guards the verdict itself. This file guards the other
half: that the report *renders* the distinction. A correct verdict displayed as
"pass" is the same bug wearing a different hat.

The report generator is an inline `python3` heredoc inside `scripts/run_daily.sh`,
so it cannot be imported. It is extracted and run as a subprocess — the same text
launchd executes, not a copy that can drift out of sync with it.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNNER = REPO / "scripts" / "run_daily.sh"
HEREDOC_OPEN = "<<'PYTHON_SCRIPT'"


def extract_report_generator() -> str:
    """Return the Phase 5 heredoc body: the report generator the runner executes.

    Keyed on the phase banner rather than a line number so inserting a phase above
    it does not silently start testing the wrong block.
    """
    lines = RUNNER.read_text().splitlines()
    phase5 = next(i for i, l in enumerate(lines) if l.startswith("# === Phase 5"))
    start = next(i for i in range(phase5, len(lines))
                 if lines[i].rstrip().endswith(HEREDOC_OPEN)) + 1
    end = next(i for i in range(start, len(lines))
               if lines[i].strip() == "PYTHON_SCRIPT")
    return "\n".join(lines[start:end]) + "\n"


def row(title, overall, *, score=100, chars=0, source=None, failed=(),
        selected=True, company="ACME", location="Budapest", track="T5_process_perf"):
    """One corpus row shaped the way `prerank_jobs.py` writes it."""
    gates = {"overall": overall, "failed": list(failed), "evidence_chars": chars}
    if source is not None:
        gates["evidence_source"] = source
    return {"title": title, "company": company, "location": location,
            "url": f"https://example.invalid/{title}", "dedup_key": title,
            "prerank": {"selected": selected, "score": score,
                        "track_guess": track, "gates": gates}}


class ReportHarness(unittest.TestCase):
    """Runs the real Phase 5 block over a synthetic corpus and returns the report."""

    maxDiff = None

    def render(self, rows, meta=None):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            script = d / "report.py"
            script.write_text(extract_report_generator())

            jobs = d / "jobs.json"
            jobs.write_text(json.dumps({
                "meta": {"unique": len(rows),
                         "portals": {"linkedin-search": len(rows)},
                         **(meta or {})},
                "results": rows,
            }))
            report = d / "report.md"
            warn = d / "warn.txt"
            warn.write_text("")

            def stub(name, payload):
                p = d / name
                p.write_text(json.dumps(payload))
                return str(p)

            argv = [
                "2026-08-23", str(jobs),
                stub("top5.json", {"jobs": []}),
                stub("applicable.json", {"jobs": []}),
                stub("qa.json", {"reviews": [], "errors": []}),
                str(report),
                stub("not_drafted.json", []),
                str(warn),
                stub("prerank.json", {"selected": len(rows), "budget": 25,
                                      "alert_budget": 10}),
                stub("alerts.json", {"results": []}),
            ]
            proc = subprocess.run([sys.executable, str(script)] + argv,
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0,
                             f"report generator crashed:\n{proc.stderr[-2000:]}")
            self.assertTrue(report.exists(), "no report was written")
            return report.read_text()

    def section(self, text):
        """Just the gate-verification section, so unrelated tables cannot match."""
        start = text.index("## Gate Verification")
        rest = text.find("\n## ", start + 4)
        return text[start:rest] if rest > start else text[start:]


class AnUnenrichedRowNeverRendersAsPass(ReportHarness):
    """The user's requirement, stated at the display layer.

    "UNVERIFIED jobs stay in the top 25 and the Telegram list, but clearly labeled
    as such — don't exclude them, don't silently default them to PASS."
    """

    def test_a_snippet_only_row_is_labelled_unverified(self):
        got = self.section(self.render([
            row("Consulting Manager Data & AI Strategy", "UNKNOWN",
                chars=487, source="description_snippet"),
        ]))
        self.assertIn("unverified", got)
        self.assertIn("snippet only, 487 chars", got,
                      "the label must say what evidence there was, not just that it "
                      "was thin — 487 characters of marketing blurb is the whole story")

    def test_a_row_with_no_text_at_all_says_so(self):
        got = self.section(self.render([
            row("Medior Data Engineer", "UNKNOWN", chars=0, source="none"),
        ]))
        self.assertIn("unverified (no posting text)", got)

    def test_a_truncated_body_row_says_the_body_was_cut(self):
        """The provenance the snippet fix missed, at the display layer.

        `hard_gates` now caps a fetched body carrying `description_truncated`, which
        put a fourth value in `evidence_source`. Before `gate_label` learned it, such
        a row fell through to "no posting text" — false for 6001 characters of body,
        and it would have sent me looking for a fetch failure that never happened.
        """
        got = self.section(self.render([
            row("Product Operations Manager", "UNKNOWN",
                chars=6001, source="description_truncated"),
        ]))
        self.assertIn("body cut at 6001 chars", got)
        self.assertNotIn("no posting text", got)
        self.assertNotIn("snippet only", got,
                         "a cut body is not a card snippet; conflating them puts a "
                         "false statement about the evidence in the report")

    def test_a_cut_body_is_labelled_apart_from_a_whole_one(self):
        """Both are UNKNOWN, and the report has to say which is which.

        A whole body that came back UNKNOWN was read end to end and something else
        (pure_technical) went undecided. A cut body was never read to the end. Same
        verdict, different fact, different next action.
        """
        got = self.section(self.render([
            row("Whole Body Role", "UNKNOWN", score=140, chars=4000,
                source="description"),
            row("Cut Body Role", "UNKNOWN", score=120, chars=6001,
                source="description_truncated"),
        ]))
        whole = next(l for l in got.splitlines() if "Whole Body Role" in l)
        cut = next(l for l in got.splitlines() if "Cut Body Role" in l)
        self.assertNotIn("body cut", whole)
        self.assertIn("body cut at 6001 chars", cut)

    def test_an_unverified_row_keeps_its_slot_in_the_table(self):
        """Unverified is a label, not an exclusion. Dropping these rows would be a
        second bug in the opposite direction: a job nobody read is not a job that
        failed, and the whole point of UNKNOWN is that it stays in play."""
        got = self.section(self.render([
            row("Verified Role", "PASS", score=140, chars=4000,
                source="description"),
            row("Unread Role", "UNKNOWN", score=120, chars=490,
                source="description_snippet"),
            row("Textless Role", "UNKNOWN", score=100, chars=0, source="none"),
        ]))
        for title in ("Verified Role", "Unread Role", "Textless Role"):
            self.assertIn(title, got, "no row may be dropped for being unverified")
        self.assertIn("Deep-Ranked Set (3)", got)

    def test_no_unenriched_row_can_render_as_pass(self):
        """The sweep. Any gate block that did not read a fetched body must not put
        the word "pass" on the row, whatever its shape."""
        shapes = [
            {"overall": "UNKNOWN", "chars": 0, "source": "none"},
            {"overall": "UNKNOWN", "chars": 1, "source": "description_snippet"},
            {"overall": "UNKNOWN", "chars": 503, "source": "description_snippet"},
            {"overall": "UNKNOWN", "chars": 6001,
             "source": "description_truncated"},
            {"overall": "UNKNOWN", "chars": 0, "source": None},    # pre-fix artifact
            {"overall": "UNKNOWN", "chars": 475, "source": None},  # pre-fix artifact
        ]
        for shape in shapes:
            with self.subTest(**shape):
                text = self.section(self.render([row("Some Role", **shape)]))
                line = next(l for l in text.splitlines() if "Some Role" in l)
                self.assertNotIn("pass", line.lower())
                self.assertIn("unverified", line)

    def test_a_pre_fix_artifact_with_a_snippet_is_still_called_out(self):
        """`evidence_source` postdates the fail-open fix, so artifacts written before
        it have only `evidence_chars`. Those rows must still read as snippet-only
        rather than silently degrading to the vaguest label available."""
        got = self.section(self.render([
            row("Older Artifact Role", "UNKNOWN", chars=475, source=None),
        ]))
        self.assertIn("snippet only, 475 chars", got)


class TheHeadlineCountsAreHonest(ReportHarness):
    def test_verified_count_excludes_the_unverified(self):
        got = self.section(self.render([
            row("A", "PASS", chars=3000, source="description"),
            row("B", "PASS", chars=5000, source="description"),
            row("C", "UNKNOWN", chars=480, source="description_snippet"),
            row("D", "UNKNOWN", chars=0, source="none"),
            row("E", "FAIL", chars=4000, source="description", failed=["experience"]),
        ]))
        self.assertIn("**2 of 5 verified.**", got)
        self.assertIn("The other 2 are *unverified*", got,
                      "the FAIL row is neither verified nor unverified and must not "
                      "be counted into either bucket")

    def test_a_failed_row_names_the_gate_that_failed(self):
        got = self.section(self.render([
            row("Process Manager", "FAIL", chars=4000, source="description",
                failed=["experience"]),
        ]))
        self.assertIn("fail (experience)", got)

    def test_rows_are_ordered_by_prerank_score(self):
        got = self.section(self.render([
            row("Low Role", "UNKNOWN", score=40, source="none"),
            row("High Role", "UNKNOWN", score=145, source="none"),
            row("Mid Role", "UNKNOWN", score=105, source="none"),
        ]))
        self.assertLess(got.index("High Role"), got.index("Mid Role"))
        self.assertLess(got.index("Mid Role"), got.index("Low Role"))

    def test_deferred_rows_are_not_in_this_section(self):
        """The section describes the deep-ranked set. A deferred job's gate status is
        not a verification claim about anything that was offered."""
        got = self.section(self.render([
            row("Ranked Role", "UNKNOWN", source="none"),
            row("Deferred Role", "UNKNOWN", source="none", selected=False),
        ]))
        self.assertIn("Ranked Role", got)
        self.assertNotIn("Deferred Role", got)

    def test_the_ceiling_is_stated_where_the_unverified_rows_are(self):
        """`enrich_linkedin.py` can only fetch LinkedIn, so raising the budget cannot
        verify a non-LinkedIn row. Without that sentence the honest reading of "12
        unverified" is "the budget is too low", which sends Salman to the wrong dial."""
        got = self.section(self.render([
            row("Unread Role", "UNKNOWN", chars=490, source="description_snippet"),
        ]))
        self.assertIn("detail_enrich_budget", got)
        self.assertIn("only fetch LinkedIn", got)

    def test_no_section_when_nothing_was_deep_ranked(self):
        text = self.render([row("Deferred only", "UNKNOWN", selected=False)])
        self.assertNotIn("## Gate Verification", text,
                         "an empty table with a 0-of-0 headline is noise")


class TheSectionSurvivesRealArtifacts(ReportHarness):
    """A gate block missing keys must not take down the only step that reports the day."""

    def test_a_row_with_no_gate_block_is_labelled_not_gated(self):
        r = row("Ungated Role", "UNKNOWN")
        del r["prerank"]["gates"]
        got = self.section(self.render([r]))
        self.assertIn("not gated", got)

    def test_a_fail_with_no_named_gate_still_renders(self):
        got = self.section(self.render([
            row("Mystery Fail", "FAIL", chars=4000, source="description"),
        ]))
        self.assertIn("unspecified", got)


class TheGateSurfaceIsWiredIntoTheRunner(unittest.TestCase):
    """Cheap guards that the section cannot be quietly removed or reordered."""

    TEXT = RUNNER.read_text()

    def test_phase_5_renders_the_gate_section(self):
        self.assertIn("## Gate Verification of the Deep-Ranked Set", self.TEXT)

    def test_the_section_reads_the_corpus_not_a_new_argument(self):
        """Corpus and rankset gate blocks were measured identical on 2026-08-22 (all
        15 rows), because `propagate_to_corpus` copies `prerank` wholesale. Reading
        `$JOBS_FILE` keeps Phase 5's argv contract unchanged."""
        at = self.TEXT.index("## Gate Verification of the Deep-Ranked Set")
        window = self.TEXT[max(0, at - 3000):at]
        self.assertIn('jobs_data.get("results", [])', window)

    def test_the_gate_label_is_defined_before_it_is_used(self):
        self.assertLess(self.TEXT.index("def gate_label"),
                        self.TEXT.index("{gate_label(job)}"))


if __name__ == "__main__":
    unittest.main()
