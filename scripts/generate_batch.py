#!/usr/bin/env python3
"""Sequential batch runner for job-application document generation.

Drafts and compiles one job at a time, writing a machine-readable state file so
a dashboard (scripts/watch_generation.py) can watch it, stop it, and resume it.

Three rules are baked in, each paid for by a failed run:

  * SEQUENTIAL. The API relay in front of this account reserves a per-request
    hold (~$0.65-$0.92 for a drafting prompt) BEFORE it streams a token. Three
    parallel jobs mean three holds competing for one balance, so a thin account
    loses all three instead of completing one.
  * SKIP WHAT IS DONE. Regenerating a finished application spends a fresh hold
    to overwrite a good document.
  * ABORT ON A QUOTA REFUSAL. It will hit every remaining job identically, so
    grinding through the rest only buries the real cause in a wall of failures.

Usage:
    python3 scripts/generate_batch.py --indices 13,16,18,19,20,21
    python3 scripts/generate_batch.py --all-missing        # everything unbuilt
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
from functools import partial
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
STATE_VERSION = 1


# ---------------------------------------------------------------- shared paths


def state_path(day: str) -> Path:
    return Path(f"/tmp/jobsearch_batch_{day}.json")


def lock_path(day: str) -> Path:
    return Path(f"/tmp/jobsearch_batch_{day}.lock")


def out_path(day: str) -> Path:
    return Path(f"/tmp/jobsearch_batch_{day}.out")


def log_path(day: str) -> Path:
    """Per-job transcript log. Shared with the Telegram path on purpose."""
    return Path(f"/tmp/jobsearch_select_generate_{day}.log")


def rankset_path(day: str) -> Path:
    return Path(f"/tmp/jobsearch_rankset_{day}.json")


# ------------------------------------------------------------ quota detection


# The relay answers in either English or Chinese, and the wording differs
# between "you are already overdrawn" and "this request's hold is too big".
# Matching only "quota" missed 6 jobs on 2026-08-25, so match all four shapes.
QUOTA_MARKERS = (
    "pre-consume quota failed",
    "insufficient",
    "quota",
    "预扣费额度",  # pre-consume hold
    "额度不足",  # insufficient balance
    "剩余额度",  # remaining balance
)
HAVE_RE = re.compile(r"(?:user quota|用户剩余额度|剩余额度)\s*[:：]\s*[＄$]?\s*(-?[\d.]+)")
NEED_RE = re.compile(r"(?:need quota|需要预扣费额度|需要额度)\s*[:：]\s*[＄$]?\s*(-?[\d.]+)")


def is_quota_error(text: str) -> bool:
    """True when `text` looks like the relay refusing on balance, not a code bug.

    Deliberately broad: a false positive costs one paused batch that a keypress
    resumes, while a false negative costs a full run of identical failures.
    """
    if not text:
        return False
    low = text.lower()
    if "403" in low and any(m in low for m in QUOTA_MARKERS):
        return True
    return bool(HAVE_RE.search(text) or NEED_RE.search(text))


def parse_quota(text: str) -> dict:
    """Pull the balance/required numbers out of a refusal, when it names them."""
    have, need = HAVE_RE.search(text or ""), NEED_RE.search(text or "")
    got = {}
    if have:
        got["have"] = float(have.group(1))
    if need:
        got["need"] = float(need.group(1))
    return got


# ------------------------------------------------------------------ state file


def read_state(path: Path) -> Optional[dict]:
    """Load a state file, tolerating a torn or foreign file.

    The runner writes atomically, but a state file left by an older version (or
    truncated by a full disk) must not crash a watcher.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_state(path: Path, data: dict) -> None:
    """Atomically replace the state file so a reader never sees a partial write."""
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)


def proc_alive(pid: Optional[int]) -> bool:
    """True when `pid` is a live runner of THIS script.

    The command-line check is what makes a kill safe: PIDs are reused, and a
    stale state file must never let a dashboard signal an unrelated process that
    happens to have inherited the number.
    """
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False  # owned by another user: not ours, never signal it
    try:
        cmd = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return False
    return "generate_batch.py" in cmd


def batch_running(state: Optional[dict]) -> bool:
    if not state or state.get("finished_at"):
        return False
    return proc_alive(state.get("pid"))


# --------------------------------------------------------------- control (API)
# Used by the dashboard's buttons. Kept here so the meaning of "stop" and the
# meaning of the state file it edits live in one file.


def request_stop(day: str, hard: bool = False) -> str:
    """Ask a running batch to stop. Returns a human-readable outcome.

    Graceful (SIGTERM) lets the job in flight finish, so no money already spent
    on a draft is thrown away. Hard (SIGUSR1) also kills the child `claude`,
    which can leave a half-written .tex — harmless, because a document only
    counts as done when its .tex AND .pdf both exist, so resume rebuilds it.
    """
    state = read_state(state_path(day))
    if not batch_running(state):
        return "no batch is running"
    pid = int(state["pid"])
    try:
        os.kill(pid, signal.SIGUSR1 if hard else signal.SIGTERM)
    except OSError as exc:
        return f"could not signal pid {pid}: {exc}"
    return f"{'hard' if hard else 'graceful'} stop sent to pid {pid}"


def launch(day: str, indices: list[int], rankset: Optional[Path] = None) -> tuple[bool, str]:
    """Start a detached batch for `indices`. Returns (started, message).

    Detached (start_new_session) for two reasons: the batch must outlive the
    dashboard that started it, and being its own session leader means a stop can
    target the whole process group without any risk of reaching the terminal.
    """
    state = read_state(state_path(day))
    if batch_running(state):
        return False, f"a batch is already running (pid {state.get('pid')})"
    if not indices:
        return False, "nothing to generate"

    rs = rankset or rankset_path(day)
    if not rs.exists():
        return False, f"rankset not found: {rs}"

    out = out_path(day)
    cmd = [
        sys.executable, str(REPO / "scripts" / "generate_batch.py"),
        "--date", day,
        "--rankset", str(rs),
        "--indices", ",".join(str(i) for i in indices),
    ]
    try:
        fh = out.open("a", encoding="utf-8")
    except OSError as exc:
        return False, f"cannot open {out}: {exc}"
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(REPO), stdin=subprocess.DEVNULL,
            stdout=fh, stderr=subprocess.STDOUT, start_new_session=True,
        )
    except OSError as exc:
        fh.close()
        return False, f"launch failed: {exc}"
    fh.close()
    return True, f"launched pid {proc.pid} for {len(indices)} job(s)"


# ------------------------------------------------------------------- lock file


def acquire_lock(day: str) -> Optional[Path]:
    """Take the single-batch lock, clearing it if its owner is gone.

    Two runners on one balance is the exact failure this whole script exists to
    prevent, and two dashboards can press Resume in the same second — so the
    lock is O_EXCL rather than a read-then-write check.
    """
    path = lock_path(day)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            try:
                owner = int(path.read_text(encoding="utf-8").strip() or 0)
            except (OSError, ValueError):
                owner = 0
            if proc_alive(owner):
                return None
            # Stale: the previous runner died without releasing it.
            path.unlink(missing_ok=True)
            continue
        except OSError:
            return None
        with os.fdopen(fd, "w") as fh:
            fh.write(str(os.getpid()))
        return path
    return None


def release_lock(path: Optional[Path]) -> None:
    if path is None:
        return
    try:
        if path.read_text(encoding="utf-8").strip() == str(os.getpid()):
            path.unlink(missing_ok=True)
    except OSError:
        pass


# ------------------------------------------------------------- disk inspection


# The run id for this batch, set by main() from --run-id. Module-level rather
# than threaded through every signature so `is_complete` keeps the same call
# signature the sandbox runner and the dashboard already read.
_RUN_ID: Optional[str] = None
_ts_is_complete = None  # bound to telegram_select.is_complete by run() when run-scoped
_ts_legacy_is_complete = None  # bound to the read-only legacy gate by run()


def artifacts(slug: str) -> list[Path]:
    return [
        REPO / "cv" / slug / "Salman-Resume.tex",
        REPO / "cv" / slug / "Salman-Resume.pdf",
        REPO / "cover_letters" / slug / "Salman-Cover-Letter.tex",
        REPO / "cover_letters" / slug / "Salman-Cover-Letter.pdf",
    ]


def is_complete(slug: str) -> bool:
    """True when both documents exist as non-empty .tex AND compiled .pdf.

    All four are required: a .tex with no .pdf is a draft that never compiled,
    and sending a recruiter a missing PDF is worse than regenerating one.

    With `--run-id` this delegates to the shared run-scoped layout, while also
    honoring the shared read-only legacy gate. A complete legacy application is
    never regenerated or migrated; a partial one does not block generation.
    The legacy check stays local when no run id is active so a batch keeps
    working without telegram_select.py loaded.
    """
    if _RUN_ID is not None:
        return _ts_legacy_is_complete(slug) or _ts_is_complete(slug)
    try:
        return all(p.exists() and p.stat().st_size > 0 for p in artifacts(slug))
    except OSError:
        return False


# ----------------------------------------------------------------- the run loop


def load_ts():
    """Import scripts/telegram_select.py by path.

    Reused rather than reimplemented so a batch produces byte-identical output to
    the Telegram path — same prompt, same slug rule, same tracker columns. The
    sys.modules assignment is required: dataclasses resolves JobRow's annotations
    through sys.modules[cls.__module__], which is None for a path import.
    """
    spec = importlib.util.spec_from_file_location("ts", REPO / "scripts" / "telegram_select.py")
    ts = importlib.util.module_from_spec(spec)
    sys.modules["ts"] = ts
    spec.loader.exec_module(ts)
    return ts


class Stop:
    """Stop request flags, set from signal handlers."""

    graceful = False
    hard = False
    reason = ""


def install_signals(loop: asyncio.AbstractEventLoop) -> None:
    """SIGTERM/SIGINT = finish this job then stop. SIGUSR1 = also kill the child.

    A plain SIGTERM would abandon a draft the account has already paid for, so
    the polite stop is the default and the violent one is a separate signal.
    """

    def graceful() -> None:
        Stop.graceful, Stop.reason = True, "stopped by request"

    def hard() -> None:
        Stop.graceful, Stop.hard = True, True
        Stop.reason = "hard-stopped by request"
        # `claude` is a direct child of this process; killing it makes the
        # awaited generate_one() return a failure promptly instead of hanging.
        subprocess.run(["pkill", "-TERM", "-P", str(os.getpid())], check=False)

    for sig, handler in (
        (signal.SIGTERM, graceful), (signal.SIGINT, graceful), (signal.SIGUSR1, hard)
    ):
        try:
            loop.add_signal_handler(sig, handler)
        except (NotImplementedError, RuntimeError):
            pass


async def run(args: argparse.Namespace) -> int:
    global _ts_is_complete, _ts_legacy_is_complete
    ts = load_ts()
    if _RUN_ID is not None:
        # A complete legacy application remains authoritative and read-only; if
        # none exists, resume checks the current run's own output tree.
        _ts_legacy_is_complete = partial(ts.legacy_is_complete, repo=REPO)
        _ts_is_complete = partial(
            ts.is_complete, day=args.date, run_id=_RUN_ID, output_root=args.output_root
        )
    day, spath = args.date, state_path(args.date)
    log, rankset = log_path(day), Path(args.rankset)

    rows = {r.idx: r for r in ts.load_rankset(rankset)}
    if args.all_missing:
        wanted = [i for i in sorted(rows) if not is_complete(rows[i].slug)]
    else:
        wanted = [i for i in args.indices if i in rows]
        unknown = [i for i in args.indices if i not in rows]
        if unknown:
            print(f"[batch] rankset has no indices {unknown}", file=sys.stderr, flush=True)

    todo = [i for i in wanted if not is_complete(rows[i].slug)]
    skipped = [i for i in wanted if i not in todo]

    state = {
        "version": STATE_VERSION,
        "date": day,
        "pid": os.getpid(),
        "pgid": os.getpgid(0),
        "rankset": str(rankset),
        "log": str(log),
        "indices": wanted,
        "started_at": time.time(),
        "finished_at": None,
        "stop_reason": None,
        "quota": None,
        "jobs": {
            str(i): {
                "slug": rows[i].slug,
                "company": rows[i].company,
                "status": "skipped" if i in skipped else "pending",
                "error": None,
                "started": None,
                "finished": None,
            }
            for i in wanted
        },
    }
    write_state(spath, state)

    for i in skipped:
        print(f"[batch] SKIP already complete: {rows[i].company} — {rows[i].title}", flush=True)
    print(f"[batch] {len(todo)} to generate, {len(skipped)} already done; sequential", flush=True)

    install_signals(asyncio.get_running_loop())
    sem = asyncio.Semaphore(1)  # sequential regardless of the module default
    done: list[tuple] = []

    try:
        for n, idx in enumerate(todo, 1):
            if Stop.graceful:
                state["stop_reason"] = Stop.reason
                print(f"[batch] {Stop.reason}; {len(todo) - n + 1} job(s) not attempted", flush=True)
                break

            row = rows[idx]
            entry = state["jobs"][str(idx)]
            entry.update(status="running", started=time.time(), error=None)
            write_state(spath, state)
            print(f"[batch] {n}/{len(todo)} starting {row.company} — {row.title}", flush=True)

            try:
                outcome = await ts.generate_one(
                    row, day, sem, log, run_id=_RUN_ID, output_root=args.output_root
                )
            except Exception as exc:  # a crashed child must not abort the batch
                outcome = {"row": row, "ok": False, "error": f"{type(exc).__name__}: {exc}"}

            entry["finished"] = time.time()
            if outcome.get("ok"):
                entry.update(status="done", error=None)
                done.append((row, outcome.get("result") or {}))
                write_state(spath, state)
                print(f"[batch] {n}/{len(todo)} OK   {row.company}", flush=True)
                continue

            err = str(outcome.get("error") or "unknown failure")
            # The relay prints its 403 to stdout, not stderr, so the error string
            # can be a bare "claude exited 1". Read the job's own log entry back
            # to find out whether this was really a quota refusal.
            detail = tail_log_entry(log, idx)
            quota_hit = is_quota_error(err) or is_quota_error(detail)
            if quota_hit and detail:
                err = first_line(detail) or err

            if Stop.graceful and not quota_hit:
                # We killed it. That is an interruption, not a defect: leaving it
                # PENDING is what makes Resume pick it up instead of parading a
                # red row the operator has to reason about.
                entry.update(status="pending", error=None, started=None, finished=None)
                state["stop_reason"] = Stop.reason
                write_state(spath, state)
                print(f"[batch] {n}/{len(todo)} interrupted {row.company} — will resume", flush=True)
                break

            entry.update(status="failed", error=err[:400])
            write_state(spath, state)
            print(f"[batch] {n}/{len(todo)} FAIL {row.company}: {err[:200]}", flush=True)

            if quota_hit:
                state["stop_reason"] = "quota"
                state["quota"] = {**parse_quota(detail or err), "at": time.time()}
                left = len(todo) - n
                print(
                    f"[batch] ABORT: quota refusal. {left} job(s) not attempted. "
                    "Top up, then resume — finished work is skipped automatically.",
                    flush=True,
                )
                break
    finally:
        # Tracker rows for whatever finished, even on a stop or a crash: an
        # application on disk with no tracker row is invisible to every later step.
        try:
            ts.append_tracker(done, day, None)
        except Exception as exc:  # noqa: BLE001 - never lose the documents over a CSV
            print(f"[batch] WARN tracker append failed: {exc}", file=sys.stderr, flush=True)
        state["finished_at"] = time.time()
        state["stop_reason"] = state.get("stop_reason") or (
            "complete" if len(done) == len(todo) else "incomplete"
        )
        write_state(spath, state)

    print(f"\n[batch] === generated {len(done)}/{len(todo)} attempted ===", flush=True)
    for row, res in done:
        print(f"  OK   {row.company} — {row.title}")
    for idx in todo:
        e = state["jobs"][str(idx)]
        if e["status"] == "failed":
            print(f"  FAIL {e['company']}: {e['error']}")
        elif e["status"] == "pending":
            print(f"  ...  {e['company']} — never attempted")
    print(f"\n[batch] tracker rows appended: {len(done)}  (status=drafted)")
    print(f"[batch] per-job detail: {log}")
    print(f"[batch] state: {spath}")
    return 0 if len(done) == len(todo) else 2


def first_line(text: str) -> str:
    return next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")


def tail_log_entry(log: Path, idx: int) -> str:
    """Return the body of the LAST log entry for job `idx`.

    The log is appended by every run of the day, so only the final entry
    describes the attempt that just happened.
    """
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    marks = list(re.finditer(rf"^=== job {idx} \S+ exit=(-?\d+) ===$", text, re.MULTILINE))
    if not marks:
        return ""
    last = marks[-1]
    nxt = re.search(r"^=== job \d+ ", text[last.end():], re.MULTILINE)
    body = text[last.end():last.end() + nxt.start()] if nxt else text[last.end():]
    return body.strip()[:2000]


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate application documents sequentially.")
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d"), help="run date (default: today)")
    ap.add_argument("--rankset", help="rankset JSON (default: /tmp/jobsearch_rankset_<date>.json)")
    ap.add_argument("--indices", default="", help="comma-separated rankset indices")
    ap.add_argument("--all-missing", action="store_true", help="every rankset job not yet complete")
    ap.add_argument(
        "--run-id",
        default=None,
        help="Stage 3 run id: documents land in cv/<date>/<run_id>/<slug>/ and the "
        "completeness check follows the same shared layout",
    )
    ap.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="root the document tree here instead of the repo (with --run-id)",
    )
    args = ap.parse_args()

    args.rankset = args.rankset or str(rankset_path(args.date))
    try:
        args.indices = [int(x) for x in args.indices.replace(" ", "").split(",") if x]
    except ValueError:
        print("--indices must be comma-separated integers", file=sys.stderr)
        return 2
    if not args.indices and not args.all_missing:
        print("nothing to do: pass --indices or --all-missing", file=sys.stderr)
        return 2
    if not Path(args.rankset).exists():
        print(f"rankset not found: {args.rankset}", file=sys.stderr)
        return 2

    global _RUN_ID
    _RUN_ID = args.run_id
    if _RUN_ID is not None and args.output_root is None:
        args.output_root = REPO  # explicit beats implicit

    lock = acquire_lock(args.date)
    if lock is None:
        print(
            "another batch is already running for this date — refusing to start a "
            "second one (two runners would compete for one balance)",
            file=sys.stderr,
        )
        return 3
    try:
        return asyncio.run(run(args))
    finally:
        release_lock(lock)


if __name__ == "__main__":
    raise SystemExit(main())


