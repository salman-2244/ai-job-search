"""Stage 3 run-scoped output paths: layout, legacy read-only detection, threading.

Every check here runs offline. The subprocess paths (`generate_one`'s `claude`
call, the drafter prompt) are not exercised; what is exercised is the contract
they consume — that the four artifact paths are distinct per run, that a
complete legacy application is recognized without being touched, that
`run_daily.sh` carries the new Stage 3 variables and hands them to the
listener, and that the listener treats a handoff's run_id as untrusted input.

`run_daily.sh` itself is NEVER executed by these tests — it is a real pipeline
that queries portals and takes a lock. Its validation block is pinned by
`bash -n` plus source assertions; the pattern's behavior (reject `../evil`,
`.hidden`, spaces; accept the orchestrator's ids) is verified empirically on
the shell and recorded in the task report, not re-proven by running the script.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "telegram_select", REPO / "scripts" / "telegram_select.py"
)
ts = importlib.util.module_from_spec(_spec)
sys.modules["telegram_select"] = ts
_spec.loader.exec_module(ts)

_listener_spec = importlib.util.spec_from_file_location(
    "selector_listener_testee", REPO / "scripts" / "selector_listener.py"
)
listener = importlib.util.module_from_spec(_listener_spec)
sys.modules["selector_listener_testee"] = listener
_listener_spec.loader.exec_module(listener)

_batch_spec = importlib.util.spec_from_file_location(
    "generate_batch_testee", REPO / "scripts" / "generate_batch.py"
)
batch = importlib.util.module_from_spec(_batch_spec)
sys.modules["generate_batch_testee"] = batch
_batch_spec.loader.exec_module(batch)


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="stage3-outputs-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


# -- layout ---------------------------------------------------------------------


class OutputPathLayout(TempDirCase):
    def test_same_day_runs_do_not_share_output_paths(self):
        one = ts.output_paths(Path(self.tmp), "2026-09-08", "run-a", "acme-role")
        two = ts.output_paths(Path(self.tmp), "2026-09-08", "run-b", "acme-role")
        self.assertNotEqual(one["cv_tex"], two["cv_tex"])
        self.assertIn("2026-09-08/run-a", str(one["cv_tex"]))

    def test_all_four_artifacts_share_the_run_directory(self):
        paths = ts.output_paths(Path(self.tmp), "2026-09-08", "run-a", "acme-role")
        self.assertEqual(sorted(paths), ["cl_pdf", "cl_tex", "cv_pdf", "cv_tex"])
        self.assertEqual({p.parent.name for p in paths.values()}, {"acme-role"})
        self.assertTrue(str(paths["cv_tex"]).startswith(str(self.tmp / "cv" / "2026-09-08" / "run-a")))
        self.assertTrue(
            str(paths["cl_tex"]).startswith(
                str(self.tmp / "cover_letters" / "2026-09-08" / "run-a")
            )
        )

    def test_run_id_none_falls_back_to_the_legacy_layout(self):
        paths = ts.output_paths(Path(self.tmp), "2026-09-08", None, "acme-role")
        self.assertEqual(
            str(paths["cv_tex"]), str(self.tmp / "cv" / "acme-role" / "Salman-Resume.tex")
        )

    def test_malformed_run_ids_are_rejected_before_any_write(self):
        for bad in ("../evil", "a/b", "a\\b", ".hidden", "", " "):
            with self.assertRaises(ValueError):
                ts.doc_dir_for("2026-09-08", bad, "acme-role")

    def test_malformed_slug_is_rejected(self):
        for bad in ("../evil", "a/b", "", ".hidden"):
            with self.assertRaises(ValueError):
                ts.doc_dir_for("2026-09-08", "run-a", bad)

    def test_malformed_day_is_rejected(self):
        for bad in ("2026-9-8", "20260908", "nope", ""):
            with self.assertRaises(ValueError):
                ts.doc_dir_for(bad, "run-a", "acme-role")

    def test_valid_orchestrator_ids_are_accepted(self):
        # new_run_id's own output must pass the same validation it feeds into:
        # the orchestrator generates ids, telegram_select consumes them.
        sys.path.insert(0, str(REPO))
        try:
            from stage_3.orchestrator import new_run_id

            rid = new_run_id()
            ts.doc_dir_for("2026-09-08", rid, "acme-role")  # must not raise
        finally:
            sys.path.remove(str(REPO))


    def test_generate_one_prompt_targets_external_output_root(self):
        external = self.tmp / "external"
        captured = {}
        row = ts.JobRow(
            idx=0,
            key="job-1",
            company="Acme",
            title="Data Role",
            location="Remote",
            score=90,
            tier="strong",
            source="synthetic",
            language="pass",
            experience="pass",
            url="https://example.test/job",
        )

        class FakeProcess:
            returncode = 1

            async def communicate(self):
                return b"", b"synthetic stop after prompt capture"

        async def fake_exec(*args, **kwargs):
            captured["prompt"] = args[2]
            return FakeProcess()

        original = ts.asyncio.create_subprocess_exec
        ts.asyncio.create_subprocess_exec = fake_exec
        try:
            asyncio.run(ts.generate_one(
                row,
                "2026-09-08",
                asyncio.Semaphore(1),
                self.tmp / "generation.log",
                run_id="run-a",
                output_root=external,
            ))
        finally:
            ts.asyncio.create_subprocess_exec = original

        self.assertIn(f"Use the output root: `{external}`", captured["prompt"])
        self.assertIn(f"Create `{external}/cv/2026-09-08/run-a/{row.slug}/Salman-Resume.tex`", captured["prompt"])
        self.assertNotIn("<OUTPUT_ROOT>", captured["prompt"])


# -- completeness -----------------------------------------------------------------


class CompletenessTests(TempDirCase):
    def test_complete_legacy_output_is_detected_without_moving_it(self):
        legacy = self.tmp / "cv" / "acme-role"
        letters = self.tmp / "cover_letters" / "acme-role"
        for path in (
            legacy / "Salman-Resume.tex",
            legacy / "Salman-Resume.pdf",
            letters / "Salman-Cover-Letter.tex",
            letters / "Salman-Cover-Letter.pdf",
        ):
            touch(path)
        before = sorted(p.name for p in legacy.iterdir())
        self.assertTrue(ts.legacy_is_complete("acme-role", self.tmp))
        self.assertEqual(sorted(p.name for p in legacy.iterdir()), before)
        self.assertTrue((legacy / "Salman-Resume.tex").exists())
        self.assertTrue((letters / "Salman-Cover-Letter.pdf").exists())

    def test_partial_legacy_output_is_never_reported_complete_nor_deleted(self):
        # A .tex without its .pdf is a draft that never compiled: not complete,
        # and left exactly where it is — a human decides what happens to it.
        cv = self.tmp / "cv" / "acme-role"
        touch(cv / "Salman-Resume.tex")
        self.assertFalse(ts.legacy_is_complete("acme-role", self.tmp))
        self.assertTrue((cv / "Salman-Resume.tex").exists())
        self.assertFalse(ts.is_complete("acme-role", output_root=self.tmp))

    def test_run_scoped_complete_requires_all_four_under_the_run_dir(self):
        paths = ts.output_paths(self.tmp, "2026-09-08", "run-a", "acme-role")
        for path in paths.values():
            touch(path)
        self.assertTrue(
            ts.is_complete(
                "acme-role", output_root=self.tmp, day="2026-09-08", run_id="run-a"
            )
        )

    def test_run_scoped_complete_does_not_succeed_on_legacy_files(self):
        # The failure the shared-layout delegation exists to prevent: a legacy-
        # finished slug must not read as complete for the run, or --all-missing
        # would skip regeneration forever while the run's own tree stays empty.
        legacy_cv = self.tmp / "cv" / "acme-role"
        legacy_cl = self.tmp / "cover_letters" / "acme-role"
        for path in (
            legacy_cv / "Salman-Resume.tex",
            legacy_cv / "Salman-Resume.pdf",
            legacy_cl / "Salman-Cover-Letter.tex",
            legacy_cl / "Salman-Cover-Letter.pdf",
        ):
            touch(path)
        self.assertFalse(
            ts.is_complete(
                "acme-role", output_root=self.tmp, day="2026-09-08", run_id="run-a"
            )
        )

    def test_run_scoped_missing_artifacts_are_reported_not_assumed(self):
        paths = ts.output_paths(self.tmp, "2026-09-08", "run-a", "acme-role")
        for key in ("cv_tex", "cv_pdf", "cl_tex"):
            touch(paths[key])  # cl_pdf deliberately absent
        self.assertFalse(
            ts.is_complete(
                "acme-role", output_root=self.tmp, day="2026-09-08", run_id="run-a"
            )
        )

    def test_batch_skip_gate_honours_complete_legacy_output(self):
        legacy = ts.legacy_artifacts("acme-role", self.tmp)
        for path in legacy:
            touch(path)
        before = {path: path.read_bytes() for path in legacy}
        old_run_id = batch._RUN_ID
        old_run_complete = batch._ts_is_complete
        old_legacy_complete = getattr(batch, "_ts_legacy_is_complete", None)
        try:
            batch._RUN_ID = "run-a"
            batch._ts_is_complete = lambda slug: False
            batch._ts_legacy_is_complete = lambda slug: ts.legacy_is_complete(
                slug, self.tmp
            )
            self.assertTrue(batch.is_complete("acme-role"))
        finally:
            batch._RUN_ID = old_run_id
            batch._ts_is_complete = old_run_complete
            batch._ts_legacy_is_complete = old_legacy_complete
        self.assertEqual({path: path.read_bytes() for path in legacy}, before)

    def test_job_file_path_is_run_scoped_when_a_run_id_is_given(self):
        # Concurrent runs on one day must not collide on the JSON payload file.
        self.assertEqual(
            ts.job_file_path("2026-09-08", 3, "run-a").name,
            "jobsearch_selected_2026-09-08_run-a_3.json",
        )
        self.assertEqual(
            ts.job_file_path("2026-09-08", 3).name,
            "jobsearch_selected_2026-09-08_3.json",
        )


# -- fixup --------------------------------------------------------------------------


class FixupTests(TempDirCase):
    def test_fixup_rewrites_reported_paths_to_the_real_layout(self):
        result = {
            "cv_file": "cv/acme-role/Salman-Resume.tex",
            "cv_pdf": "cv/acme-role/Salman-Resume.pdf",
            "cover_letter_file": "cover_letters/acme-role/Salman-Cover-Letter.tex",
            "cl_pdf": "cover_letters/acme-role/Salman-Cover-Letter.pdf",
        }
        ts.fixup_run_scoped_result(result, "2026-09-08", "run-a", "acme-role", self.tmp)
        self.assertEqual(
            result["cv_file"],
            str(Path("cv/2026-09-08/run-a/acme-role/Salman-Resume.tex")),
        )
        self.assertEqual(
            result["cl_pdf"],
            str(Path("cover_letters/2026-09-08/run-a/acme-role/Salman-Cover-Letter.pdf")),
        )

    def test_fixup_uses_the_callers_slug_not_the_drafters_json(self):
        # The drafter's output contract predates run scoping: it names no slug
        # field. A fixup that read the slug from `result` would silently skip
        # every rewrite, leaving tracker rows pointing at cv/<slug>/ paths that
        # do not exist for this run.
        result = {"cv_file": "cv/acme-role/Salman-Resume.tex"}
        ts.fixup_run_scoped_result(result, "2026-09-08", "run-a", "other-slug", self.tmp)
        self.assertIn("2026-09-08/run-a/other-slug", result["cv_file"])

    def test_fixup_with_an_empty_slug_leaves_the_result_alone(self):
        result = {"cv_file": "cv/acme-role/Salman-Resume.tex"}
        ts.fixup_run_scoped_result(result, "2026-09-08", "run-a", "", self.tmp)
        self.assertEqual(result["cv_file"], "cv/acme-role/Salman-Resume.tex")


# -- run_daily.sh threading ------------------------------------------------------------


class RunDailyThreading(TempDirCase):
    """`run_daily.sh` is never executed by tests; syntax + wiring are pinned here."""

    def test_script_syntax_stays_clean(self):
        proc = subprocess.run(
            ["bash", "-n", str(REPO / "scripts" / "run_daily.sh")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_variables_are_defaulted_and_validated(self):
        text = (REPO / "scripts" / "run_daily.sh").read_text(encoding="utf-8")
        self.assertIn('RUN_ID="${RUN_ID:-}"', text)
        self.assertIn('OUTPUT_ROOT="${OUTPUT_ROOT:-}"', text)
        self.assertIn('JOB_COUNT="${JOB_COUNT:-}"', text)
        # Both the malformed-run-id and the job-count bounds are stated in the
        # validation block, which sits before SKIP_ALERTS' block (i.e. before
        # any real work).
        self.assertIn("FATAL: RUN_ID is not path-safe", text)
        self.assertIn("FATAL: JOB_COUNT must be a whole number between 1 and 50", text)
        validation_at = text.index('JOB_COUNT="${JOB_COUNT:-}"')
        self.assertLess(validation_at, text.index('SKIP_ALERTS="${SKIP_ALERTS:-0}"'))

    def test_job_count_cut_and_handoff_threading_present(self):
        text = (REPO / "scripts" / "run_daily.sh").read_text(encoding="utf-8")
        # The Phase 1b-final cut (guarded, array-safe expansion — the plain
        # ${ARR[@]} form aborts under set -u on bash 3.2 when the array is
        # empty) and the Phase 3 handoff carrying run_id/output_root.
        self.assertIn('JOB_COUNT_ARGS=("--budget" "$JOB_COUNT")', text)
        self.assertIn('JOB_COUNT_ARGS[@]+"${JOB_COUNT_ARGS[@]}', text)
        self.assertIn('write_selection_handoff.py', text)
        self.assertIn('"$TODAY" "$RANKSET_FILE" "$RUN_ID" "$OUTPUT_ROOT"', text)
        # The run id lands in new log metadata without touching the date-keyed
        # file names the earlier phases read.
        self.assertIn('log "Stage 3 run id: $RUN_ID"', text)

    def test_handoff_writer_serializes_special_characters(self):
        from scripts.write_selection_handoff import write_handoff

        pending = self.tmp / "pending.json"
        rankset = self.tmp / 'rankset-"quoted"-\\backslash\nline.json'
        output_root = self.tmp / 'outputs-"quoted"-\\backslash\nline'
        write_handoff(
            pending,
            today="2026-09-08",
            rankset=rankset,
            run_id="run-1",
            output_root=output_root,
        )

        payload = json.loads(pending.read_text(encoding="utf-8"))
        self.assertEqual(payload["rankset"], str(rankset))
        self.assertEqual(payload["output_root"], str(output_root))
        self.assertEqual(payload["run_id"], "run-1")
        self.assertEqual(list(self.tmp.glob(".pending.json.*")), [])


# -- listener handoff -------------------------------------------------------------------


class ListenerHandoffTests(TempDirCase):
    def write(self, payload: dict) -> Path:
        path = self.tmp / "pending.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_handoff_without_run_scoped_keys_stays_legacy(self):
        handoff = listener.read_handoff(
            self.write({"today": "2026-09-08", "rankset": "/tmp/r.json"})
        )
        self.assertEqual(handoff, {"today": "2026-09-08", "rankset": Path("/tmp/r.json")})

    def test_handoff_with_valid_run_id_and_output_root(self):
        handoff = listener.read_handoff(
            self.write(
                {
                    "today": "2026-09-08",
                    "rankset": "/tmp/r.json",
                    "run_id": "20260908T120000Z-abc123",
                    "output_root": "/tmp/outs",
                }
            )
        )
        self.assertEqual(handoff["run_id"], "20260908T120000Z-abc123")
        self.assertEqual(handoff["output_root"], Path("/tmp/outs"))

    def test_handoff_run_id_is_validated_never_sanitized(self):
        for bad in ("../evil", "a/b", ".hidden", ""):
            handoff = listener.read_handoff(
                self.write({"today": "2026-09-08", "rankset": "/tmp/r.json", "run_id": bad})
            )
            self.assertNotIn("run_id", handoff)

    def test_handoff_without_output_root_omits_it(self):
        handoff = listener.read_handoff(
            self.write({"today": "2026-09-08", "rankset": "/tmp/r.json", "run_id": "run-1"})
        )
        self.assertEqual(handoff["run_id"], "run-1")
        self.assertNotIn("output_root", handoff)


if __name__ == "__main__":
    unittest.main()
