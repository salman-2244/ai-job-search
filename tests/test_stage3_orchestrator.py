import json
import threading
from datetime import datetime, timezone

import pytest

from stage_3.config import Stage3Config
from stage_3.orchestrator import (
    MAX_JOB_COUNT,
    Orchestrator,
    RunRefusedError,
    RunRequest,
    atomic_json_write,
    new_run_id,
    retain_run_logs,
    select_resumable,
    validate_run_id,
)


class FakeClock:
    def __init__(self, start=0.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeProcess:
    """A process double: scripted stdout lines plus observable signals.

    `hold_open` keeps stdout open after the scripted lines are consumed, which is
    how a real long-running pipeline looks to the monitor. `terminate` closes
    stdout unless the process "ignores" SIGTERM, in which case only `kill` ends it.
    """

    def __init__(self, lines=(), exit_code=0, hold_open=False, ignore_terminate=False):
        self._lines = list(lines)
        self.exit_code = exit_code
        self.hold_open = hold_open
        self.ignore_terminate = ignore_terminate
        self.pid = 42424
        self.terminated = False
        self.killed = False
        self._read_index = 0
        self._closed = threading.Event()
        self._lock = threading.Lock()

    @property
    def stdout(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        with self._lock:
            if self._read_index < len(self._lines):
                line = self._lines[self._read_index]
                self._read_index += 1
                return line
        if not self.hold_open:
            raise StopIteration
        self._closed.wait()
        raise StopIteration

    def poll(self):
        if self.hold_open and not self._closed.is_set():
            return None
        with self._lock:
            done = self._read_index >= len(self._lines)
        return self.exit_code if done else None

    def wait(self):
        return self.poll()

    def terminate(self):
        self.terminated = True
        if not self.ignore_terminate:
            self._closed.set()

    def kill(self):
        self.killed = True
        self._closed.set()


SYNTHETIC_CONFIG = Stage3Config(
    bot_token="123456:synthetic-secret-value",
    chat_id=42,
    allowed_user_ids=(42,),
)


def make_orchestrator(lines=(), exit_code=0, hold_open=False, clock=None, **kwargs):
    clock = clock or FakeClock()
    proc = FakeProcess(lines=lines, exit_code=exit_code, hold_open=hold_open)
    orchestrator = Orchestrator(
        config=SYNTHETIC_CONFIG,
        runner=lambda request, command, env, cwd: proc,
        clock=clock,
        sleeper=clock.advance,
        lock_dir=None,
        poll_interval=0.0,
        max_runtime=0,
        **kwargs,
    )
    return orchestrator, proc, clock


def request(tmp_path, **overrides):
    defaults = dict(geo=None, job_count=10)
    defaults.update(overrides)
    return RunRequest(**defaults)


def wait_for(handle):
    result = handle.wait()
    assert result is not None
    return result


def test_same_day_runs_get_distinct_ids_and_manifests():
    first = new_run_id(datetime(2026, 9, 8, tzinfo=timezone.utc), "aaaaaa")
    second = new_run_id(datetime(2026, 9, 8, tzinfo=timezone.utc), "bbbbbb")
    assert first != second
    assert first.startswith("20260908T")
    assert first.endswith("-aaaaaa")
    validate_run_id(first)
    validate_run_id(second)


def test_unsafe_run_ids_are_rejected():
    for unsafe in ("../escape", ".hidden", "a/b", "a b", "", "x" * 100, "..", "a..b"):
        with pytest.raises(ValueError):
            validate_run_id(unsafe)


def test_atomic_manifest_replace_recovers_without_partial_json(tmp_path):
    path = tmp_path / "nested" / "manifest.json"
    atomic_json_write(path, {"run_id": "synthetic", "status": "running"})
    assert json.loads(path.read_text())["status"] == "running"
    assert not list(tmp_path.rglob("*.tmp"))


def test_successful_run_projects_log_into_state_and_manifest(tmp_path):
    lines = [
        "[10:00:00] Phase 1 complete: 37 unique jobs fetched\n",
        "[10:05:00] Phase 2 complete: 8 of 37 jobs ranked\n",
        "[10:06:00] Pipeline complete.\n",
    ]
    orchestrator, _, _ = make_orchestrator(lines=lines)
    handle = orchestrator.start(request(tmp_path), state_root=tmp_path, today="2026-09-08")
    result = wait_for(handle)
    assert result.status == "complete"
    assert result.exit_code == 0
    assert result.state.pct == 100
    manifest = json.loads(result.manifest.read_text())
    assert manifest["status"] == "complete"
    assert manifest["job_count"] == 10
    assert manifest["run_id"] == result.run_id
    assert result.manifest.parent == tmp_path / "2026-09-08" / result.run_id
    log_text = (result.manifest.parent / "pipeline.log").read_text()
    assert "Phase 1 complete" in log_text


def test_run_environment_carries_run_id_count_geo_and_no_token(tmp_path):
    seen = {}

    def runner(req, command, env, cwd):
        seen.update(env=env, command=command)
        return FakeProcess(lines=["[10:00:00] Pipeline complete.\n"])

    orchestrator = Orchestrator(
        config=SYNTHETIC_CONFIG,
        runner=runner,
        clock=FakeClock(),
        sleeper=lambda _: None,
        lock_dir=None,
    )
    handle = orchestrator.start(
        request(tmp_path, geo="Germany", env={"RESUME": "1"}),
        state_root=tmp_path,
        today="2026-09-08",
    )
    wait_for(handle)
    env = seen["env"]
    assert env["GEO_FILTER"] == "Germany"
    assert env["JOB_COUNT"] == "10"
    assert env["RESUME"] == "1"
    assert env["RUN_ID"]
    assert "STAGE3_BOT_TOKEN" not in env
    assert "synthetic-secret-value" not in json.dumps(seen["env"])
    assert command_is_clean(seen["command"])


def command_is_clean(command):
    text = " ".join(command)
    return "synthetic-secret-value" not in text and text.startswith("bash")


def test_failing_run_is_classified_not_completed(tmp_path):
    lines = [
        "[10:00:00] Phase 1 complete: 5 unique jobs fetched\n",
        "[10:01:00] Phase 2 FAILED (exit 1)\n",
    ]
    orchestrator, _, _ = make_orchestrator(lines=lines, exit_code=1)
    handle = orchestrator.start(request(tmp_path), state_root=tmp_path, today="2026-09-08")
    result = wait_for(handle)
    assert result.status == "failed"
    manifest = json.loads(result.manifest.read_text())
    assert manifest["exit_code"] == 1
    assert manifest["resumable"] is True


def test_job_count_is_bounded(tmp_path):
    orchestrator, _, _ = make_orchestrator()
    with pytest.raises(RunRefusedError):
        orchestrator.start(request(tmp_path, job_count=MAX_JOB_COUNT + 1),
                           state_root=tmp_path, today="2026-09-08")


def test_second_start_while_active_is_refused_not_queued(tmp_path):
    orchestrator, proc, _ = make_orchestrator(hold_open=True)
    first = orchestrator.start(request(tmp_path), state_root=tmp_path, today="2026-09-08")
    try:
        with pytest.raises(RunRefusedError):
            orchestrator.start(request(tmp_path), state_root=tmp_path, today="2026-09-08")
    finally:
        first.cancel()
        wait_for(first)
    assert first.result().status == "cancelled"


def test_start_after_finish_is_allowed_again(tmp_path):
    lines = ["[10:00:00] Pipeline complete.\n"]
    clock = FakeClock()
    # The runner must hand out a fresh process per start, exactly like the
    # production runner spawns a new PopenProcess each time.
    orchestrator = Orchestrator(
        config=SYNTHETIC_CONFIG,
        runner=lambda request, command, env, cwd: FakeProcess(lines=lines),
        clock=clock,
        sleeper=clock.advance,
        lock_dir=None,
        poll_interval=0.0,
        max_runtime=0,
    )
    first = orchestrator.start(request(tmp_path), state_root=tmp_path, today="2026-09-08")
    wait_for(first)
    second = orchestrator.start(request(tmp_path), state_root=tmp_path, today="2026-09-08")
    assert wait_for(second).status == "complete"


def test_existing_lock_dir_refuses_the_run(tmp_path):
    lock_dir = tmp_path / "lock"
    lock_dir.mkdir()
    orchestrator = Orchestrator(
        config=SYNTHETIC_CONFIG,
        runner=lambda *args: pytest.fail("runner must not be called"),
        clock=FakeClock(),
        sleeper=lambda _: None,
        lock_dir=lock_dir,
    )
    with pytest.raises(RunRefusedError):
        orchestrator.start(request(tmp_path), state_root=tmp_path, today="2026-09-08")


def test_cancel_terminates_and_reports_cancelled(tmp_path):
    orchestrator, proc, _ = make_orchestrator(hold_open=True)
    events = []
    handle = orchestrator.start(request(tmp_path), state_root=tmp_path, today="2026-09-08")
    handle.subscribe(lambda kind, state: events.append(kind))
    handle.cancel()
    result = wait_for(handle)
    assert result.status == "cancelled"
    assert proc.terminated
    assert "terminal" in events


def test_cancel_escalates_to_kill_when_terminate_is_ignored(tmp_path):
    proc = FakeProcess(hold_open=True, ignore_terminate=True)
    clock = FakeClock()
    orchestrator = Orchestrator(
        config=SYNTHETIC_CONFIG,
        runner=lambda request, command, env, cwd: proc,
        clock=clock,
        sleeper=clock.advance,
        lock_dir=None,
        poll_interval=0.0,
        kill_after=2,
    )
    handle = orchestrator.start(request(tmp_path), state_root=tmp_path, today="2026-09-08")
    handle.cancel()
    result = wait_for(handle)
    assert result.status == "cancelled"
    assert proc.terminated and proc.killed


def test_max_runtime_stops_the_run_and_notifies_once(tmp_path):
    proc = FakeProcess(hold_open=True, ignore_terminate=True)
    clock = FakeClock()
    notifications = []
    orchestrator = Orchestrator(
        config=SYNTHETIC_CONFIG,
        runner=lambda request, command, env, cwd: proc,
        clock=clock,
        sleeper=clock.advance,
        lock_dir=None,
        poll_interval=0.0,
        max_runtime=5,
        kill_after=2,
        notify=notifications.append,
    )
    handle = orchestrator.start(request(tmp_path), state_root=tmp_path, today="2026-09-08")
    result = wait_for(handle)
    assert result.status == "timeout"
    assert proc.killed
    manifest = json.loads(result.manifest.read_text())
    assert manifest["status"] == "timeout"
    assert manifest["terminal_notified"] is True
    assert len([n for n in notifications if "timeout" in n.lower()]) == 1


def test_waiting_warning_is_emitted_at_most_every_two_minutes(tmp_path):
    proc = FakeProcess(
        lines=["[10:00:00] Phase 1: Fetching jobs from portals...\n"],
        hold_open=True,
        ignore_terminate=True,
    )
    clock = FakeClock()
    notifications = []
    orchestrator = Orchestrator(
        config=SYNTHETIC_CONFIG,
        runner=lambda request, command, env, cwd: proc,
        clock=clock,
        sleeper=clock.advance,
        lock_dir=None,
        poll_interval=0.0,
        stall_after=120,
        warn_every=120,
        max_runtime=400,
        kill_after=2,
        notify=notifications.append,
    )
    handle = orchestrator.start(request(tmp_path), state_root=tmp_path, today="2026-09-08")
    wait_for(handle)
    waiting = [n for n in notifications if "still working" in n.lower()]
    # The clock passes the 120s, 240s and 360s marks: at most one warning each.
    assert 1 <= len(waiting) <= 3


def test_subscribers_see_progress_and_terminal_events(tmp_path):
    lines = [
        "[10:00:00] Phase 1 complete: 37 unique jobs fetched\n",
        "[10:06:00] Pipeline complete.\n",
    ]
    orchestrator, _, _ = make_orchestrator(lines=lines)
    kinds = []
    handle = orchestrator.start(request(tmp_path), state_root=tmp_path, today="2026-09-08")
    handle.subscribe(lambda kind, state: kinds.append(kind))
    result = wait_for(handle)
    assert "progress" in kinds
    assert kinds[-1] == "terminal"
    assert result.state.complete


def test_manifest_and_log_never_contain_the_configured_secret(tmp_path):
    lines = ["[10:00:00] Phase 1 complete: 3 unique jobs fetched\n"]
    orchestrator, _, _ = make_orchestrator(lines=lines)
    handle = orchestrator.start(request(tmp_path), state_root=tmp_path, today="2026-09-08")
    result = wait_for(handle)
    manifest_text = result.manifest.read_text()
    log_text = (result.manifest.parent / "pipeline.log").read_text()
    for artifact in (manifest_text, log_text):
        assert "synthetic-secret-value" not in artifact
        assert "STAGE3_BOT_TOKEN" not in artifact


def test_retention_keeps_seven_newest_date_dirs_and_archives(tmp_path):
    for day in range(1, 10):
        run_dir = tmp_path / f"2026-09-{day:02d}" / f"2026090{day}T000000Z-aaaaaa"
        run_dir.mkdir(parents=True)
        (run_dir / "pipeline.log").write_text("log")
    deleted = retain_run_logs(tmp_path, keep_days=7, today="2026-09-09")
    assert sorted(d.name for d in deleted) == ["2026-09-01", "2026-09-02"]
    remaining = sorted(d.name for d in tmp_path.iterdir() if d.is_dir())
    assert "2026-09-01" not in remaining and "2026-09-02" not in remaining
    assert (tmp_path / "archive" / "2026-09-01.tar.gz").exists()


def test_retention_never_touches_today_or_non_date_dirs(tmp_path):
    today_dir = tmp_path / "2026-09-09" / "run"
    today_dir.mkdir(parents=True)
    (today_dir / "pipeline.log").write_text("active")
    (tmp_path / "archive").mkdir()
    (tmp_path / "not-a-date").mkdir()
    deleted = retain_run_logs(tmp_path, keep_days=0, today="2026-09-09")
    assert deleted == []
    assert (today_dir / "pipeline.log").exists()
    assert (tmp_path / "not-a-date").exists()


def _write_manifest(root, date, run_id, status):
    run_dir = root / date / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"run_id": run_id, "status": status})
    )
    return run_dir


def test_resume_selects_the_only_unfinished_run(tmp_path):
    _write_manifest(tmp_path, "2026-09-08", "20260908T090000Z-aaaaaa", "complete")
    unfinished = "20260908T110000Z-bbbbbb"
    _write_manifest(tmp_path, "2026-09-08", unfinished, "running")
    assert select_resumable(tmp_path, "2026-09-08") == unfinished


def test_resume_with_ambiguous_candidates_fails_with_ids(tmp_path):
    _write_manifest(tmp_path, "2026-09-08", "20260908T110000Z-bbbbbb", "running")
    _write_manifest(tmp_path, "2026-09-08", "20260908T120000Z-cccccc", "running")
    with pytest.raises(RunRefusedError) as excinfo:
        select_resumable(tmp_path, "2026-09-08")
    assert "bbbbbb" in str(excinfo.value) and "cccccc" in str(excinfo.value)


def test_resume_with_no_candidates_returns_none_for_fresh_start(tmp_path):
    _write_manifest(tmp_path, "2026-09-08", "20260908T090000Z-aaaaaa", "complete")
    assert select_resumable(tmp_path, "2026-09-08") is None


def test_resume_explicit_id_must_exist_and_be_resumable(tmp_path):
    with pytest.raises(Exception):
        select_resumable(tmp_path, "2026-09-08", explicit="20260908T090000Z-zzzzzz")
    _write_manifest(tmp_path, "2026-09-08", "20260908T090000Z-aaaaaa", "complete")
    with pytest.raises(RunRefusedError):
        select_resumable(tmp_path, "2026-09-08", explicit="20260908T090000Z-aaaaaa")


def test_start_resolves_resume_request_against_manifests(tmp_path):
    unfinished = "20260908T110000Z-bbbbbb"
    _write_manifest(tmp_path, "2026-09-08", unfinished, "running")
    orchestrator, _, _ = make_orchestrator(lines=["[10:00:00] Pipeline complete.\n"])
    handle = orchestrator.start(
        request(tmp_path, env={"RESUME": "1"}),
        state_root=tmp_path,
        today="2026-09-08",
    )
    result = wait_for(handle)
    assert result.run_id == unfinished
