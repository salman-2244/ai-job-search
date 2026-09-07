#!/usr/bin/env python3
"""Live terminal dashboard and control panel for document generation.

Renders a generation run as a self-refreshing table — one row per selected job,
with status, elapsed time, and compiled page counts — and puts three buttons
under it:

    S  stop after the job in flight finishes (keeps work already paid for)
    X  force-stop now, killing the job in flight
    R  resume: generate every selected job that is not complete on disk
    A  arm/disarm the 403 guard
    Q  quit watching (the batch keeps running)

The 403 guard is the reason this exists. The API relay reserves a per-request
hold before it streams a token, so once the balance runs dry EVERY remaining job
fails identically — 14 in a row on 2026-08-25. When a quota refusal appears and
the guard is armed, the batch is stopped immediately rather than burning through
the queue, and Resume picks up exactly what is missing after a top-up.

Watching is always safe: start it, kill it, run it in three terminals. Only the
buttons act, and each destructive one asks for a y/n confirmation first.

Truth comes from four places, in order of authority:
  1. Disk — a job is DONE when its .tex and .pdf both exist non-empty. The only
     signal that survives a crashed run.
  2. /tmp/jobsearch_batch_<date>.json — the runner's own per-job state.
  3. The runner's stdout — names the job currently in flight.
  4. /tmp/jobsearch_select_generate_<date>.log — per-job exit codes and the
     child's error text, which is where a quota refusal shows up.

Usage:
    python3 scripts/watch_generation.py                    # today, autodetect
    python3 scripts/watch_generation.py --once             # one frame, no loop
    python3 scripts/watch_generation.py --date 2026-08-25
    python3 scripts/watch_generation.py --no-auto-stop     # guard disarmed
"""

from __future__ import annotations

import argparse
import re
import select
import shutil
import subprocess
import sys
import termios
import time
import tty
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, filename: str):
    """Import a sibling script by path (scripts/ is not an importable package)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / filename)
    mod = importlib.util.module_from_spec(spec)
    # dataclasses resolves annotations through sys.modules[cls.__module__].
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# The batch runner owns the state-file schema, the quota rules, and what "stop"
# means. Importing it keeps one definition of each instead of a second copy here
# that could drift out of agreement with the process it controls.
GB = _load("gb", "generate_batch.py")


# ANSI. Kept literal rather than pulling in a dependency; disabled with --no-color.
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GREY = "\033[90m"
    REV = "\033[7m"  # keycaps; strippable so --no-color stays plain

    @classmethod
    def strip(cls) -> None:
        for name in dir(cls):
            if name.isupper():
                setattr(cls, name, "")


@dataclass
class Job:
    """One watched job. `status` is derived, never stored."""

    idx: int
    company: str
    title: str
    slug: str
    status: str = "pending"  # pending | running | done | failed | skipped
    error: str = ""
    cv_pages: Optional[int] = None
    cl_pages: Optional[int] = None
    started: Optional[float] = None
    finished: Optional[float] = None

    @property
    def elapsed(self) -> Optional[float]:
        if self.started is None:
            return None
        return (self.finished or time.time()) - self.started


def cv_paths(slug: str) -> tuple[Path, Path]:
    return (
        REPO / "cv" / slug / "Salman-Resume.tex",
        REPO / "cv" / slug / "Salman-Resume.pdf",
    )


def cl_paths(slug: str) -> tuple[Path, Path]:
    return (
        REPO / "cover_letters" / slug / "Salman-Cover-Letter.tex",
        REPO / "cover_letters" / slug / "Salman-Cover-Letter.pdf",
    )


def nonempty(p: Path) -> bool:
    try:
        return p.exists() and p.stat().st_size > 0
    except OSError:
        return False


def page_count(pdf: Path) -> Optional[int]:
    """Pages via pdfinfo. Returns None when the tool is absent or the PDF is bad.

    pdfinfo is optional (poppler); its absence degrades the Pages column to '?'
    rather than failing the dashboard.
    """
    if not nonempty(pdf) or not shutil.which("pdfinfo"):
        return None
    try:
        out = subprocess.run(
            ["pdfinfo", str(pdf)], capture_output=True, text=True, timeout=10
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    m = re.search(r"^Pages:\s+(\d+)", out, re.MULTILINE)
    return int(m.group(1)) if m else None


def load_jobs(rankset: Path, indices: Optional[list[int]]) -> list[Job]:
    """Build the watch list from the rankset, reusing telegram_select's parser.

    Imported by path rather than as a package: scripts/ is not importable, and
    duplicating the slug rule here would let the dashboard look for directories
    the generator never writes.
    """
    ts = _load("ts", "telegram_select.py")

    rows = ts.load_rankset(rankset)
    if indices is not None:
        wanted = set(indices)
        rows = [r for r in rows if r.idx in wanted]
    return [
        Job(idx=r.idx, company=r.company, title=r.title, slug=r.slug) for r in rows
    ]


DRIVER_START = re.compile(r"\bstarting\s+(.+?)\s+—\s+", re.UNICODE)
DRIVER_SKIP = re.compile(r"\bSKIP\b.*?:\s*(.+?)\s+—\s+", re.UNICODE)
LOG_HEADER = re.compile(r"^=== job (\d+) (\S+) exit=(-?\d+) ===$", re.MULTILINE)


def apply_generation_log(jobs: dict[int, Job], log: Path) -> None:
    """Mark failures from the per-job log.

    Only failures are read from here. A zero exit is NOT trusted as success:
    the log is appended by every run of the day, so a stale exit=0 from an
    earlier attempt would mask a job that has since been deleted or rewritten.
    Disk decides success; the log only explains why something did not finish.
    """
    if not log.exists():
        return
    text = log.read_text(encoding="utf-8", errors="replace")
    matches = list(LOG_HEADER.finditer(text))
    for n, m in enumerate(matches):
        idx, _slug, code = int(m.group(1)), m.group(2), int(m.group(3))
        job = jobs.get(idx)
        if job is None or code == 0 or job.status == "done":
            # A job that is complete on disk stays done: the log is appended by
            # every run of the day, so an old failed attempt must not overwrite
            # the finished work of a later one.
            continue
        body = text[m.end() : matches[n + 1].start() if n + 1 < len(matches) else len(text)]
        first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
        # Later entries overwrite earlier ones: the newest attempt is the truth.
        job.error = first[:200]
        job.status = "failed"


def apply_driver_log(jobs: dict[int, Job], driver_log: Optional[Path]) -> tuple[Optional[str], Optional[set[str]]]:
    """Return the company in flight and the set this run has attempted; mark skips.

    Matching is by company name because that is what the driver prints. Slug
    matching would be stricter but the driver's own lines are the only place the
    in-flight job is named at all. The attempted set is what lets the caller
    ignore failures logged by an EARLIER run of the same day: a job this run has
    not reached yet is queued, not failed.
    """
    if not driver_log or not driver_log.exists():
        return None, None
    text = driver_log.read_text(encoding="utf-8", errors="replace")
    by_company = {j.company: j for j in jobs.values()}

    for m in DRIVER_SKIP.finditer(text):
        job = by_company.get(m.group(1).strip())
        if job is not None:
            job.status = "skipped"

    starts = [s.strip() for s in DRIVER_START.findall(text)]
    return (starts[-1] if starts else None), set(starts)


def apply_state(jobs: dict[int, Job], state: Optional[dict]) -> None:
    """Overlay the runner's own per-job state.

    This is the precise signal: the runner writes it as each job changes, so it
    distinguishes "failed" from "never attempted" without guessing from an
    append-only log. Disk still wins for `done` — a document that exists is done
    no matter what any file says about it.
    """
    if not state:
        return
    for key, entry in (state.get("jobs") or {}).items():
        try:
            job = jobs.get(int(key))
        except (TypeError, ValueError):
            continue
        if job is None or job.status == "done":
            continue
        status = entry.get("status")
        if status not in BADGE:
            continue
        job.status = status
        job.error = (entry.get("error") or "") if status == "failed" else ""
        if entry.get("started"):
            job.started = entry["started"]
        if entry.get("finished"):
            job.finished = entry["finished"]


def refresh(jobs: list[Job], log: Path, driver_log: Optional[Path],
            state: Optional[dict] = None) -> None:
    """Recompute every job's status from scratch. Disk has the final word."""
    by_idx = {j.idx: j for j in jobs}

    for job in jobs:
        cv_tex, cv_pdf = cv_paths(job.slug)
        cl_tex, cl_pdf = cl_paths(job.slug)
        complete = all(nonempty(p) for p in (cv_tex, cv_pdf, cl_tex, cl_pdf))

        if complete:
            job.status = "done"
            job.error = ""
            if job.cv_pages is None:
                job.cv_pages = page_count(cv_pdf)
            if job.cl_pages is None:
                job.cl_pages = page_count(cl_pdf)
            if job.finished is None:
                job.finished = time.time()
        elif job.status in ("done", "skipped"):
            # Files vanished under us (deleted by hand); fall back to pending.
            job.status = "pending"

    apply_generation_log(by_idx, log)
    in_flight, attempted = apply_driver_log(by_idx, driver_log)

    for job in jobs:
        if attempted is not None and job.status == "failed" and job.company not in attempted:
            # Failure belongs to an earlier run of the same day; this run has not
            # reached the job yet.
            job.status, job.error = "pending", ""
        if job.company == in_flight and job.status not in ("done", "skipped"):
            job.status = "running"
            job.error = ""
            if job.started is None:
                job.started = time.time()

    # Last, so it overrides both heuristics for the indices it covers.
    apply_state(by_idx, state)


BADGE = {
    "done": (C.GREEN, "✓ DONE   "),
    "running": (C.CYAN, "◐ RUNNING"),
    "failed": (C.RED, "✗ FAILED "),
    "skipped": (C.BLUE, "⤼ SKIPPED"),
    "pending": (C.GREY, "· QUEUED "),
}


def bar(done: int, total: int, width: int = 34) -> str:
    if total <= 0:
        return ""
    filled = round(width * done / total)
    return f"{C.GREEN}{'█' * filled}{C.GREY}{'░' * (width - filled)}{C.RESET}"


def fmt_elapsed(secs: Optional[float]) -> str:
    if secs is None:
        return ""
    m, s = divmod(int(secs), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


def spinner(tick: int) -> str:
    return "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[tick % 10]


def dwidth(text: str) -> int:
    """Printed columns, not characters.

    The relay's 403 is Chinese, and every CJK glyph occupies two columns — using
    len() there pushed the box border off by the width of the message.
    """
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def clip(text: str, width: int) -> str:
    if dwidth(text) <= width:
        return text
    out, used = "", 0
    for ch in text:
        w = dwidth(ch)
        if used + w > width - 1:
            break
        out, used = out + ch, used + w
    return out + "…"


def pad_row(visible: str, colored: str, inner: int) -> str:
    """One boxed row. `visible` is the same text with the escape codes removed."""
    return (
        f"{C.MAGENTA}│{C.RESET}{colored}"
        f"{' ' * max(0, inner - dwidth(visible))}{C.MAGENTA}│{C.RESET}"
    )


def btn(key: str, label: str, on: bool = True) -> tuple[str, str]:
    """A keycap button. Returns (visible, colored) so rows still pad correctly."""
    visible = f" {key} {label} "
    if not on:
        return visible, f"{C.GREY} {key} {label} {C.RESET}"
    return visible, f"{C.BOLD}{C.REV} {key} {C.RESET}{C.BOLD}{label} {C.RESET}"


def render(app: "App", tick: int) -> str:
    jobs, day, log = app.jobs, app.day, app.log
    cols = shutil.get_terminal_size((100, 40)).columns
    width = max(72, min(cols, 118))
    inner = width - 2

    counts = {k: sum(1 for j in jobs if j.status == k) for k in BADGE}
    settled = counts["done"] + counts["skipped"]
    total = len(jobs)
    name_w = max(22, inner - 46)

    L: list[str] = []
    L.append(f"{C.BOLD}{C.MAGENTA}╭{'─' * inner}╮{C.RESET}")
    title = f" 📄  Application Generation — {day} "
    L.append(pad_row(title, f"{C.BOLD}{title}{C.RESET}", inner))
    L.append(f"{C.MAGENTA}├{'─' * inner}┤{C.RESET}")

    pct = f"{100 * settled // total if total else 0}%"
    prog = f" {bar(settled, total)}  {C.BOLD}{settled}/{total}{C.RESET} {C.DIM}({pct}){C.RESET}"
    # +len of the escape codes, so the visible text still pads to `inner`.
    pad = inner - (34 + len(f"  {settled}/{total} ({pct})") + 1)
    L.append(f"{C.MAGENTA}│{C.RESET}{prog}{' ' * max(0, pad)}{C.MAGENTA}│{C.RESET}")

    tally = (
        f" {C.GREEN}✓ {counts['done']} done{C.RESET}   "
        f"{C.CYAN}◐ {counts['running']} running{C.RESET}   "
        f"{C.GREY}· {counts['pending']} queued{C.RESET}   "
        f"{C.RED}✗ {counts['failed']} failed{C.RESET}   "
        f"{C.BLUE}⤼ {counts['skipped']} skipped{C.RESET}"
    )
    visible = f" ✓ {counts['done']} done   ◐ {counts['running']} running   · {counts['pending']} queued   ✗ {counts['failed']} failed   ⤼ {counts['skipped']} skipped"
    L.append(
        f"{C.MAGENTA}│{C.RESET}{tally}{' ' * max(0, inner - dwidth(visible))}"
        f"{C.MAGENTA}│{C.RESET}"
    )
    L.append(f"{C.MAGENTA}├{'─' * inner}┤{C.RESET}")

    hdr = f" {'STATUS':<10} {'COMPANY / ROLE':<{name_w}} {'CV':>4} {'CL':>4} {'TIME':>7}"
    L.append(
        f"{C.MAGENTA}│{C.RESET}{C.DIM}{hdr:<{inner}}{C.RESET}{C.MAGENTA}│{C.RESET}"
    )

    for job in jobs:
        colour, badge = BADGE[job.status]
        if job.status == "running":
            badge = f"{spinner(tick)} RUNNING"
        name = clip(f"{job.company} — {job.title}", name_w)
        cvp = "—" if job.cv_pages is None else str(job.cv_pages)
        clp = "—" if job.cl_pages is None else str(job.cl_pages)
        if job.status in ("pending", "failed"):
            cvp = clp = "—"
        el = fmt_elapsed(job.elapsed) if job.status in ("running", "done") else ""
        line = f" {badge:<10} {name:<{name_w}} {cvp:>4} {clp:>4} {el:>7}"
        L.append(
            f"{C.MAGENTA}│{C.RESET} {colour}{badge:<10}{C.RESET}"
            f" {name:<{name_w}} {cvp:>4} {clp:>4} {C.DIM}{el:>7}{C.RESET}"
            f"{' ' * max(0, inner - dwidth(line))}{C.MAGENTA}│{C.RESET}"
        )
        if job.status == "failed" and job.error:
            note = clip(f"   ↳ {job.error}", inner - 1)
            L.append(
                f"{C.MAGENTA}│{C.RESET}{C.RED}{note}{C.RESET}"
                f"{' ' * max(0, inner - dwidth(note))}{C.MAGENTA}│{C.RESET}"
            )

    L.append(f"{C.MAGENTA}├{'─' * inner}┤{C.RESET}")

    # --- alert band: the reason a batch is not moving, stated once, in words.
    if app.quota:
        have, need = app.quota.get("have"), app.quota.get("need")
        money = ""
        if have is not None and need is not None:
            money = f" — balance ${have:.2f}, this request needs ${need:.2f}"
        txt = f" ⚠  QUOTA BLOCK (403){money}. Top up, then press R to resume."
        L.append(pad_row(txt, f"{C.BOLD}{C.RED}{txt}{C.RESET}", inner))
    elif app.running:
        txt = f" ▶  batch running (pid {app.pid}) — sequential, one hold at a time"
        L.append(pad_row(txt, f"{C.CYAN}{txt}{C.RESET}", inner))
    else:
        stopped = app.stop_reason or "idle"
        txt = f" ■  no batch running ({stopped})"
        L.append(pad_row(txt, f"{C.GREY}{txt}{C.RESET}", inner))

    # --- controls
    if app.confirm:
        prompt = app.confirm_prompt()
        vis = f" {prompt}  [y] yes   [n] no"
        col = (
            f"{C.BOLD}{C.YELLOW} {prompt}{C.RESET}  {C.BOLD}{C.REV} y {C.RESET} yes"
            f"   {C.BOLD}{C.REV} n {C.RESET} no"
        )
        L.append(pad_row(vis, col, inner))
    else:
        pending = len(app.resumable())
        specs = [
            btn("S", "stop", app.running),
            btn("X", "force-stop", app.running),
            btn("R", f"resume {pending}", pending > 0 and not app.running),
            btn("A", f"auto-stop:{'ON' if app.armed else 'OFF'}"),
            btn("Q", "quit"),
        ]
        vis = " " + "  ".join(s[0] for s in specs)
        col = " " + "  ".join(s[1] for s in specs)
        L.append(pad_row(vis, col, inner))

    if app.flash and time.time() < app.flash_until:
        vis = f" ↳ {app.flash}"
        L.append(pad_row(vis, f"{C.YELLOW}{vis}{C.RESET}", inner))

    L.append(f"{C.MAGENTA}╰{'─' * inner}╯{C.RESET}")
    L.append(f"{C.DIM}  log: {log}   state: {GB.state_path(day)}{C.RESET}")
    return "\n".join(L)


def find_driver_log(day: str) -> Optional[Path]:
    """Best-effort guess at the driver's stdout capture.

    The driver is usually launched as a background task whose stdout lands in a
    per-task file, so there is no fixed path. Newest match wins; a wrong guess
    only costs the in-flight highlight, never a status.
    """
    globs = [
        Path("/tmp").glob("regen*.out"),
        Path("/tmp").glob(f"regen_selected_{day}*.log"),
        Path("/private/tmp").glob("claude-*/*/tasks/*.output"),
    ]
    cands = [p for g in globs for p in g if p.is_file()]
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_mtime)


class Keyboard:
    """Single-keypress reader that always restores the terminal.

    cbreak rather than full raw mode so Ctrl-C still interrupts: a dashboard that
    can trap the panic key is a dashboard you cannot get out of.
    """

    def __init__(self) -> None:
        self.fd = sys.stdin.fileno() if sys.stdin.isatty() else -1
        self.saved = None

    def __enter__(self) -> "Keyboard":
        if self.fd >= 0:
            try:
                self.saved = termios.tcgetattr(self.fd)
                tty.setcbreak(self.fd)
            except termios.error:
                self.fd, self.saved = -1, None
        return self

    def __exit__(self, *exc) -> None:
        if self.saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)

    def get(self, timeout: float) -> str:
        """One key, or "" when `timeout` elapses. Doubles as the frame clock."""
        if self.fd < 0:
            time.sleep(timeout)
            return ""
        try:
            ready, _, _ = select.select([sys.stdin], [], [], timeout)
        except (OSError, ValueError):
            time.sleep(timeout)
            return ""
        if not ready:
            return ""
        ch = sys.stdin.read(1)
        return ch or ""


class App:
    """Dashboard state plus the three actions its buttons perform."""

    def __init__(self, jobs: list[Job], day: str, log: Path, rankset: Path,
                 driver_log: Optional[Path], armed: bool) -> None:
        self.jobs = jobs
        self.day = day
        self.log = log
        self.rankset = rankset
        self.driver_log = driver_log
        self.armed = armed
        self.state: Optional[dict] = None
        self.running = False
        self.pid: Optional[int] = None
        self.stop_reason = ""
        self.quota: Optional[dict] = None
        self.confirm: Optional[tuple[str, int]] = None
        self.flash = ""
        self.flash_until = 0.0
        self.auto_stopped_run: Optional[float] = None
        self.last_launch = 0.0

    # ---------------------------------------------------------------- plumbing

    def say(self, msg: str, secs: float = 6.0) -> None:
        self.flash, self.flash_until = msg, time.time() + secs

    def poll(self) -> None:
        """Re-read every source of truth, then run the quota guard."""
        self.state = GB.read_state(GB.state_path(self.day))
        self.running = GB.batch_running(self.state)
        self.pid = (self.state or {}).get("pid")
        self.stop_reason = (self.state or {}).get("stop_reason") or ""

        # A runner's own .out is a better in-flight signal than an autodetected
        # file once a batch has been launched from here.
        own_out = GB.out_path(self.day)
        driver = own_out if self.state and own_out.exists() else self.driver_log
        refresh(self.jobs, self.log, driver, self.state)

        self.quota = self.detect_quota()
        self.guard()

    def detect_quota(self) -> Optional[dict]:
        """Quota block for the CURRENT run, from the state file or a fresh error.

        Only a run that is still going or has just stopped counts: an old quota
        failure from this morning must not leave the banner up forever, or it
        stops meaning anything.
        """
        if self.state and self.state.get("quota"):
            return self.state["quota"]
        if self.state:
            return None  # this run's state exists and reports no quota problem
        for job in self.jobs:
            if job.status == "failed" and GB.is_quota_error(job.error):
                return GB.parse_quota(job.error)
        return None

    def guard(self) -> None:
        """Stop a live batch the moment a quota refusal appears.

        The runner aborts on its own, but only if it is a version that knows how;
        this is the belt to that suspenders, and it is what the Auto-stop button
        arms. Fires once per run so a stuck process is not signalled every frame.
        """
        if not (self.armed and self.quota and self.running):
            return
        started = (self.state or {}).get("started_at")
        if self.auto_stopped_run == started:
            return
        self.auto_stopped_run = started
        msg = GB.request_stop(self.day, hard=True)
        self.say(f"auto-stop on 403: {msg}", 12.0)

    # ----------------------------------------------------------------- actions

    def resumable(self) -> list[int]:
        """Indices worth (re)generating: everything not complete on disk.

        Deliberately not "everything the log called failed" — a job that was
        never attempted, or whose files were deleted by hand, needs generating
        just as much, and one that quietly succeeded must not be paid for twice.
        """
        return [j.idx for j in self.jobs if j.status not in ("done", "skipped")]

    def confirm_prompt(self) -> str:
        action, n = self.confirm
        if action == "stop":
            return "Stop after the current job finishes?"
        if action == "force":
            return "Force-stop NOW and kill the job in flight?"
        return f"Generate {n} document set(s)? This spends API quota."

    def do_stop(self, hard: bool) -> None:
        self.say(GB.request_stop(self.day, hard=hard), 10.0)

    def do_resume(self) -> None:
        targets = self.resumable()
        if self.running:
            self.say("a batch is already running — stop it first")
            return
        if not targets:
            self.say("nothing to resume: every selected job is complete")
            return
        if time.time() - self.last_launch < 10:
            self.say("just launched — give it a moment")
            return
        ok, msg = GB.launch(self.day, targets, self.rankset)
        if ok:
            self.last_launch = time.time()
            # New run, new chance: let the guard fire again for it.
            self.auto_stopped_run = None
            for job in self.jobs:
                if job.idx in targets:
                    job.started = job.finished = None
        self.say(msg, 10.0)

    def key(self, ch: str) -> bool:
        """Handle one keypress. Returns False to quit."""
        if self.confirm:
            action, _ = self.confirm
            if ch in ("y", "Y"):
                self.confirm = None
                if action == "stop":
                    self.do_stop(hard=False)
                elif action == "force":
                    self.do_stop(hard=True)
                else:
                    self.do_resume()
            elif ch in ("n", "N", "\x1b", "q", "Q"):
                self.confirm = None
                self.say("cancelled", 3.0)
            return True

        if ch in ("q", "Q"):
            return False
        if ch in ("s", "S"):
            if self.running:
                self.confirm = ("stop", 0)
            else:
                self.say("no batch is running")
        elif ch in ("x", "X"):
            if self.running:
                self.confirm = ("force", 0)
            else:
                self.say("no batch is running")
        elif ch in ("r", "R"):
            targets = self.resumable()
            if not targets:
                self.say("nothing to resume: every selected job is complete")
            elif self.running:
                self.say("a batch is already running — stop it first")
            else:
                self.confirm = ("resume", len(targets))
        elif ch in ("a", "A"):
            self.armed = not self.armed
            self.say(f"auto-stop on 403 {'armed' if self.armed else 'DISARMED'}", 5.0)
        return True


def watch(app: App, interval: float, once: bool) -> int:
    tick = 0
    with Keyboard() as kb:
        try:
            while True:
                app.poll()
                frame = render(app, tick)
                if once:
                    print(frame)
                    break
                # Home + clear-to-end rather than a full clear: no flicker, and
                # the scrollback above the dashboard survives.
                sys.stdout.write("\033[H\033[J" + frame + "\n")
                sys.stdout.flush()
                tick += 1
                # Poll keys several times per frame so a button feels immediate
                # without spending a full refresh on every keystroke.
                deadline = time.time() + interval
                while True:
                    left = deadline - time.time()
                    if left <= 0:
                        break
                    ch = kb.get(min(0.15, left))
                    if ch and not app.key(ch):
                        sys.stdout.write("\n")
                        return 0
                    if ch:
                        break  # redraw immediately so the press is visible
        except KeyboardInterrupt:
            sys.stdout.write("\n")
            return 130

    settled = sum(1 for j in app.jobs if j.status in ("done", "skipped"))
    return 0 if settled == len(app.jobs) else 1


def main() -> int:
    today = date.today().isoformat()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=today, help="run date (default: today)")
    ap.add_argument("--rankset", type=Path, help="rankset JSON (default: /tmp/jobsearch_rankset_<date>.json)")
    ap.add_argument("--log", type=Path, help="per-job generation log")
    ap.add_argument("--driver-log", type=Path, help="runner stdout capture (default: autodetect)")
    ap.add_argument("--indices", help="comma-separated rankset indices to watch (default: all)")
    ap.add_argument("--interval", type=float, default=2.0, help="refresh seconds")
    ap.add_argument("--once", action="store_true", help="render one frame and exit")
    ap.add_argument("--no-color", action="store_true", help="plain output")
    ap.add_argument("--no-auto-stop", action="store_true", help="start with the 403 guard disarmed")
    args = ap.parse_args()

    if args.no_color or not sys.stdout.isatty():
        C.strip()
        for k, (_c, label) in list(BADGE.items()):
            BADGE[k] = ("", label)

    day = args.date
    rankset = args.rankset or GB.rankset_path(day)
    log = args.log or GB.log_path(day)
    if not rankset.exists():
        print(f"rankset not found: {rankset}", file=sys.stderr)
        return 2

    indices = None
    if args.indices:
        try:
            indices = [int(x) for x in args.indices.replace(" ", "").split(",") if x]
        except ValueError:
            print("--indices must be comma-separated integers", file=sys.stderr)
            return 2
    elif (state := GB.read_state(GB.state_path(day))) and state.get("indices"):
        # Default to the set the last batch was working on, so a bare invocation
        # shows the run in progress rather than all 25 ranked jobs.
        indices = list(state["indices"])

    jobs = load_jobs(rankset, indices)
    if not jobs:
        print("no jobs to watch", file=sys.stderr)
        return 2

    app = App(
        jobs=jobs, day=day, log=log, rankset=rankset,
        driver_log=args.driver_log or find_driver_log(day),
        armed=not args.no_auto_stop,
    )
    return watch(app, max(0.2, args.interval), args.once)


if __name__ == "__main__":
    raise SystemExit(main())

