"""Run orchestration: identity, manifests, subprocess lifecycle, retention.

`scripts/run_daily.sh` stays the pipeline worker — this module supervises exactly
one execution of it at a time. Everything time-consuming (log tailing, progress
projection, cancellation, the runtime ceiling) happens on a monitor thread, so
`Orchestrator.start` returns a `RunHandle` immediately and the Telegram layer never
blocks the event loop.

Testability is structural, not mocked-after-the-fact: the runner, clock and sleeper
are injectable, so the whole lifecycle — including cancellation escalation and the
waiting-warning cadence — is exercised with a fake process and a fake clock and no
subprocess at all.

Security posture:
  - the child is launched with `Stage3Config.child_env`, which never contains
    `STAGE3_BOT_TOKEN`; nothing secret is ever placed in argv (`ps` is world-readable);
  - the manifest is a fixed whitelist of non-secret fields — no job text, no paths
    into credentials, no token material. It is written with tmp-file + fsync +
    `os.replace`, so a crash never leaves a half-written JSON behind;
  - only the process group this module created is signalled, TERM first and KILL
    only after a bounded grace period — the pipeline spawns children, so killing
    just the shell would orphan them.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import json
import logging
import os
import queue
import re
import secrets
import shutil
import signal
import subprocess
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import DEFAULT_RUN_STATE_ROOT, Stage3Config
from .diagnostics import callable_reference, safe_preview
from .progress import ProgressTracker, RunState

#: `run_daily.sh:15` creates this with an atomic `mkdir`; its existence is the
#: pipeline's own overlap signal and we refuse before even spawning.
DEFAULT_LOCK_DIR = Path("/tmp/jobsearch_daily_pipeline.lock")

#: Hard ceiling on the job-count selection. The bot bounds its own inputs; this is
#: the orchestrator's independent bound so a bad caller cannot request absurdity.
MAX_JOB_COUNT = 50

_DATE_DIR_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_ATTEMPT_RE = re.compile(r"attempt\s+(\d+)", re.IGNORECASE)
_PROCESS_STAT_RE = re.compile(r"^(\d+) \((.*)\) ([A-Z]) (.*)$")
_TERMINAL_STATUSES = frozenset({
    "complete", "failed", "cancelled", "timeout", "interrupted",
})

_EOF = object()  # sentinel: the child's stdout closed

_ERROR_CLASSES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("auth", ("authentication", "login failed", "sign in", "401", "credential")),
    ("permission", ("permission denied", "unauthorized", "forbidden")),
    ("quota", ("quota", "rate limit", "429")),
    ("credit", ("credit", "billing", "payment")),
    ("timeout", ("timeout", "timed out")),
)


class Stage3OrchestratorError(RuntimeError):
    """Base class for orchestrator errors raised to the caller."""


class RunRefusedError(Stage3OrchestratorError):
    """A run was refused before it started, or an ambiguous resume was rejected."""


class RunNotFoundError(Stage3OrchestratorError):
    """An explicitly named run does not exist under the expected date."""


def validate_run_id(value: str) -> str:
    """Accept only ids that are safe to use as a single path component."""
    if not isinstance(value, str) or not _RUN_ID_RE.fullmatch(value) or ".." in value:
        raise ValueError(
            f"unsafe run id {value!r}: use 1-64 characters of A-Za-z0-9._- , "
            "without a leading dot or '..'"
        )
    return value


def new_run_id(now: datetime | None = None, random_suffix: str | None = None) -> str:
    """UTC timestamp plus a random suffix: sortable, unique, path-safe."""
    now = now or datetime.now(timezone.utc)
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{stamp}-{random_suffix or secrets.token_hex(3)}"
    return validate_run_id(run_id)


def today_str() -> str:
    """The pipeline's local date, matching `run_daily.sh`'s log folder naming."""
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json_write(path: Path, data: dict) -> None:
    """Replace `path` with `data` atomically: tmp file, fsync, rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent,
            prefix=f"{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            tmp = Path(handle.name)
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass


def retain_run_logs(root: Path, keep_days: int = 7, today: str | None = None) -> list[Path]:
    """Keep the seven newest `YYYY-MM-DD` directories, archive+delete older ones.

    Today's directory is never touched (an active run may be writing into it) and
    anything that is not a date directory is invisible to this sweep. Every step is
    best-effort: cleanup is diagnostic and must never fail a completed pipeline.
    """
    root = Path(root)
    today = today or today_str()
    keep_days = max(0, keep_days)
    deleted: list[Path] = []
    try:
        date_dirs = sorted(
            (entry for entry in root.iterdir()
             if entry.is_dir() and _DATE_DIR_RE.fullmatch(entry.name)),
            key=lambda entry: entry.name,
        )
    except OSError:
        return deleted
    stale = [d for d in date_dirs[: len(date_dirs) - keep_days] if d.name != today]
    for day_dir in stale:
        archive = root / "archive" / f"{day_dir.name}.tar.gz"
        try:
            archive.parent.mkdir(parents=True, exist_ok=True)
            if not archive.exists():
                with tarfile.open(archive, "w:gz") as tar:
                    tar.add(day_dir, arcname=day_dir.name)
        except OSError:
            pass
        try:
            shutil.rmtree(day_dir)
            deleted.append(day_dir)
        except OSError:
            pass
    return deleted


def select_resumable(root: Path, date: str, explicit: str | None = None) -> str | None:
    """Pick the run a `RESUME=1` request should continue, deterministically.

    An explicit id must exist and retain durable resume inputs. Without one, exactly
    one resumable run for the date may be selected; several candidates is an error
    that names them, and none at all returns None (start fresh — the legacy path).
    A corrupt manifest is skipped: resume is best-effort and a fresh run is safe.
    """
    date_dir = Path(root) / date
    if explicit is not None:
        validate_run_id(explicit)
        manifest = date_dir / explicit / "manifest.json"
        if not manifest.is_file():
            raise RunNotFoundError(f"no manifest for run {explicit} under {date_dir}")
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RunRefusedError(f"manifest for run {explicit} is unreadable") from exc
        if not data.get("resumable", False):
            raise RunRefusedError(
                f"run {explicit} is not resumable ({data.get('status')}); "
                "durable inputs are unavailable"
            )
        return explicit
    resumable: list[str] = []
    try:
        candidates = sorted(entry.name for entry in date_dir.iterdir() if entry.is_dir())
    except OSError:
        return None
    for name in candidates:
        manifest = date_dir / name / "manifest.json"
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("resumable", False):
            resumable.append(name)
    if len(resumable) > 1:
        raise RunRefusedError(
            "multiple resumable runs for "
            f"{date}: {', '.join(resumable)}. Pass an explicit run_id instead of "
            "letting the resume pick one for you."
        )
    return resumable[0] if resumable else None


def _classify_error(state: RunState) -> str:
    """Coarse terminal class from the pipeline's own failure wording."""
    texts = [ps.message for ps in state.phases.values() if ps.status == "failed"]
    texts.append(state.message)
    blob = " ".join(texts).lower()
    for name, words in _ERROR_CLASSES:
        if any(word in blob for word in words):
            return name
    return "transient" if blob.strip() else "unknown"


def _rank_attempt(state: RunState) -> int:
    match = _ATTEMPT_RE.search(state.message)
    return int(match.group(1)) if match else 1


@dataclass(frozen=True)
class RunRequest:
    """What the caller asks for. `env` carries passthrough vars (e.g. RESUME=1)."""

    geo: str | None
    job_count: int = 10
    run_id: str | None = None
    env: dict[str, str] | None = None


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: str
    exit_code: int | None
    state: RunState
    manifest: Path
    error_class: str | None = None


@dataclass(frozen=True)
class ProcessIdentity:
    """Stable identity for one Unix process and its dedicated process group."""

    pid: int
    pgid: int
    started_at: int

    @classmethod
    def from_manifest(cls, value) -> "ProcessIdentity | None":
        if not isinstance(value, dict):
            return None
        try:
            identity = cls(
                pid=int(value["pid"]),
                pgid=int(value["pgid"]),
                started_at=int(value["started_at"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
        if identity.pid <= 1 or identity.pgid != identity.pid \
                or identity.started_at < 0:
            return None
        return identity

    def as_manifest(self) -> dict[str, int]:
        return {
            "pid": self.pid,
            "pgid": self.pgid,
            "started_at": self.started_at,
        }


class ProcessInspector:
    """Read and signal process groups while defending against PID reuse."""

    def identity(self, pid: int) -> ProcessIdentity | None:
        try:
            raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        except OSError:
            return self._ps_identity(pid)
        match = _PROCESS_STAT_RE.match(raw.strip())
        if match is None:
            return None
        fields = match.group(4).split()
        try:
            pgid = int(fields[1])
            started_at = int(fields[18])
        except (IndexError, ValueError):
            return None
        return ProcessIdentity(pid=pid, pgid=pgid, started_at=started_at)

    def _ps_identity(self, pid: int) -> ProcessIdentity | None:
        """macOS fallback: absolute launch time plus pid/pgid identifies reuse."""
        try:
            proc = subprocess.run(
                ["ps", "-o", "pid=,pgid=,lstart=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        line = proc.stdout.strip()
        match = re.match(r"^(\d+)\s+(\d+)\s+(.+)$", line)
        if proc.returncode != 0 or match is None:
            return None
        try:
            started = datetime.strptime(
                match.group(3), "%a %b %d %H:%M:%S %Y"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        return ProcessIdentity(
            pid=int(match.group(1)),
            pgid=int(match.group(2)),
            started_at=int(started.timestamp()),
        )

    def signal_group(self, pgid: int, sig: int) -> None:
        os.killpg(pgid, sig)


class ReattachedProcess:
    """A process-group view reconstructed from a validated durable identity."""

    stdout = ()

    def __init__(self, identity: ProcessIdentity, inspector: ProcessInspector):
        self.identity = identity
        self.pid = identity.pid
        self._inspector = inspector

    def _current(self) -> ProcessIdentity | None:
        current = self._inspector.identity(self.pid)
        return current if current == self.identity else None

    def poll(self):
        return None if self._current() is not None else 0

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                return None
            time.sleep(0.05)
        return 0

    def _signal(self, sig: int) -> None:
        if self._current() is None:
            return
        try:
            self._inspector.signal_group(self.identity.pgid, sig)
        except ProcessLookupError:
            pass

    def terminate(self):
        self._signal(signal.SIGTERM)

    def kill(self):
        self._signal(signal.SIGKILL)


@dataclass
class RunHandle:
    """Live handle for one supervised run; safe to touch from any thread."""

    run_id: str
    manifest_path: Path
    _queue: queue.Queue = field(default_factory=queue.Queue, repr=False)
    _cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _done: threading.Event = field(default_factory=threading.Event, repr=False)
    _callbacks: list = field(default_factory=list, repr=False)
    _cb_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _manifest: dict = field(default_factory=dict, repr=False)
    _state: RunState | None = field(default=None, repr=False)
    _result: RunResult | None = field(default=None, repr=False)

    def subscribe(self, callback) -> None:
        with self._cb_lock:
            self._callbacks.append(callback)

    def cancel(self) -> None:
        """Ask the monitor to stop the run. Never raises, never blocks the caller."""
        self._cancel_event.set()

    def wait(self, timeout: float | None = None) -> RunResult | None:
        self._done.wait(timeout)
        return self._result

    def result(self) -> RunResult | None:
        return self._result

    @property
    def state(self) -> RunState | None:
        return self._state

    @property
    def is_running(self) -> bool:
        return not self._done.is_set()


class PopenProcess:
    """A real pipeline process, wrapped so the monitor never imports subprocess."""

    def __init__(self, command: list[str], env: dict, cwd: str):
        # start_new_session: the script spawns children; signals must reach the
        # whole group or a TERM leaves workers running with no supervisor.
        self._proc = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=env, cwd=cwd, start_new_session=True,
        )
        self.pid = self._proc.pid
        self.stdout = ()

    def poll(self):
        return self._proc.poll()

    def wait(self, timeout=None):
        try:
            return self._proc.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def _signal_group(self, sig: int) -> None:
        try:
            os.killpg(self._proc.pid, sig)
        except ProcessLookupError:
            pass

    def terminate(self):
        self._signal_group(signal.SIGTERM)

    def kill(self):
        self._signal_group(signal.SIGKILL)


class PipelineRunner:
    """The production runner: launch `scripts/run_daily.sh` from the repo."""

    def __call__(self, request: RunRequest, command: list[str], env: dict, cwd: str):
        return PopenProcess(command, env=env, cwd=cwd)


class Orchestrator:
    """Supervises single runs of the Stage 1/2 pipeline with live progress."""

    def __init__(
        self,
        config: Stage3Config | None,
        runner=None,
        clock=time.monotonic,
        sleeper=time.sleep,
        lock_dir: Path | str | None = DEFAULT_LOCK_DIR,
        poll_interval: float = 0.5,
        stall_after: float = 120.0,
        warn_every: float = 120.0,
        kill_after: float = 10.0,
        max_runtime: float | None = None,
        keep_days: int = 7,
        notify=None,
        process_inspector=None,
    ):
        self._config = config
        self._runner = runner or PipelineRunner()
        self._process_inspector = process_inspector or ProcessInspector()
        self._clock = clock
        self._sleeper = sleeper
        self._lock_dir = Path(lock_dir) if lock_dir is not None else None
        self._poll_interval = max(0.0, poll_interval)
        # With poll_interval=0 (tests) the queue poll never blocks, so the loop
        # still needs a tick for the injected clock to move.
        self._sleep_tick = self._poll_interval if self._poll_interval > 0 else 1.0
        self._stall_after = stall_after
        self._warn_every = warn_every
        self._kill_after = kill_after
        self._notify = notify
        self._keep_days = keep_days
        if max_runtime is None and config is not None:
            max_runtime = float(config.max_runtime) if config.max_runtime else None
        self._max_runtime = max_runtime or None  # 0 disables the ceiling
        self._start_lock = threading.Lock()
        self._active: RunHandle | None = None
        self._logger = logging.getLogger("stage_3.orchestrator")

    # -- public API ---------------------------------------------------------

    def set_notify(self, notify) -> None:
        """Install the operator notification hook once the bot loop exists."""
        if not callable(notify):
            raise TypeError("notify must be callable")
        self._notify = notify
        self._logger.info(
            "notification callback installed callback=%s",
            callable_reference(notify),
        )

    def restore_active(self, state_root: Path | None = None) -> RunHandle | None:
        """Reattach to the newest positively identified running process group.

        A PID is never trusted by itself: the persisted process-group id and OS
        start marker must still match. A stale ``running`` manifest is finalized
        as interrupted so it cannot block launches or masquerade as live state.
        """
        state_root = Path(state_root or self._default_state_root())
        candidates = self._running_manifests(state_root)
        live: list[tuple[Path, dict, ProcessIdentity]] = []
        for manifest_path, data in candidates:
            identity = ProcessIdentity.from_manifest(data.get("process"))
            current = (
                self._process_inspector.identity(identity.pid)
                if identity is not None else None
            )
            if identity is None or current != identity:
                self._mark_interrupted(manifest_path, data)
                continue
            live.append((manifest_path, data, identity))
        if len(live) > 1:
            run_ids = ", ".join(str(data["run_id"]) for _, data, _ in live)
            raise RunRefusedError(
                "multiple live pipeline processes were recorded "
                f"({run_ids}); refusing to choose or signal either one"
            )
        if not live:
            return None
        manifest_path, data, identity = live[0]
        handle = RunHandle(str(data["run_id"]), manifest_path)
        handle._manifest = data
        handle._state = self._state_from_log(manifest_path.parent / "pipeline.log")
        proc = ReattachedProcess(identity, self._process_inspector)
        request = RunRequest(
            geo=data.get("geo"),
            job_count=int(data.get("job_count") or 10),
            run_id=handle.run_id,
        )
        with self._start_lock:
            if self._active is not None and self._active.is_running:
                return self._active
            self._active = handle
        threading.Thread(
            target=self._drain,
            args=(proc, handle._queue, manifest_path.parent / "pipeline.log"),
            name=f"stage3-reattach-drain-{handle.run_id}",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._supervise,
            args=(handle, request, manifest_path.parent, proc,
                  state_root, str(data.get("date") or manifest_path.parent.parent.name),
                  self._elapsed_runtime(data.get("started_at"))),
            name=f"stage3-reattach-{handle.run_id}",
            daemon=True,
        ).start()
        return handle

    def start(self, request: RunRequest, *, state_root: Path | None = None,
              today: str | None = None) -> RunHandle:
        """Launch one supervised run and return immediately.

        Raises RunRefusedError when a run is already active, the pipeline lock
        exists, the job count is out of bounds, or the process cannot be spawned.
        """
        if (not isinstance(request.job_count, int) or isinstance(request.job_count, bool)
                or not 1 <= request.job_count <= MAX_JOB_COUNT):
            raise RunRefusedError(
                f"job_count must be an integer between 1 and {MAX_JOB_COUNT}, "
                f"got {request.job_count!r}"
            )
        state_root = Path(state_root or self._default_state_root())
        date = today or today_str()
        run_id = self._resolve_run_id(request, state_root, date)
        self._refuse_if_locked()
        with self._start_lock:
            if self._active is not None and self._active.is_running:
                raise RunRefusedError(
                    f"A run is already being supervised ({self._active.run_id}). "
                    "Concurrent runs are refused, not queued — /cancel it first."
                )
            run_dir = state_root / date / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            handle = RunHandle(run_id, run_dir / "manifest.json")
            handle._manifest = self._initial_manifest(request, run_id, date)
            try:
                atomic_json_write(handle.manifest_path, handle._manifest)
            except OSError:
                pass  # observability only; the run itself must not hinge on it
            try:
                proc = self._runner(
                    request,
                    self._command(),
                    self._child_env(
                        request,
                        run_id,
                        run_dir / "pipeline.log",
                        run_dir / "resume",
                    ),
                    str(self._repo()),
                )
            except OSError as exc:
                handle._manifest.update(status="failed", error_class="spawn",
                                        finished_at=now_iso(), resumable=False)
                try:
                    atomic_json_write(handle.manifest_path, handle._manifest)
                except OSError:
                    pass
                raise RunRefusedError(f"could not launch the pipeline: {exc}") from exc
            identity = self._process_inspector.identity(proc.pid)
            if identity is None:
                identity = ProcessIdentity(
                    pid=proc.pid, pgid=proc.pid, started_at=int(time.time())
                )
            if identity.pgid != proc.pid:
                proc.terminate()
                raise RunRefusedError(
                    "could not verify the pipeline's dedicated process group"
                )
            handle._manifest["process"] = identity.as_manifest()
            try:
                atomic_json_write(handle.manifest_path, handle._manifest)
            except OSError:
                pass
            self._active = handle
        threading.Thread(
            target=self._drain, args=(proc, handle._queue, run_dir / "pipeline.log"),
            name=f"stage3-drain-{run_id}", daemon=True,
        ).start()
        if self._poll_interval <= 0:
            # Test mode: give the drain thread a real moment to queue the scripted
            # lines before the monitor starts moving the injected clock, so line
            # timestamps do not depend on thread-scheduling luck.
            time.sleep(0.05)
        threading.Thread(
            target=self._supervise, args=(handle, request, run_dir, proc, state_root, date),
            name=f"stage3-monitor-{run_id}", daemon=True,
        ).start()
        return handle

    # -- setup helpers ------------------------------------------------------

    def _default_state_root(self) -> Path:
        if self._config is not None:
            return Path(self._config.run_state_root)
        return Path(DEFAULT_RUN_STATE_ROOT)

    def _repo(self) -> Path:
        return Path(self._config.repo) if self._config is not None else Path.cwd()

    def _command(self) -> list[str]:
        return ["bash", str(self._repo() / "scripts" / "run_daily.sh")]

    def _resolve_run_id(self, request: RunRequest, state_root: Path, date: str) -> str:
        if request.run_id is not None:
            return validate_run_id(request.run_id)
        if (request.env or {}).get("RESUME") == "1":
            selected = select_resumable(state_root, date)
            if selected is not None:
                return selected
        for _ in range(5):
            candidate = new_run_id()
            if not (state_root / date / candidate).exists():
                return candidate
        raise RunRefusedError("could not allocate a fresh run id; run state is crowded")

    def _refuse_if_locked(self) -> None:
        if self._lock_dir is not None and self._lock_dir.exists():
            raise RunRefusedError(
                f"Pipeline already running (lock dir {self._lock_dir} exists). "
                "Concurrent runs are refused — the script holds that lock; check "
                "/status or wait for it to finish."
            )

    def _child_env(self, request: RunRequest, run_id: str,
                   pipeline_log: Path | None = None,
                   resume_root: Path | None = None) -> dict:
        extra = dict(request.env or {})
        extra.setdefault("OUTPUT_ROOT", str(self._repo()))
        extra["RUN_ID"] = run_id  # authoritative: never inherited from the caller
        extra["JOB_COUNT"] = str(request.job_count)
        if pipeline_log is not None:
            extra["STAGE3_PIPELINE_LOG"] = str(pipeline_log)
        if resume_root is not None:
            extra["STAGE3_RESUME_ROOT"] = str(resume_root)
        if request.geo:
            extra["GEO_FILTER"] = request.geo
        if self._config is not None:
            return self._config.child_env(extra)
        env = dict(os.environ)
        env.pop("STAGE3_BOT_TOKEN", None)
        env.update(extra)
        return env

    def _initial_manifest(self, request: RunRequest, run_id: str, date: str) -> dict:
        attempts_raw = (request.env or {}).get("RANK_ATTEMPTS", "3")
        attempts = int(attempts_raw) if attempts_raw.isdigit() else 3
        return {
            "run_id": run_id,
            "date": date,
            "status": "running",
            "phase": None,
            "geo": request.geo,
            "job_count": request.job_count,
            "rank_attempt": 1,
            "rank_max_attempts": attempts,
            "error_class": None,
            "resumable": True,
            "started_at": now_iso(),
            "finished_at": None,
            "exit_code": None,
            "last_message": "",
            "terminal_notified": False,
            "process": None,
        }

    def _running_manifests(self, root: Path) -> list[tuple[Path, dict]]:
        found: list[tuple[Path, dict]] = []
        try:
            paths = sorted(root.glob("????-??-??/*/manifest.json"), reverse=True)
        except OSError:
            return found
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("status") == "running" and data.get("run_id"):
                found.append((path, data))
        return found

    def _mark_interrupted(self, path: Path, data: dict) -> None:
        data.update(
            status="interrupted",
            error_class="process_missing",
            finished_at=now_iso(),
            last_message="The recorded pipeline process is no longer running.",
            resumable=True,
        )
        try:
            atomic_json_write(path, data)
        except OSError:
            pass

    def _state_from_log(self, log_path: Path) -> RunState:
        tracker = ProgressTracker()
        try:
            with log_path.open(encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    tracker.feed(line, now=self._clock())
        except OSError:
            pass
        return tracker.state

    def _elapsed_runtime(self, started_at) -> float:
        """Return wall-clock runtime already consumed before reattachment."""
        if not isinstance(started_at, str):
            return 0.0
        try:
            started = datetime.fromisoformat(started_at)
        except ValueError:
            return 0.0
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return max(
            0.0,
            (datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds(),
        )

    # -- threads ------------------------------------------------------------

    def _drain(self, proc, lines: queue.Queue, log_path: Path) -> None:
        """Project output from a fake stream or the production durable log."""
        if proc.stdout != ():
            try:
                with log_path.open("a", encoding="utf-8") as log_file:
                    for line in proc.stdout:
                        if not line.endswith("\n"):
                            line += "\n"
                        log_file.write(line)
                        log_file.flush()
                        lines.put(line)
            except Exception:
                pass
            finally:
                lines.put(_EOF)
            return
        position = 0
        if isinstance(proc, ReattachedProcess):
            try:
                position = log_path.stat().st_size
            except OSError:
                pass
        try:
            while proc.poll() is None:
                position = self._read_log_lines(log_path, position, lines)
                time.sleep(self._poll_interval or 0.01)
            self._read_log_lines(log_path, position, lines)
        except Exception:
            pass
        finally:
            lines.put(_EOF)

    def _read_log_lines(self, path: Path, position: int, lines: queue.Queue) -> int:
        try:
            with path.open(encoding="utf-8", errors="replace") as stream:
                stream.seek(position)
                for line in stream:
                    lines.put(line if line.endswith("\n") else line + "\n")
                return stream.tell()
        except OSError:
            return position

    def _supervise(self, handle, request, run_dir, proc, state_root, date,
                   elapsed_runtime=0.0) -> None:
        """Monitor one run end-to-end. All monitor-thread logic lives here."""
        tracker = ProgressTracker(handle._state)
        state = tracker.state
        handle._state = state
        started = self._clock() - max(0.0, elapsed_runtime)
        next_warning = started + self._stall_after
        sigterm_at: float | None = None
        enforced: str | None = None
        try:
            while True:
                try:
                    line = handle._queue.get(timeout=self._poll_interval)
                except queue.Empty:
                    line = None
                now = self._clock()
                if line is not None and line is not _EOF:
                    tracker.feed(line, now=now)
                    self._update_manifest(handle, state, "running")
                    self._emit(handle, "progress", state)
                    next_warning = now + self._stall_after
                else:
                    # Idle iteration: the only place the loop yields. In test mode
                    # (poll_interval == 0) this is what moves the injected clock,
                    # so a fed line must never advance it.
                    self._sleeper(self._sleep_tick)
                if (enforced is None and not state.complete
                        and state.stalled(now, self._stall_after)
                        and now >= next_warning):
                    quiet = int(now - state.last_activity)
                    self._emit(handle, "waiting", state)
                    self._notify_text(
                        handle,
                        f"💬 Still working — no log update for {quiet}s "
                        f"(phase {state.current_phase}).",
                    )
                    next_warning = self._clock() + self._warn_every
                if enforced is None:
                    if handle._cancel_event.is_set():
                        enforced = "cancelled"
                    elif self._max_runtime is not None \
                            and now - started >= self._max_runtime:
                        enforced = "timeout"
                    if enforced is not None:
                        proc.terminate()
                        sigterm_at = now
                        self._notify_text(
                            handle,
                            f"⏹ Run {handle.run_id} is stopping — SIGTERM sent, "
                            f"escalation in {int(self._kill_after)}s.",
                        )
                elif sigterm_at is not None and now - sigterm_at >= self._kill_after:
                    if proc.poll() is None:
                        proc.kill()
                    sigterm_at = None
                if line is _EOF and proc.poll() is not None:
                    break
            self._finalize(handle, request, state, run_dir, proc, state_root, date, enforced)
        except Exception as exc:  # noqa: BLE001 — the monitor must never die silently
            handle._manifest.update(status="failed", error_class="internal",
                                    last_message=str(exc)[:200])
            self._finalize(handle, request, state, run_dir, proc, state_root, date, None)

    def _finalize(self, handle, request, state, run_dir, proc, state_root, date,
                  enforced) -> None:
        exit_code = proc.wait()
        if enforced == "timeout":
            status, error_class = "timeout", "max_runtime"
        elif enforced == "cancelled" or handle._cancel_event.is_set():
            status, error_class = "cancelled", None
        elif exit_code == 0 and state.complete:
            status, error_class = "complete", None
        else:
            status, error_class = "failed", _classify_error(state)
        self._update_manifest(handle, state, status, exit_code=exit_code,
                              error_class=error_class, finished=True)
        self._emit(handle, "terminal", state)
        terminal_text = None
        if status == "complete":
            terminal_text = (
                f"✅ Run {handle.run_id} terminal: complete — documents saved to disk."
            )
        elif status == "failed":
            terminal_text = (
                f"❌ Run {handle.run_id} terminal: failed (exit {exit_code}, "
                f"class {error_class})."
            )
        elif status == "cancelled":
            terminal_text = f"⏹ Run {handle.run_id} terminal: cancelled."
        elif status == "timeout":
            terminal_text = f"⏱ Run {handle.run_id} terminal: timeout."
        if terminal_text is not None:
            self._notify_text(handle, terminal_text, terminal=True)
        try:
            retain_run_logs(state_root, keep_days=self._keep_days, today=date)
        except Exception:
            pass
        with self._start_lock:
            if self._active is handle:
                self._active = None
        handle._result = RunResult(
            run_id=handle.run_id, status=status, exit_code=exit_code, state=state,
            manifest=handle.manifest_path, error_class=error_class,
        )
        handle._done.set()

    # -- manifest + notifications ------------------------------------------

    def _update_manifest(self, handle, state, status, exit_code=None,
                         error_class=None, finished=False) -> None:
        data = handle._manifest
        data.update(
            status=status,
            phase=state.current_phase,
            last_message=state.message[:200],
            rank_attempt=_rank_attempt(state),
            exit_code=exit_code,
            error_class=error_class,
        )
        if finished:
            data["finished_at"] = now_iso()
            data["resumable"] = status in {"failed", "timeout", "interrupted"}
        try:
            atomic_json_write(handle.manifest_path, data)
        except OSError:
            pass

    def _notify_text(self, handle, text: str, *, terminal: bool = False) -> None:
        """Send a best-effort notice; confirm terminal delivery before deduping."""
        if terminal and handle._manifest.get("terminal_notified"):
            return
        if self._notify is None:
            self._logger.error(
                "notification callback unavailable terminal=%s preview=%s",
                terminal, safe_preview(text),
            )
            return
        self._logger.info(
            "invoking notification callback callback=%s terminal=%s preview=%s",
            callable_reference(self._notify), terminal, safe_preview(text),
        )
        try:
            outcome = self._notify(text)
            if isinstance(outcome, concurrent.futures.Future):
                outcome.result()
            elif inspect.isawaitable(outcome):
                asyncio.run(outcome)
        except (Exception, concurrent.futures.CancelledError) as exc:
            self._logger.exception(
                "notification delivery failed exception_type=%s preview=%s",
                type(exc).__name__, safe_preview(text),
            )
            return
        if terminal:
            handle._manifest["terminal_notified"] = True
            try:
                atomic_json_write(handle.manifest_path, handle._manifest)
            except OSError:
                pass

    def _emit(self, handle, kind: str, state: RunState) -> None:
        with handle._cb_lock:
            callbacks = list(handle._callbacks)
        for callback in callbacks:
            try:
                callback(kind, state)
            except Exception:
                pass  # a broken subscriber must never kill the pipeline
