"""Guards for the document-generation gate.

This gate is the one place in the pipeline where a decision costs real work: clearing
it means a tailored CV and cover letter get written and QA'd. The ranker prompt states
the rule, but a prompt cannot enforce it — so `gate_jobs.py` re-applies it in code and
these tests pin the parts that would be dangerous to get wrong:

  * **The 60 gate needs a live alert.** An expired, undated or unparseable alert entry
    fails closed. Failing open would widen the gate permanently on the strength of a
    record whose age is unknown.
  * **The store outranks the ranker.** `alert_matched: true` in the model's output does
    not qualify a 62-point job; presence in `alert_matched.json` does.
  * **Alert-match moves the gate, never the score.** Nothing may add points.
  * **Nothing is dropped silently.** Gate rejections and over-cap jobs land on the
    not-drafted list so the report still shows them.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "gate_jobs.py"

_spec = importlib.util.spec_from_file_location("gate_jobs", SCRIPT)
gate = importlib.util.module_from_spec(_spec)
sys.modules["gate_jobs"] = gate
_spec.loader.exec_module(gate)

TODAY = date(2026, 8, 18)


def quiet(_msg):
    """Swallow warnings in tests that assert on the result, not the messaging."""


def job(key="url:linkedin:4000000000", score=80, title="AI Engineer",
        company="Alpha", **extra):
    """A ranker output entry (prompts/pipeline_phase1_rank.md Step 7)."""
    return {"key": key, "title": title, "company": company, "score": score,
            "url": f"https://hu.linkedin.com/jobs/view/{key.rsplit(':', 1)[-1]}",
            "location": "Budapest", "portal": "linkedin-search", "track": "T1_ai_ml",
            "verdict": "Strong Fit" if isinstance(score, int) and score >= 75
                       else "Good Fit",
            "alert_matched": False, "gate_reason": "score>=75",
            "strengths": ["python"], "gaps": [], "location_gate": "PASS",
            "language_gate": "PASS", "posting_text": "text", **extra}


def alert(days_ago=1, **extra):
    """An alert_matched.json entry, dated relative to TODAY."""
    return {"first_alerted": (TODAY - timedelta(days=days_ago)).isoformat(),
            "alert_name": "T1 AI ML", "track": "T1_ai_ml",
            "source": "linkedin-alert", **extra}


class ArrayExtraction(unittest.TestCase):
    def test_bare_json_array(self):
        self.assertEqual(gate.extract_array('[{"a": 1}]'), [{"a": 1}])

    def test_a_fenced_array_is_recovered(self):
        raw = 'Here you go:\n```json\n[{"a": 1}]\n```\nDone.'
        self.assertEqual(gate.extract_array(raw), [{"a": 1}])

    def test_an_empty_array_is_a_valid_answer_not_a_failure(self):
        self.assertEqual(gate.extract_array("[]"), [],
                         "'nothing qualified' must be distinguishable from a parse error")

    def test_unparseable_output_is_none_not_empty(self):
        for raw in ("", "   ", "I could not complete this task.",
                    '{"jobs": []}', "[unclosed"):
            with self.subTest(raw=raw):
                self.assertIsNone(gate.extract_array(raw))


class AlertExpiry(unittest.TestCase):
    def test_a_recent_alert_is_live(self):
        live, stats = gate.live_alert_keys({"k": alert(days_ago=1)}, TODAY, 30, quiet)
        self.assertEqual(live, {"k"})
        self.assertEqual(stats["alert_live"], 1)

    def test_an_alert_on_the_expiry_boundary_still_counts(self):
        live, _ = gate.live_alert_keys({"k": alert(days_ago=30)}, TODAY, 30, quiet)
        self.assertEqual(live, {"k"}, "exactly 30 days old is not yet expired")

    def test_an_alert_past_the_window_stops_widening_the_gate(self):
        live, stats = gate.live_alert_keys({"k": alert(days_ago=31)}, TODAY, 30, quiet)
        self.assertEqual(live, set())
        self.assertEqual(stats["alert_expired"], 1)

    def test_an_undated_entry_fails_closed_and_is_reported(self):
        warnings = []
        store = {"a": {}, "b": {"first_alerted": "not-a-date"},
                 "c": {"first_alerted": None}, "d": "junk"}
        live, stats = gate.live_alert_keys(store, TODAY, 30, warnings.append)
        self.assertEqual(live, set(), "an undatable alert must not widen the gate")
        self.assertEqual(stats["alert_undated"], 4)
        self.assertEqual(len(warnings), 4)

    def test_a_corrupt_store_disables_the_60_gate_loudly(self):
        warnings = []
        live, stats = gate.live_alert_keys(["not", "an", "object"], TODAY, 30,
                                          warnings.append)
        self.assertEqual(live, set())
        self.assertEqual(stats["alert_entries"], 0)
        self.assertTrue(warnings)

    def test_an_empty_store_is_silent(self):
        warnings = []
        live, stats = gate.live_alert_keys({}, TODAY, 30, warnings.append)
        self.assertEqual((live, warnings), (set(), []))
        self.assertEqual(stats["alert_entries"], 0)

    def test_prune_keeps_only_live_entries(self):
        store = {"live": alert(days_ago=2), "old": alert(days_ago=99)}
        live, _ = gate.live_alert_keys(store, TODAY, 30, quiet)
        self.assertEqual(list(gate.prune_store(store, live)), ["live"])


class GateRule(unittest.TestCase):
    def gate_it(self, jobs, live=frozenset(), cap=5, warn=quiet):
        return gate.apply_gate(jobs, live, 75, 60, cap, warn)

    def test_a_strong_fit_clears_without_any_alert(self):
        kept, rejected, _ = self.gate_it([job(score=75)])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["gate_reason"], "score>=75")
        self.assertEqual(rejected, [])

    def test_a_good_fit_with_a_live_alert_clears_at_60(self):
        kept, _, _ = self.gate_it([job(key="k", score=60)], live={"k"})
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["gate_reason"], "alert_matched+score>=60")

    def test_a_good_fit_without_an_alert_is_reported_not_drafted(self):
        kept, rejected, stats = self.gate_it([job(key="k", score=74)])
        self.assertEqual(kept, [])
        self.assertEqual(len(rejected), 1)
        self.assertEqual(stats["gate_rejected"], 1)
        self.assertIn("74", rejected[0]["gate_note"])

    def test_below_60_never_clears_even_when_alert_matched(self):
        kept, rejected, _ = self.gate_it([job(key="k", score=59)], live={"k"})
        self.assertEqual(kept, [], "an alert widens the gate to 60, not below it")
        self.assertIn("below 60", rejected[0]["gate_note"])

    def test_an_expired_alert_does_not_clear_a_62(self):
        """The whole point of the expiry: live_alert_keys already dropped the key."""
        kept, _, _ = self.gate_it([job(key="k", score=62)], live=set())
        self.assertEqual(kept, [])

    def test_the_store_overrides_a_false_ranker_claim(self):
        warnings = []
        kept, _, stats = self.gate_it(
            [job(key="k", score=62, alert_matched=True,
                 gate_reason="alert_matched+score>=60")], warn=warnings.append)
        self.assertEqual(kept, [],
                         "a hallucinated alert_matched must not buy a document")
        self.assertEqual(stats["alert_claim_corrected"], 1)
        self.assertTrue(any("alert_matched.json says" in w for w in warnings))

    def test_the_store_also_corrects_a_missing_ranker_claim(self):
        kept, _, stats = self.gate_it([job(key="k", score=62, alert_matched=False)],
                                      live={"k"})
        self.assertEqual(len(kept), 1)
        self.assertTrue(kept[0]["alert_matched"])
        self.assertEqual(stats["alert_claim_corrected"], 1)

    def test_alert_match_never_changes_the_score(self):
        scored = job(key="k", score=62)
        kept, _, _ = self.gate_it([scored], live={"k"})
        self.assertEqual(kept[0]["score"], 62,
                         "alert-match moves the gate, it does not add points")

    def test_a_sponsorship_flag_still_qualifies(self):
        """CLAUDE.md: flag, not auto-reject. A FLAG is information, not a veto."""
        kept, _, _ = self.gate_it([job(score=80, location_gate="FLAG")])
        self.assertEqual(len(kept), 1)

    def test_an_unscored_job_is_rejected_and_reported(self):
        warnings = []
        for bad in (None, "high", "", {}):
            with self.subTest(score=bad):
                kept, rejected, stats = self.gate_it([job(score=bad)],
                                                     warn=warnings.append)
                self.assertEqual(kept, [])
                self.assertEqual(stats["unscored"], 1)
                self.assertEqual(rejected[0]["gate_note"], "no usable score")
        self.assertTrue(warnings)

    def test_a_string_score_that_is_numeric_is_accepted(self):
        kept, _, _ = self.gate_it([job(score="80")])
        self.assertEqual(len(kept), 1)

    def test_a_non_object_entry_does_not_crash_the_gate(self):
        kept, _, stats = self.gate_it(["garbage", job(score=80)])
        self.assertEqual(len(kept), 1)
        self.assertEqual(stats["unscored"], 1)

    def test_an_empty_ranker_list_is_a_clean_zero(self):
        kept, rejected, stats = self.gate_it([])
        self.assertEqual((kept, rejected), ([], []))
        self.assertEqual(stats["cleared"], 0)


class CapAndOrdering(unittest.TestCase):
    def test_the_cap_is_a_hard_stop(self):
        jobs = [job(key=f"k{i}", score=80) for i in range(9)]
        kept, _, stats = gate.apply_gate(jobs, set(), 75, 60, 5, quiet)
        self.assertEqual(len(kept), 5)
        self.assertEqual(stats["over_cap"], 4)

    def test_over_cap_jobs_are_reported_not_discarded(self):
        warnings = []
        jobs = [job(key=f"k{i}", score=80) for i in range(7)]
        kept, rejected, _ = gate.apply_gate(jobs, set(), 75, 60, 5, warnings.append)
        self.assertEqual(len(rejected), 2)
        self.assertIn("over the cap", rejected[0]["gate_note"])
        self.assertTrue(any("cap" in w for w in warnings),
                        f"a truncated draft list must say so: {warnings}")

    def test_higher_scores_are_drafted_first(self):
        jobs = [job(key="low", score=76), job(key="high", score=95)]
        kept, _, _ = gate.apply_gate(jobs, set(), 75, 60, 5, quiet)
        self.assertEqual([j["key"] for j in kept], ["high", "low"])

    def test_alert_matched_wins_an_equal_score(self):
        jobs = [job(key="plain", score=80), job(key="alerted", score=80)]
        kept, _, _ = gate.apply_gate(jobs, {"alerted"}, 75, 60, 5, quiet)
        self.assertEqual([j["key"] for j in kept], ["alerted", "plain"])

    def test_richer_evidence_wins_when_score_and_alert_are_equal(self):
        jobs = [job(key="snippet", score=80),
                job(key="full", score=80, enriched=True)]
        kept, _, _ = gate.apply_gate(jobs, set(), 75, 60, 5, quiet)
        self.assertEqual([j["key"] for j in kept], ["full", "snippet"])

    def test_ties_keep_ranker_order_so_runs_are_reproducible(self):
        jobs = [job(key="a", score=80), job(key="b", score=80)]
        first = gate.apply_gate(jobs, set(), 75, 60, 5, quiet)[0]
        second = gate.apply_gate(jobs, set(), 75, 60, 5, quiet)[0]
        self.assertEqual([j["key"] for j in first], ["a", "b"])
        self.assertEqual([j["key"] for j in second], ["a", "b"])

    def test_a_zero_cap_drafts_nothing_but_reports_everything(self):
        kept, rejected, _ = gate.apply_gate([job(score=90)], set(), 75, 60, 0, quiet)
        self.assertEqual(kept, [])
        self.assertEqual(len(rejected), 1)


class NotDraftedList(unittest.TestCase):
    def test_a_rejected_job_carries_the_fields_the_report_renders(self):
        entry = gate.to_not_drafted({**job(score=70), "gate_note": "below"})
        for field in ("key", "title", "company", "url", "location", "portal",
                      "track", "score", "verdict", "gate_note"):
            self.assertIn(field, entry)

    def test_the_posting_text_is_not_copied_into_the_report_list(self):
        entry = gate.to_not_drafted(job(score=70, posting_text="x" * 5000))
        self.assertNotIn("posting_text", entry,
                         "the report table does not need the full posting")

    def test_the_rankers_own_list_is_preserved(self):
        existing = [{"key": "ranker-one", "title": "Prior", "score": 65}]
        merged = gate.merge_not_drafted(existing, [job(key="gate-one", score=70)], quiet)
        self.assertEqual([e["key"] for e in merged], ["ranker-one", "gate-one"])

    def test_a_job_already_on_the_list_is_not_duplicated(self):
        existing = [{"key": "same", "title": "Prior", "score": 65}]
        merged = gate.merge_not_drafted(existing, [job(key="same", score=70)], quiet)
        self.assertEqual(len(merged), 1)

    def test_a_corrupt_existing_list_is_rebuilt_loudly(self):
        warnings = []
        merged = gate.merge_not_drafted({"not": "a list"},
                                        [job(key="k", score=70)], warnings.append)
        self.assertEqual(len(merged), 1)
        self.assertTrue(warnings)

    def test_a_missing_existing_list_is_fine(self):
        merged = gate.merge_not_drafted(None, [job(key="k", score=70)], quiet)
        self.assertEqual(len(merged), 1)


class CliBehaviour(unittest.TestCase):
    def _write(self, directory, name, payload):
        path = Path(directory) / name
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
        return path

    def _run(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                              capture_output=True, text=True)

    def _config(self, directory, cap=5, min_score=60):
        return self._write(directory, "automation.json", {
            "pipeline": {"max_jobs_to_apply": cap, "min_score_threshold": min_score}})

    def test_it_rewrites_the_jobs_file_as_a_clean_array(self):
        with tempfile.TemporaryDirectory() as d:
            jobs = self._write(d, "top5.json",
                               "```json\n" + json.dumps([job(score=90), job(
                                   key="url:linkedin:4000000001", score=70)]) + "\n```")
            nd = Path(d) / "not_drafted.json"
            proc = self._run("--jobs", jobs, "--not-drafted", nd,
                             "--alerts", Path(d) / "absent.json",
                             "--config", self._config(d), "--today", "2026-08-18")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            kept = json.loads(jobs.read_text())
            self.assertEqual(len(kept), 1, "the fence must be gone and the 70 dropped")
            self.assertEqual(kept[0]["score"], 90)
            self.assertEqual(len(json.loads(nd.read_text())), 1)

    def test_a_missing_alert_store_is_normal_and_the_run_succeeds(self):
        with tempfile.TemporaryDirectory() as d:
            jobs = self._write(d, "top5.json", [job(score=90)])
            proc = self._run("--jobs", jobs, "--alerts", Path(d) / "absent.json",
                             "--config", self._config(d), "--today", "2026-08-18")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["cleared"], 1)
            self.assertIn("no alert store", proc.stderr)

    def test_the_alert_store_widens_the_gate_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            jobs = self._write(d, "top5.json", [job(key="k", score=62)])
            alerts = self._write(d, "alerts.json", {"k": alert(days_ago=3)})
            proc = self._run("--jobs", jobs, "--alerts", alerts,
                             "--config", self._config(d), "--today", "2026-08-18")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            summary = json.loads(proc.stdout)
            self.assertEqual((summary["cleared"], summary["alert_live"]), (1, 1))

    def test_an_expired_store_entry_closes_the_gate_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            jobs = self._write(d, "top5.json", [job(key="k", score=62)])
            alerts = self._write(d, "alerts.json", {"k": alert(days_ago=45)})
            proc = self._run("--jobs", jobs, "--alerts", alerts,
                             "--config", self._config(d), "--today", "2026-08-18")
            summary = json.loads(proc.stdout)
            self.assertEqual((summary["cleared"], summary["alert_expired"]), (0, 1))

    def test_unparseable_ranker_output_exits_nonzero_and_empties_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            jobs = self._write(d, "top5.json", "I was unable to rank the jobs.")
            proc = self._run("--jobs", jobs, "--config", self._config(d),
                             "--alerts", Path(d) / "absent.json")
            self.assertEqual(proc.returncode, 1,
                             "a parse failure is not the same event as 'none qualified'")
            self.assertTrue(json.loads(proc.stdout)["parse_failed"])
            self.assertEqual(json.loads(jobs.read_text()), [])

    def test_an_empty_ranker_array_exits_zero(self):
        with tempfile.TemporaryDirectory() as d:
            jobs = self._write(d, "top5.json", [])
            proc = self._run("--jobs", jobs, "--config", self._config(d),
                             "--alerts", Path(d) / "absent.json")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["cleared"], 0)

    def test_a_missing_jobs_file_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            proc = self._run("--jobs", Path(d) / "absent.json",
                             "--config", self._config(d))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("could not read", proc.stderr)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            jobs = self._write(d, "top5.json", [job(score=90), job(key="k2", score=61)])
            before = jobs.read_text()
            nd = Path(d) / "not_drafted.json"
            proc = self._run("--jobs", jobs, "--not-drafted", nd, "--dry-run",
                             "--alerts", Path(d) / "absent.json",
                             "--config", self._config(d), "--today", "2026-08-18")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(jobs.read_text(), before)
            self.assertFalse(nd.exists())
            self.assertTrue(json.loads(proc.stdout)["dry_run"])

    def test_prune_only_touches_the_store_when_asked(self):
        with tempfile.TemporaryDirectory() as d:
            jobs = self._write(d, "top5.json", [job(score=90)])
            store = {"live": alert(days_ago=2), "old": alert(days_ago=99)}
            alerts = self._write(d, "alerts.json", store)

            self._run("--jobs", jobs, "--alerts", alerts,
                      "--config", self._config(d), "--today", "2026-08-18")
            self.assertEqual(json.loads(alerts.read_text()), store,
                             "an unattended run must not rewrite personal data")

            jobs.write_text(json.dumps([job(score=90)]))
            self._run("--jobs", jobs, "--alerts", alerts, "--prune",
                      "--config", self._config(d), "--today", "2026-08-18")
            self.assertEqual(list(json.loads(alerts.read_text())), ["live"])

    def test_the_cap_comes_from_automation_json(self):
        with tempfile.TemporaryDirectory() as d:
            jobs = self._write(d, "top5.json",
                               [job(key=f"k{i}", score=90) for i in range(5)])
            proc = self._run("--jobs", jobs, "--config", self._config(d, cap=2),
                             "--alerts", Path(d) / "absent.json")
            summary = json.loads(proc.stdout)
            self.assertEqual((summary["cleared"], summary["cap"]), (2, 2))

    def test_a_missing_config_falls_back_to_the_documented_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            jobs = self._write(d, "top5.json",
                               [job(key=f"k{i}", score=90) for i in range(8)])
            proc = self._run("--jobs", jobs, "--config", Path(d) / "absent.json",
                             "--alerts", Path(d) / "absent.json")
            summary = json.loads(proc.stdout)
            self.assertEqual((summary["cleared"], summary["cap"], summary["min_score"]),
                             (5, 5, 60))

    def test_a_bad_today_is_rejected_rather_than_guessed(self):
        with tempfile.TemporaryDirectory() as d:
            jobs = self._write(d, "top5.json", [job(score=90)])
            proc = self._run("--jobs", jobs, "--today", "18-08-2026",
                             "--config", self._config(d))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("YYYY-MM-DD", proc.stderr)


class PipelineWiring(unittest.TestCase):
    SCRIPT_TEXT = (REPO / "scripts" / "run_daily.sh").read_text()
    PROMPT = (REPO / "prompts" / "pipeline_phase1_rank.md").read_text()

    def test_run_daily_enforces_the_gate(self):
        self.assertIn("scripts/gate_jobs.py", self.SCRIPT_TEXT)

    def test_the_gate_runs_after_ranking_and_before_the_handoff(self):
        """Was "before drafting" — the handoff is what drafting became.

        Same invariant either way: the gate has nothing to evaluate until the
        ranker has run, and its verdicts have to exist before the ranked list is
        offered, or the report cannot explain the scores on the list.
        """
        rank_at = self.SCRIPT_TEXT.index("prompts/pipeline_phase1_rank.md")
        gate_at = self.SCRIPT_TEXT.index("scripts/gate_jobs.py")
        handoff_at = self.SCRIPT_TEXT.index("launchctl kickstart")
        self.assertLess(rank_at, gate_at, "there is nothing to gate before ranking")
        self.assertLess(gate_at, handoff_at,
                        "offering the list before gating leaves scores unexplained")

    def test_the_gates_count_is_computed_from_the_gate(self):
        """TOP5_COUNT must come from the gate, not from the ranker's raw output."""
        gate_at = self.SCRIPT_TEXT.index("scripts/gate_jobs.py")
        assign_at = self.SCRIPT_TEXT.index("TOP5_COUNT=$(")
        self.assertLess(gate_at, assign_at)

    def test_a_parse_failure_reaches_the_report(self):
        self.assertIn("Phase 2b", self.SCRIPT_TEXT)
        self.assertIn('>> "$WARN_FILE"', self.SCRIPT_TEXT)

    def test_the_prompt_and_the_code_state_the_same_gate(self):
        self.assertIn("score >= 75", self.PROMPT)
        self.assertIn("alert_matched.json", self.PROMPT)
        self.assertEqual((gate.STRONG_SCORE, gate.MIN_SCORE), (75, 60),
                         "the code's thresholds must match the prompt's wording")

    def test_the_prompt_forbids_scoring_a_bonus_for_alert_match(self):
        self.assertIn("Do not add points for it", self.PROMPT)

    def test_the_gate_no_longer_decides_what_gets_drafted(self):
        """SKIP_DRAFT is retired because pipeline-time drafting is retired.

        The flag existed to withhold documents for one run. Withholding is now
        the permanent default: Phase 3 offers the ranked list on Telegram and
        starts the selector, which drafts only what Salman picks. A flag to
        suppress drafting that no longer happens would be dead code, and a
        reader finding it would reasonably assume the old auto-drafting path
        still exists somewhere.
        """
        self.assertNotIn("SKIP_DRAFT", self.SCRIPT_TEXT)
        self.assertNotIn("prompts/pipeline_phase2_draft.md", self.SCRIPT_TEXT)

    def test_the_gate_still_runs_and_still_reports(self):
        """Retiring auto-drafting must not cost the run its verdicts.

        The gate's output is now purely informational — it ranks and explains
        rather than authorising documents — but that information is the whole
        content of the daily report, so it has to be computed either way.
        """
        self.assertIn("scripts/gate_jobs.py", self.SCRIPT_TEXT)
        self.assertIn("Phase 2b", self.SCRIPT_TEXT)

    def test_phase_3_hands_off_to_the_selector_after_the_gate(self):
        """Order matters for the same reason it did with SKIP_DRAFT.

        The selection list is offered from the rankset, which Phase 1b-final
        writes, and the report still needs Phase 2b's verdicts. Starting the
        selector before the gate would offer a list whose scores the report
        cannot explain.
        """
        gate_at = self.SCRIPT_TEXT.index("scripts/gate_jobs.py")
        handoff_at = self.SCRIPT_TEXT.index("launchctl kickstart")
        self.assertLess(gate_at, handoff_at)

    def test_a_failed_handoff_is_reported_not_silent(self):
        """"Nothing to apply for" and "the list never arrived" differ.

        If launchctl cannot start the selector the run still produces a report,
        and that report must not read as a day with no opportunities.

        Anchored on the failure branch rather than a byte window around the
        kickstart, so adding a comment near the handoff cannot break it.
        """
        at = self.SCRIPT_TEXT.index("launchctl kickstart")
        block = self.SCRIPT_TEXT[at : self.SCRIPT_TEXT.index("Phase 3: skipped")]
        self.assertIn('>> "$WARN_FILE"', block)
        self.assertIn("could not start", block)

    def test_the_rankset_outlives_the_run_that_offered_it(self):
        """The selector reads the rankset when the button is pressed.

        That can be hours later, and again after a KeepAlive restart. Phase 7
        used to delete it, which would leave the listener with nothing to offer
        and no way to recover.
        """
        cleanup = self.SCRIPT_TEXT[self.SCRIPT_TEXT.index("Phase 7: Cleanup"):]
        deletions = [
            line for line in cleanup.splitlines()
            if line.strip().startswith("rm ") and "$RANKSET_FILE" in line
        ]
        self.assertEqual(deletions, [],
                         "Phase 7 deletes the rankset the selector still needs")

    def test_the_email_digest_is_retired_without_dropping_the_imap_credential(self):
        """Retiring the outbound digest must not break Phase 0b.

        scripts/linkedin_alerts.py reads the LinkedIn job alerts over IMAP using
        the same Gmail app password in automation.json's email block. Deleting
        that block to tidy up after the digest would silently cost the corpus
        its alert-sourced jobs.
        """
        self.assertNotIn("SKIP_EMAIL", self.SCRIPT_TEXT)
        # Checked per line rather than with a bare `assertNotIn`: the retirement
        # comment names the script it retired, which is worth keeping. What must
        # be gone is any line that actually runs it.
        invocations = [
            line for line in self.SCRIPT_TEXT.splitlines()
            if "send_email.py" in line and not line.lstrip().startswith("#")
        ]
        self.assertEqual(invocations, [], f"the digest is still sent: {invocations}")
        alerts = (REPO / "scripts" / "linkedin_alerts.py").read_text()
        self.assertIn("smtp_password", alerts,
                      "Phase 0b still needs the email block's credential")

    def test_the_alert_store_is_gitignored(self):
        """It holds job keys and URLs from Salman's own email — personal data."""
        result = subprocess.run(
            ["git", "check-ignore", "job_scraper/alert_matched.json"],
            cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         "job_scraper/alert_matched.json must never be committable")


if __name__ == "__main__":
    unittest.main()
