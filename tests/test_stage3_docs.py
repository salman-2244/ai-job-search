"""Task 9 tests: the Stage 3 setup/operations guide stays complete.

The Stage 3 brief makes the setup instructions an explicit deliverable —
"Show me the instructions for setting up the Telegram token, and how to run
the orchestrator." A guide that drifts out of sync with the bot's commands or
that starts recommending committing secrets would fail exactly the way a
missing guide would, so the checks below are text-level and cheap on purpose:
they read `docs/STAGE_3.md` the way a newcomer does and assert the things a
newcomer cannot proceed without.

Offline, stdlib-only, no network and no Telegram import needed.
"""
from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GUIDE = REPO / "docs" / "STAGE_3.md"
HISTORICAL_CHECKPOINT = REPO / "docs" / "STAGE_3_RESUME.md"


def guide_text() -> str:
    return GUIDE.read_text(encoding="utf-8")


class TheGuideNamesEveryCommand(unittest.TestCase):
    def test_stage3_docs_name_all_commands_and_never_recommend_repo_secrets(self):
        text = guide_text()
        for command in ("/start", "/status", "/run", "/cancel", "/schedule",
                        "/schedule_recurring", "/list_schedules", "/cancel_schedule"):
            with self.subTest(command=command):
                self.assertIn(command, text)
        # The env file lives outside the repo and its values are never copied in.
        self.assertIn("never commit", text.lower())
        self.assertIn(".jobsearch-stage3.env", text)


class TheGuideCarriesTheOperationalTruths(unittest.TestCase):
    def test_the_third_token_and_getupdates_isolation_is_explained(self):
        """One getUpdates consumer per token: the collision failure must be named."""
        text = guide_text().lower()
        self.assertIn("third", text)
        self.assertIn("getupdates", text)

    def test_the_entry_point_is_the_module(self):
        text = guide_text()
        self.assertIn("python -m stage_3.bot", text)

    def test_pdfs_stay_on_disk_and_messages_are_untrusted(self):
        text = guide_text().lower()
        self.assertIn("pdf", text)
        self.assertIn("untrusted", text)

    def test_the_optional_playwright_tier_is_documented(self):
        text = guide_text().lower()
        self.assertIn("playwright", text)
        self.assertIn("storage state", text)
        self.assertIn("terminal", text)

    def test_schedule_recovery_is_described_not_guessed(self):
        text = guide_text().lower()
        self.assertIn(".bak", text)

    def test_schedule_save_failure_is_fail_closed_and_retryable(self):
        text = guide_text().lower()
        self.assertIn("no job launches", text)
        self.assertIn("remain unchanged", text)
        self.assertIn("still due on the next tick or restart", text)

    def test_restart_identity_and_run_scoped_log_are_documented(self):
        text = guide_text()
        self.assertIn("YYYYMMDDThhmmssZ-<6 hex>", text)
        self.assertIn("pipeline.log", text)
        self.assertIn("process-start marker", text)
        self.assertIn("terminal `interrupted`", text)
        self.assertIn("more than one process identity", text)
        self.assertIn("does not reset that budget", text)

    def test_storage_state_is_the_only_auth_mechanism_and_exactly_0600(self):
        text = guide_text().lower()
        self.assertIn("only authenticated\n  playwright input", text)
        self.assertIn("exact mode `0600`", text)
        self.assertIn("reject any broader unix permissions", text)
        self.assertNotIn("linkedin_email=", text)
        self.assertNotIn("linkedin_password=", text)

    def test_resume_file_is_explicitly_historical_not_a_live_checkpoint(self):
        text = HISTORICAL_CHECKPOINT.read_text(encoding="utf-8").lower()
        self.assertIn("historical build checkpoint", text)
        self.assertIn("not the current", text)
        self.assertNotIn("resume checkpoint (final", text)


if __name__ == "__main__":
    unittest.main()
