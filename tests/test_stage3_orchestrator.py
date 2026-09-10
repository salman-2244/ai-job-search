import asyncio
import concurrent.futures
import json
import os
import signal
import stat
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from stage_3.config import Stage3Config
from stage_3.orchestrator import (
    MAX_JOB_COUNT,
    Orchestrator,
    ProcessIdentity,
    ProcessInspector,
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


def write_running_manifest(root, run_id="20260908T120000Z-aaaaaa", **overrides):
    run_dir = root / "2026-09-08" / run_id
    run_dir.mkdir(parents=True)
    manifest = {
        "run_id": run_id,
        "date": "2026-09-08",
        "status": "running",
        "phase": "2",
        "geo": "Germany",
        "job_count": 10,
        "rank_attempt": 1,
        "rank_max_attempts": 3,
        "error_class": None,
        "resumable": True,
        "started_at": "2026-09-08T12:00:00+00:00",
        "finished_at": None,
        "exit_code": None,
        "last_message": "Phase 2: Ranking jobs via Claude Code...",
        "terminal_notified": False,
        "process": {
            "pid": 42424,
            "pgid": 42424,
            "started_at": 987654321,
        },
    }
    manifest.update(overrides)
    atomic_json_write(run_dir / "manifest.json", manifest)
    (run_dir / "pipeline.log").write_text(
        "[12:00:00] Phase 1 complete: 37 unique jobs fetched\n"
        "[12:05:00] Phase 2: Ranking jobs via Claude Code...\n",
        encoding="utf-8",
    )
    return run_dir / "manifest.json"


class FakeProcessInspector:
    def __init__(self, identities=()):
        self.identities = {identity.pid: identity for identity in identities}
        self.signals = []

    def identity(self, pid):
        return self.identities.get(pid)

    def signal_group(self, pgid, sig):
        self.signals.append((pgid, sig))
        self.identities.pop(pgid, None)


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


def test_new_run_state_directories_are_owner_only(tmp_path):
    orchestrator, _, _ = make_orchestrator(
        lines=["[10:00:00] Pipeline complete.\n"],
    )

    handle = orchestrator.start(
        request(tmp_path), state_root=tmp_path, today="2026-09-08"
    )
    result = wait_for(handle)

    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "2026-09-08").stat().st_mode) == 0o700
    assert stat.S_IMODE(result.manifest.parent.stat().st_mode) == 0o700


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
    assert env["STAGE3_RESUME_ROOT"].endswith(f"/{env['RUN_ID']}/resume")
    assert "STAGE3_BOT_TOKEN" not in env
    assert "synthetic-secret-value" not in json.dumps(seen["env"])
    assert command_is_clean(seen["command"])


def command_is_clean(command):
    text = " ".join(command)
    return "synthetic-secret-value" not in text and text.startswith("bash")


def test_set_notify_installs_runtime_notification_hook(tmp_path):
    notifications = []
    orchestrator, _, _ = make_orchestrator(
        lines=["[10:00:00] Pipeline complete.\n"],
    )
    orchestrator.set_notify(notifications.append)

    handle = orchestrator.start(
        request(tmp_path), state_root=tmp_path, today="2026-09-08"
    )

    assert wait_for(handle).status == "complete"
    assert len(notifications) == 1
    assert "complete" in notifications[0].lower()


def test_set_notify_rejects_non_callable():
    orchestrator, _, _ = make_orchestrator()
    with pytest.raises(TypeError, match="callable"):
        orchestrator.set_notify(None)


def _completed_run(tmp_path, notify=None, run_id="notify-run"):
    orchestrator, _, _ = make_orchestrator(
        lines=["[10:00:00] Pipeline complete.\n"],
    )
    if notify is not None:
        orchestrator.set_notify(notify)
    handle = orchestrator.start(
        request(tmp_path, run_id=run_id),
        state_root=tmp_path,
        today="2026-09-08",
    )
    result = wait_for(handle)
    return orchestrator, handle, result


def test_terminal_notification_without_callback_remains_unconfirmed(tmp_path):
    _, _, result = _completed_run(tmp_path)

    assert json.loads(result.manifest.read_text())["terminal_notified"] is False


def test_terminal_notification_sync_success_is_confirmed_and_deduplicated(tmp_path):
    notifications = []
    orchestrator, handle, result = _completed_run(tmp_path, notifications.append)

    assert json.loads(result.manifest.read_text())["terminal_notified"] is True
    orchestrator._notify_text(handle, "duplicate terminal", terminal=True)
    assert len(notifications) == 1


def test_terminal_notification_sync_exception_remains_unconfirmed(tmp_path):
    def fail(_text):
        raise RuntimeError("synthetic delivery failure")

    _, _, result = _completed_run(tmp_path, fail)

    assert json.loads(result.manifest.read_text())["terminal_notified"] is False


@pytest.mark.parametrize("completion", ["exception", "cancelled"])
def test_terminal_notification_async_failure_remains_unconfirmed(tmp_path, completion):
    future = concurrent.futures.Future()
    if completion == "exception":
        future.set_exception(RuntimeError("synthetic async delivery failure"))
    else:
        future.cancel()

    _, _, result = _completed_run(tmp_path, lambda _text: future)

    assert json.loads(result.manifest.read_text())["terminal_notified"] is False


def test_terminal_notification_future_success_is_confirmed(tmp_path):
    future = concurrent.futures.Future()
    future.set_result(object())

    _, _, result = _completed_run(tmp_path, lambda _text: future)

    assert json.loads(result.manifest.read_text())["terminal_notified"] is True


def test_terminal_notification_coroutine_success_is_confirmed(tmp_path):
    async def deliver(_text):
        await asyncio.sleep(0)
        return object()

    _, _, result = _completed_run(tmp_path, deliver)

    assert json.loads(result.manifest.read_text())["terminal_notified"] is True


def test_notification_callback_actually_invoked_for_terminal_outcomes(tmp_path):
    cases = (
        ("complete", ["[10:00:00] Pipeline complete.\n"], 0),
        ("failed", ["[10:00:00] Phase 2 FAILED (exit 1)\n"], 1),
    )
    for index, (expected, lines, exit_code) in enumerate(cases):
        notifications = []
        orchestrator, _, _ = make_orchestrator(lines=lines, exit_code=exit_code)
        orchestrator.set_notify(notifications.append)
        result = wait_for(orchestrator.start(
            request(tmp_path, run_id=f"run-{index}"),
            state_root=tmp_path,
            today="2026-09-08",
        ))
        assert result.status == expected
        assert len(notifications) == 1
        assert f"terminal: {expected}" in notifications[0].lower()

    notifications = []
    orchestrator, _, _ = make_orchestrator(hold_open=True)
    orchestrator.set_notify(notifications.append)
    handle = orchestrator.start(
        request(tmp_path, run_id="run-cancel"),
        state_root=tmp_path,
        today="2026-09-08",
    )
    handle.cancel()
    assert wait_for(handle).status == "cancelled"
    assert any("is stopping" in text.lower() for text in notifications)
    terminal = [text for text in notifications if "terminal: cancelled" in text.lower()]
    assert len(terminal) == 1


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


def test_complete_marker_cannot_override_nonzero_process_exit(tmp_path):
    lines = [
        "[10:00:00] Phase 1 complete: 5 unique jobs fetched\n",
        "[10:01:00] Pipeline complete.\n",
    ]
    orchestrator, _, _ = make_orchestrator(lines=lines, exit_code=73)
    result = wait_for(
        orchestrator.start(request(tmp_path), state_root=tmp_path, today="2026-09-08")
    )
    manifest = json.loads(result.manifest.read_text())
    assert result.state.complete is True
    assert result.status == "failed"
    assert result.exit_code == 73
    assert manifest["status"] == "failed"
    assert manifest["exit_code"] == 73
    assert manifest["resumable"] is True


def test_resume_reuses_same_run_scoped_input_directory(tmp_path):
    first_seen = {}

    def first_runner(req, command, env, cwd):
        first_seen.update(env)
        resume_root = Path(env["STAGE3_RESUME_ROOT"])
        resume_root.mkdir(parents=True, exist_ok=True)
        fetched = resume_root / "fetched_jobs.json"
        fetched.write_text('{"meta":{"unique":1},"results":[{"id":"kept"}]}')
        fetched.chmod(0o600)
        return FakeProcess(lines=["[10:00:00] Phase 2 FAILED (exit 1)\n"], exit_code=1)

    first_orchestrator = Orchestrator(
        config=SYNTHETIC_CONFIG,
        runner=first_runner,
        clock=FakeClock(),
        sleeper=lambda _: None,
        lock_dir=None,
    )
    first = wait_for(
        first_orchestrator.start(
            request(tmp_path), state_root=tmp_path, today="2026-09-08"
        )
    )
    assert first.status == "failed"

    second_seen = {}

    def second_runner(req, command, env, cwd):
        second_seen.update(env)
        fetched = Path(env["STAGE3_RESUME_ROOT"]) / "fetched_jobs.json"
        assert fetched.is_file()
        assert json.loads(fetched.read_text())["results"][0]["id"] == "kept"
        assert fetched.stat().st_mode & 0o777 == 0o600
        return FakeProcess(lines=["[10:00:00] Pipeline complete.\n"])

    second_orchestrator = Orchestrator(
        config=SYNTHETIC_CONFIG,
        runner=second_runner,
        clock=FakeClock(),
        sleeper=lambda _: None,
        lock_dir=None,
    )
    resumed = wait_for(
        second_orchestrator.start(
            request(tmp_path, env={"RESUME": "1"}),
            state_root=tmp_path,
            today="2026-09-08",
        )
    )
    assert resumed.run_id == first.run_id
    assert second_seen["STAGE3_RESUME_ROOT"] == first_seen["STAGE3_RESUME_ROOT"]


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


def test_process_identity_is_persisted_before_monitoring(tmp_path):
    seen = {}
    proc = FakeProcess(hold_open=True)
    inspector = FakeProcessInspector([
        ProcessIdentity(pid=proc.pid, pgid=proc.pid, started_at=987654321)
    ])

    def runner(req, command, env, cwd):
        seen["env"] = env
        return proc

    orchestrator = Orchestrator(
        config=SYNTHETIC_CONFIG,
        runner=runner,
        process_inspector=inspector,
        clock=FakeClock(),
        sleeper=lambda _: None,
        lock_dir=None,
        poll_interval=0.0,
        max_runtime=0,
    )
    handle = orchestrator.start(
        request(tmp_path), state_root=tmp_path, today="2026-09-08"
    )
    try:
        process = json.loads(handle.manifest_path.read_text())["process"]
        assert process == {
            "pid": proc.pid,
            "pgid": proc.pid,
            "started_at": 987654321,
        }
        assert seen["env"]["STAGE3_PIPELINE_LOG"] == str(
            handle.manifest_path.parent / "pipeline.log"
        )
    finally:
        handle.cancel()
        wait_for(handle)


def test_reattach_requires_the_exact_same_process_identity(tmp_path):
    manifest = write_running_manifest(tmp_path)
    reused = FakeProcessInspector([
        ProcessIdentity(pid=42424, pgid=42424, started_at=111111111)
    ])
    orchestrator = Orchestrator(
        config=SYNTHETIC_CONFIG,
        process_inspector=reused,
        lock_dir=None,
    )

    assert orchestrator.restore_active(tmp_path) is None
    data = json.loads(manifest.read_text())
    assert data["status"] == "interrupted"
    assert data["error_class"] == "process_missing"
    assert data["finished_at"] is not None
    assert reused.signals == []


def test_restore_active_replays_log_and_refuses_overlap(tmp_path):
    identity = ProcessIdentity(pid=42424, pgid=42424, started_at=987654321)
    inspector = FakeProcessInspector([identity])
    write_running_manifest(tmp_path)
    orchestrator = Orchestrator(
        config=SYNTHETIC_CONFIG,
        process_inspector=inspector,
        lock_dir=None,
        poll_interval=0.01,
        max_runtime=0,
    )

    handle = orchestrator.restore_active(tmp_path)

    assert handle is not None and handle.is_running
    assert handle.run_id == "20260908T120000Z-aaaaaa"
    assert handle.state.current_phase == "2"
    assert "Ranking" in handle.state.message
    with pytest.raises(RunRefusedError, match="already being supervised"):
        orchestrator.start(
            request(tmp_path), state_root=tmp_path, today="2026-09-08"
        )


def test_reattached_cancel_signals_only_the_validated_process_group(tmp_path):
    identity = ProcessIdentity(pid=42424, pgid=42424, started_at=987654321)
    inspector = FakeProcessInspector([identity])
    write_running_manifest(tmp_path)
    orchestrator = Orchestrator(
        config=SYNTHETIC_CONFIG,
        process_inspector=inspector,
        lock_dir=None,
        poll_interval=0.01,
        kill_after=0.01,
        max_runtime=0,
    )
    handle = orchestrator.restore_active(tmp_path)

    handle.cancel()
    result = handle.wait(timeout=1)

    assert result is not None and result.status == "cancelled"
    assert inspector.signals[0] == (42424, signal.SIGTERM)
    assert all(pgid == 42424 for pgid, _ in inspector.signals)


def test_reattached_run_keeps_original_max_runtime_budget(tmp_path):
    identity = ProcessIdentity(pid=42424, pgid=42424, started_at=987654321)
    inspector = FakeProcessInspector([identity])
    write_running_manifest(
        tmp_path,
        started_at="2000-01-01T00:00:00+00:00",
    )
    orchestrator = Orchestrator(
        config=SYNTHETIC_CONFIG,
        process_inspector=inspector,
        lock_dir=None,
        poll_interval=0.01,
        kill_after=0.01,
        max_runtime=60,
    )

    handle = orchestrator.restore_active(tmp_path)
    result = handle.wait(timeout=1)

    assert result is not None and result.status == "timeout"
    assert inspector.signals[0] == (42424, signal.SIGTERM)


def test_restore_active_refuses_multiple_live_process_groups(tmp_path):
    first = ProcessIdentity(pid=42424, pgid=42424, started_at=987654321)
    second = ProcessIdentity(pid=52525, pgid=52525, started_at=987654322)
    write_running_manifest(tmp_path)
    write_running_manifest(
        tmp_path,
        run_id="20260908T130000Z-bbbbbb",
        process=second.as_manifest(),
        started_at="2026-09-08T13:00:00+00:00",
    )
    inspector = FakeProcessInspector([first, second])
    orchestrator = Orchestrator(
        config=SYNTHETIC_CONFIG,
        process_inspector=inspector,
        lock_dir=None,
        max_runtime=0,
    )

    with pytest.raises(RunRefusedError, match="multiple live pipeline processes"):
        orchestrator.restore_active(tmp_path)

    assert inspector.signals == []
    assert json.loads(
        (tmp_path / "2026-09-08" / "20260908T120000Z-aaaaaa" /
         "manifest.json").read_text()
    )["status"] == "running"
    assert json.loads(
        (tmp_path / "2026-09-08" / "20260908T130000Z-bbbbbb" /
         "manifest.json").read_text()
    )["status"] == "running"


def test_process_inspector_identity_is_stable_for_real_process():
    proc = subprocess.Popen(
        ["sleep", "30"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    inspector = ProcessInspector()
    try:
        first = inspector.identity(proc.pid)
        second = inspector.identity(proc.pid)
        assert first is not None
        assert first == second
        assert first.pid == proc.pid
        assert first.pgid == proc.pid
        assert first.started_at >= 0
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)


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
        max_runtime=0,
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


def test_waiting_notice_does_not_suppress_terminal_notification(tmp_path):
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
        stall_after=2,
        warn_every=120,
        max_runtime=5,
        kill_after=1,
        notify=notifications.append,
    )

    result = wait_for(orchestrator.start(
        request(tmp_path), state_root=tmp_path, today="2026-09-08"
    ))

    assert any("still working" in text.lower() for text in notifications)
    terminal = [text for text in notifications if "terminal: timeout" in text.lower()]
    assert len(terminal) == 1
    assert json.loads(result.manifest.read_text())["terminal_notified"] is True


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


def _write_manifest(root, date, run_id, status, resumable=None):
    run_dir = root / date / run_id
    run_dir.mkdir(parents=True)
    if resumable is None:
        resumable = status in {"running", "failed", "timeout", "interrupted"}
    (run_dir / "manifest.json").write_text(
        json.dumps({"run_id": run_id, "status": status, "resumable": resumable})
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


@pytest.mark.parametrize("status", ["complete", "cancelled"])
def test_resume_never_selects_nonresumable_terminal_runs(tmp_path, status):
    run_id = "20260908T090000Z-aaaaaa"
    _write_manifest(tmp_path, "2026-09-08", run_id, status)

    assert select_resumable(tmp_path, "2026-09-08") is None
    with pytest.raises(RunRefusedError, match="not resumable"):
        select_resumable(tmp_path, "2026-09-08", explicit=run_id)


@pytest.mark.parametrize("status", ["failed", "timeout", "interrupted"])
def test_resume_selects_resumable_terminal_runs(tmp_path, status):
    run_id = "20260908T090000Z-aaaaaa"
    _write_manifest(tmp_path, "2026-09-08", run_id, status)

    assert select_resumable(tmp_path, "2026-09-08") == run_id
    assert select_resumable(tmp_path, "2026-09-08", explicit=run_id) == run_id


def test_stale_running_manifest_is_terminal_and_not_resumable(tmp_path):
    manifest = write_running_manifest(tmp_path)
    orchestrator = Orchestrator(
        config=SYNTHETIC_CONFIG,
        process_inspector=FakeProcessInspector(),
        lock_dir=None,
    )

    assert orchestrator.restore_active(tmp_path) is None
    data = json.loads(manifest.read_text())
    assert data["status"] == "interrupted"
    assert data["resumable"] is True
    assert select_resumable(tmp_path, "2026-09-08") == data["run_id"]


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
