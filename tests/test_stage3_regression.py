"""Cross-module regressions for the Stage 3 audit remediations."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone

import pytest

from scripts import generate_batch
from scripts.linkedin_playwright import (
    PlaywrightDetailProvider,
    PlaywrightStorageStateError,
)
from scripts.telegram_select import legacy_is_complete
from scripts.write_selection_handoff import write_handoff
from stage_3.bot import restore_active_run, scheduler_tick
from stage_3.orchestrator import Orchestrator
from stage_3.schedules import Schedule, ScheduleStore, ScheduleStoreError, due
from tests.test_stage3_bot import FakeContext
from tests.test_stage3_orchestrator import FakeClock, FakeProcess, request


def run_coro(coro):
    return asyncio.run(coro)


def test_bot_startup_with_none_state(tmp_path):
    context = FakeContext(tmp_path)
    context.bot_data["orchestrator"].restored = None

    assert restore_active_run(context.bot_data) is None
    assert "active_handle" not in context.bot_data


def test_scheduler_aborts_on_save_failure(tmp_path):
    record = Schedule(
        id="s-restart", kind="recurring", expression="0 8 * * 1-5",
        geo="Germany", job_count=10, timezone="UTC",
    )
    durable = ScheduleStore(tmp_path / "schedules.json")
    durable.save([record])

    class FailingStore:
        def load(self):
            return durable.load()

        def save(self, records):
            raise ScheduleStoreError("synthetic save failure")

    now = datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc)
    context = FakeContext(tmp_path, store=FailingStore())
    run_coro(scheduler_tick(context, now=now))

    assert context.bot_data["orchestrator"].requests == []
    unchanged = durable.load()[0]
    assert unchanged.last_started_at is None
    assert due([unchanged], now) == [unchanged]


def test_notification_deduplication_independence(tmp_path):
    notifications = []
    proc = FakeProcess(
        lines=["[10:00:00] Phase 1: Fetching jobs from portals...\n"],
        hold_open=True,
        ignore_terminate=True,
    )
    clock = FakeClock()
    orchestrator = Orchestrator(
        config=None,
        runner=lambda *args: proc,
        clock=clock,
        sleeper=clock.advance,
        lock_dir=None,
        poll_interval=0,
        stall_after=2,
        warn_every=120,
        max_runtime=5,
        kill_after=1,
        notify=notifications.append,
    )

    result = orchestrator.start(
        request(tmp_path), state_root=tmp_path, today="2026-09-08"
    ).wait()

    assert result is not None and result.status == "timeout"
    assert any("still working" in text.lower() for text in notifications)
    assert len([n for n in notifications if "terminal: timeout" in n.lower()]) == 1


def test_legacy_skip_gate(tmp_path, monkeypatch):
    slug = "acme-role"
    for path in (
        tmp_path / "cv" / slug / "Salman-Resume.tex",
        tmp_path / "cv" / slug / "Salman-Resume.pdf",
        tmp_path / "cover_letters" / slug / "Salman-Cover-Letter.tex",
        tmp_path / "cover_letters" / slug / "Salman-Cover-Letter.pdf",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("complete", encoding="utf-8")

    assert legacy_is_complete(slug, tmp_path)
    monkeypatch.setattr(generate_batch, "_RUN_ID", "run-1")
    monkeypatch.setattr(generate_batch, "_ts_is_complete", lambda value: False)
    monkeypatch.setattr(
        generate_batch, "_ts_legacy_is_complete",
        lambda value: legacy_is_complete(value, tmp_path),
    )
    assert generate_batch.is_complete(slug)


def test_storage_state_permission_enforcement(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")
    os.chmod(state, 0o640)
    provider = PlaywrightDetailProvider(storage_state=state)

    with pytest.raises(PlaywrightStorageStateError, match="0600"):
        provider.fetch("123", ledger=None)


def test_safe_handoff_json(tmp_path):
    target = tmp_path / "pending.json"
    rankset = tmp_path / 'rankset-"quoted"-\\backslash\nline.json'
    output_root = tmp_path / 'output-"quoted"-\\backslash\nline'

    write_handoff(
        target,
        today="2026-09-08",
        rankset=rankset,
        run_id="run-1",
        output_root=output_root,
    )

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload == {
        "today": "2026-09-08",
        "rankset": str(rankset),
        "run_id": "run-1",
        "output_root": str(output_root),
    }
    assert target.stat().st_mode & 0o777 == 0o600
