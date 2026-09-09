#!/usr/bin/env python3
"""Interactive Telegram job selection, then per-job document generation.

A separate entry point from scripts/run_daily.sh. It consumes a rankset that an
earlier run already computed and adds the interactive layer on top: one message
per job with a toggle button, a Submit button, and CV + cover-letter generation
for the jobs that were actually selected.

Why a second bot token rather than the Claude Code bot's:
    Telegram hands `getUpdates` to exactly one consumer per token. The Claude
    Code bot (~/claude-code-telegram) polls continuously under launchd with
    allowed_updates=ALL_TYPES, so a second poller on that token would 409 and
    the two would race to steal each other's updates — costing phone access to
    Claude. This script therefore runs its own bot, configured in a separate
    env file, and never touches the other bot's token. See docs/TELEGRAM.md.

Config: ~/.jobsearch-selector.env (mode 600), overridable with
JOBSEARCH_SELECTOR_ENV. Keys:
    SELECTOR_BOT_TOKEN=123456:ABC...      the dedicated bot, from @BotFather
    SELECTOR_CHAT_ID=123456789            where to send the list
    SELECTOR_ALLOWED_USER_IDS=123456789   who may press the buttons

Usage:
    # render the messages to stdout, no network, no token needed
    python3 scripts/telegram_select.py --rankset /tmp/rs.json --dry-run

    # the real thing
    python3 scripts/telegram_select.py --rankset /tmp/jobsearch_rankset_2026-08-23.json

    # selection only, never spend money on generation
    python3 scripts/telegram_select.py --rankset ... --no-generate
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import html
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parent.parent
DEFAULT_ENV = Path.home() / ".jobsearch-selector.env"
DRAFT_PROMPT = REPO / "prompts" / "selected_job_draft.md"
TRACKER = REPO / "job_search_tracker.csv"

# callback_data is capped at 64 bytes by Telegram, so the payload is an index
# into the rendered list rather than a dedup_key (which can be arbitrarily long).
CB_TOGGLE = "t"
CB_SUBMIT = "submit"
CB_ALL = "all"
CB_NONE = "none"

# Concurrency for generation. Each job is an independent `claude -p` process.
# Sequential (1) on purpose: the API relay in front of this account reserves a
# per-request hold (~$0.65 for a drafting prompt) BEFORE streaming any tokens,
# and refuses the request outright if the balance cannot cover it. Running 3 at
# once meant three holds competing for one balance, so a thin account lost all
# three instead of completing one — the 2026-08-25 run failed 14/14 that way.
# One at a time also means every job that finishes is money already converted
# into a document. Raise this only against an endpoint with no pre-consume hold.
MAX_PARALLEL_JOBS = 1


# --------------------------------------------------------------------------
# Run-scoped output paths (Stage 3)
#
# The legacy layout — cv/<slug>/, cover_letters/<slug>/ — collides across runs:
# two runs that both draft "Acme_Role" overwrite each other's documents, and a
# re-draft silently replaces a finished application. The run-scoped layout
# threads a day and run id into the directory so every run owns its own tree:
#
#     cv/<YYYY-MM-DD>/<run_id>/<slug>/Salman-Resume.tex
#     cover_letters/<YYYY-MM-DD>/<run_id>/<slug>/Salman-Cover-Letter.tex
#
# Legacy directories are read-only to this helper: recognized and skipped, never
# moved, deleted, or overwritten (the .gitignore name globs like `*-Resume.*`
# keep either layout out of the repo). This module is the single source of the
# layout; generate_batch.py and the Stage 3 orchestrator consume it through here.
# --------------------------------------------------------------------------

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def doc_dir_for(day: str, run_id: Optional[str], slug: str) -> str:
    """The directory (relative to the output root) one application's files go in.

    With a run id this is `<day>/<run_id>/<slug>` — run-scoped, so two runs on
    the same day never share a directory. Without one it is just `<slug>`, the
    legacy layout, so a hand-run without Stage 3 keeps behaving exactly as
    before. Raising on a malformed component is the point: a `/` or `..` inside
    any of them would escape the output root, and the values travel into a
    drafter prompt that runs with write access to the repo.
    """
    if not _DAY_RE.fullmatch(day or ""):
        raise ValueError(f"day must be YYYY-MM-DD, got {day!r}")
    if not slug or "/" in slug or "\\" in slug or ".." in slug or slug.startswith("."):
        raise ValueError(f"slug is not path-safe: {slug!r}")
    if run_id is None:
        return slug
    if not _RUN_ID_RE.fullmatch(run_id) or ".." in run_id:
        raise ValueError(f"run_id is not path-safe: {run_id!r}")
    return f"{day}/{run_id}/{slug}"


def output_paths(repo: Path, day: str, run_id: str, slug: str) -> dict[str, Path]:
    """The four artifact paths for one application inside one run.

    Keys are `cv_tex`, `cv_pdf`, `cl_tex`, `cl_pdf`. Used by the batch runner's
    completeness check and by generate_one's result fixups, so the reported
    tracker paths are the paths that actually exist.
    """
    doc_dir = doc_dir_for(day, run_id, slug)
    cv_dir = Path(repo) / "cv" / doc_dir
    cl_dir = Path(repo) / "cover_letters" / doc_dir
    return {
        "cv_tex": cv_dir / "Salman-Resume.tex",
        "cv_pdf": cv_dir / "Salman-Resume.pdf",
        "cl_tex": cl_dir / "Salman-Cover-Letter.tex",
        "cl_pdf": cl_dir / "Salman-Cover-Letter.pdf",
    }


def legacy_artifacts(slug: str, repo: Path = REPO) -> list[Path]:
    """The four artifact paths in the legacy `cv/<slug>/` layout."""
    return [
        Path(repo) / "cv" / slug / "Salman-Resume.tex",
        Path(repo) / "cv" / slug / "Salman-Resume.pdf",
        Path(repo) / "cover_letters" / slug / "Salman-Cover-Letter.tex",
        Path(repo) / "cover_letters" / slug / "Salman-Cover-Letter.pdf",
    ]


def _all_present(paths: Iterable[Path]) -> bool:
    try:
        return all(p.is_file() and p.stat().st_size > 0 for p in paths)
    except OSError:
        return False


def is_complete(
    slug: str,
    output_root: Optional[Path] = None,
    day: Optional[str] = None,
    run_id: Optional[str] = None,
) -> bool:
    """True when all four artifacts exist as non-empty files.

    With `run_id`, checks the run-scoped layout under `output_root` (default the
    repo). Without one, checks the legacy layout — which is what makes resume
    and `--all-missing` recognize yesterday's work instead of regenerating it.
    A .tex without its .pdf is a draft that never compiled, so all four are
    required.
    """
    if run_id:
        paths = output_paths(
            output_root if output_root is not None else REPO, day or "", run_id, slug
        )
        return _all_present(paths.values())
    return _all_present(legacy_artifacts(slug, output_root if output_root is not None else REPO))


def legacy_is_complete(slug: str, repo: Path = REPO) -> bool:
    """Recognize a finished legacy application without touching it.

    Read-only by contract: callers skip complete legacy slugs rather than
    migrating them, and a partial one (a .tex missing its .pdf) is left exactly
    as it is for a human to look at — never moved, deleted, or overwritten.
    """
    return _all_present(legacy_artifacts(slug, repo))


def job_file_path(today: str, idx: int, run_id: Optional[str] = None) -> Path:
    """Where this job's JSON payload goes, named so concurrent runs cannot collide."""
    if run_id:
        return Path(f"/tmp/jobsearch_selected_{today}_{run_id}_{idx}.json")
    return Path(f"/tmp/jobsearch_selected_{today}_{idx}.json")

# Telegram tolerates ~30 messages/sec globally but throttles bursts to one chat.
SEND_DELAY_SECONDS = 0.12


# --------------------------------------------------------------------------
# Pure logic — no telegram import, no network. Unit-tested directly.
# --------------------------------------------------------------------------


@dataclass
class JobRow:
    """One selectable job, flattened out of a rankset record for display."""

    idx: int
    key: str
    company: str
    title: str
    location: str
    score: Any
    tier: str
    source: str
    language: str
    experience: str
    url: str
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def slug(self) -> str:
        """Directory-safe <Company>_<Role>, matching prior runs' convention."""
        return f"{_slug(self.company)}_{_slug(self.title)}"


def _slug(text: str) -> str:
    """Collapse arbitrary posting text into a filesystem-safe token."""
    cleaned = re.sub(r"[^\w\s-]", "", text or "", flags=re.UNICODE)
    cleaned = re.sub(r"[\s-]+", "_", cleaned).strip("_")
    return cleaned[:60] or "unknown"


def _verdict(raw: Optional[str], *, enriched: bool) -> str:
    """Map a gate verdict onto display vocabulary.

    UNKNOWN means the gate had no description text to read, which is exactly
    the "unverified" state the list needs to show rather than implying a pass.
    """
    v = (raw or "").upper()
    if v == "PASS":
        return "pass"
    if v == "FAIL":
        return "fail"
    return "unverified"


def safe_url(url: str) -> Optional[str]:
    """Return the URL only if it is a plain http(s) link.

    Posting URLs are scraped, i.e. untrusted. Anything with another scheme is
    dropped rather than rendered as a tappable link.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return url


def load_rankset(path: Path) -> list[JobRow]:
    """Flatten a rankset file into display rows, best score first.

    Accepts the pipeline's {"meta":…, "results":[…]} shape and a bare array,
    so a hand-made fixture works without wrapping.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        records = data.get("results") or data.get("jobs") or []
    else:
        records = data
    if not isinstance(records, list):
        raise ValueError(f"{path}: expected a list of jobs, got {type(records).__name__}")

    rows: list[JobRow] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        prerank = rec.get("prerank") or {}
        gates = prerank.get("gates") or {}
        enriched = bool(rec.get("enriched"))
        rows.append(
            JobRow(
                idx=0,  # assigned after sorting
                key=str(rec.get("dedup_key") or rec.get("key") or rec.get("url") or ""),
                company=str(rec.get("company") or "unknown"),
                title=str(rec.get("title") or "untitled"),
                location=str(rec.get("location") or "n/a"),
                score=prerank.get("score", rec.get("score")),
                tier=str(prerank.get("hybrid_tier") or rec.get("verdict") or "n/a"),
                source=str(rec.get("portal") or "n/a"),
                language=_verdict(
                    (gates.get("language") or {}).get("verdict"), enriched=enriched
                ),
                experience=_verdict(
                    (gates.get("experience") or {}).get("verdict"), enriched=enriched
                ),
                url=str(rec.get("url") or ""),
                raw=rec,
            )
        )

    def sort_key(r: JobRow) -> tuple[int, Any]:
        return (0, -r.score) if isinstance(r.score, (int, float)) else (1, 0)

    rows.sort(key=sort_key)
    for i, row in enumerate(rows):
        row.idx = i
    return rows


def render_job(row: JobRow) -> str:
    """One job's message body, Telegram HTML.

    Every interpolated field comes from scraped posting data, so all of it is
    html-escaped: a title containing '<' must not become markup.
    """
    e = html.escape
    lines = [
        f"<b>{row.idx + 1}. {e(row.company)}</b>",
        e(row.title),
        "",
        f"📍 {e(row.location)}",
        f"📊 score <b>{e(str(row.score))}</b> · fit <b>{e(row.tier)}</b>",
        f"🔎 source {e(row.source)}",
        f"🗣 language {e(row.language)} · 🧭 experience {e(row.experience)}",
    ]
    url = safe_url(row.url)
    if url:
        lines.append(f'\n<a href="{e(url, quote=True)}">open posting</a>')
    else:
        lines.append("\n<i>no usable link</i>")
    return "\n".join(lines)


def toggle_label(selected: bool) -> str:
    return "☑️ Selected" if selected else "☐ Select"


def render_control(
    total: int, selected_count: int, *, locked: bool = False, generating: bool = True
) -> str:
    """The control message body, sent last so it sits at the end of the list.

    `generating` is False under --no-generate, where promising documents would
    be immediately contradicted by the next message.
    """
    if locked:
        tail = (
            "<i>Generating documents…</i>"
            if generating
            else "<i>Dry run — no documents will be written.</i>"
        )
        return (
            f"<b>Selection locked</b>\n{selected_count} of {total} job(s) submitted.\n"
            + tail
        )
    return (
        f"<b>{total} job(s) above.</b>\n"
        f"Selected: <b>{selected_count}</b>\n\n"
        "Toggle the ones you want, then press Submit."
    )


class SelectionState:
    """Which jobs are toggled on, persisted so a crash cannot lose the picks."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else None
        self.selected: set[int] = set()
        self.job_message_ids: dict[int, int] = {}
        self.control_message_id: Optional[int] = None
        self.locked = False
        # `locked` means Submit was pressed; `completed` means the run finished
        # with it. The two differ only in the window where generation is in
        # flight, and that window is exactly what selector_listener.py must not
        # re-enter after a crash: drafting writes documents and tracker rows, so
        # repeating it duplicates both.
        self.completed = False

    def toggle(self, idx: int) -> bool:
        """Flip one job. Returns the resulting state."""
        if idx in self.selected:
            self.selected.discard(idx)
            now = False
        else:
            self.selected.add(idx)
            now = True
        self.save()
        return now

    def select_all(self, indices: Iterable[int]) -> None:
        self.selected = set(indices)
        self.save()

    def clear(self) -> None:
        self.selected = set()
        self.save()

    def ordered(self) -> list[int]:
        return sorted(self.selected)

    def save(self) -> None:
        if not self.path:
            return
        payload = {
            "selected": self.ordered(),
            "job_message_ids": {str(k): v for k, v in self.job_message_ids.items()},
            "control_message_id": self.control_message_id,
            "locked": self.locked,
            "completed": self.completed,
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def load(self) -> None:
        if not self.path or not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.selected = set(data.get("selected") or [])
        self.job_message_ids = {
            int(k): v for k, v in (data.get("job_message_ids") or {}).items()
        }
        self.control_message_id = data.get("control_message_id")
        self.locked = bool(data.get("locked"))
        # Absent in files written before the listener existed. Defaulting to
        # False is right for those: none of them are mid-generation.
        self.completed = bool(data.get("completed"))


def is_current_message(message_id: Optional[int], state: "SelectionState") -> bool:
    """Whether a tapped button belongs to the list this run is serving.

    Buttons outlive the run that drew them. Telegram keeps every previous day's
    job messages in the chat with their keyboards live and tappable, and
    `callback_data` carries only "t:<idx>" — an index into *this* run's rows. So
    a tap on yesterday's job #3 would toggle today's job #3, and Submit could
    draft an application for a posting that was never on screen.

    Message identity is what separates them, and it needs no callback_data
    change or migration: every id for the live list is recorded in the state
    file before polling starts, so an id absent from it is from another run.
    """
    if message_id is None:
        return False
    return (
        message_id == state.control_message_id
        or message_id in state.job_message_ids.values()
    )


def build_job_payload(row: JobRow) -> dict:
    """The job object handed to the drafter.

    The rankset carries prerank fields; the drafter's contract wants
    score/verdict/posting_text. strengths/gaps do not exist at this stage and
    are passed empty rather than invented.
    """
    return {
        "key": row.key,
        "title": row.title,
        "company": row.company,
        "url": row.url,
        "location": row.location,
        "portal": row.source,
        "score": row.score,
        "verdict": row.tier,
        "language_status": row.language,
        "experience_status": row.experience,
        "strengths": [],
        "gaps": [],
        "posting_text": row.raw.get("description")
        or row.raw.get("description_snippet")
        or "",
    }


def extract_json_object(text: str) -> Optional[dict]:
    """Pull the first balanced {...} out of a model's stdout."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def tracker_row(row: JobRow, result: dict, today: str) -> list[str]:
    """One job_search_tracker.csv row.

    Written by the coordinator, never by the parallel drafters: concurrent
    appends to one CSV interleave and corrupt rows.
    """
    return [
        today,
        row.company,
        "",
        row.title,
        "",
        "telegram-select",
        "drafted",
        "",
        str(row.score),
        f"selected via Telegram; fit {row.tier}",
        result.get("cv_file", ""),
        result.get("cover_letter_file", ""),
        row.url,
    ]


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


def load_env(path: Optional[Path] = None) -> dict[str, str]:
    """Read KEY=VALUE from the selector env file. Never logs values."""
    env_path = Path(path or os.environ.get("JOBSEARCH_SELECTOR_ENV") or DEFAULT_ENV)
    if not env_path.exists():
        raise SystemExit(
            f"selector config not found: {env_path}\n"
            "Create it (mode 600) with SELECTOR_BOT_TOKEN, SELECTOR_CHAT_ID and\n"
            "SELECTOR_ALLOWED_USER_IDS. See the module docstring."
        )
    out: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip("'\"")
    return out


# --------------------------------------------------------------------------
# Generation — one `claude -p` per selected job, run in parallel
# --------------------------------------------------------------------------


async def generate_one(
    row: JobRow,
    today: str,
    sem: asyncio.Semaphore,
    log: Path,
    run_id: Optional[str] = None,
    output_root: Optional[Path] = None,
) -> dict:
    """Draft + compile one job's documents in its own Claude Code process.

    With `run_id` the documents land in the run-scoped layout
    (`cv/<day>/<run_id>/<slug>/…`) under `output_root` (default the repo), the
    reported paths are the ones that actually exist on disk, and `ok` additionally
    requires all four artifacts to be present — the child's own `cv_compiled`
    flag is trusted less than the filesystem. Without `run_id` the legacy layout
    and behavior are preserved byte-for-byte for the listener and hand-runs.
    """
    async with sem:
        job_file = job_file_path(today, row.idx, run_id)
        job_file.write_text(
            json.dumps(build_job_payload(row), indent=2), encoding="utf-8"
        )

        doc_dir = doc_dir_for(today, run_id, row.slug)
        output_base = str(Path(output_root)) if run_id and output_root is not None else "."
        prompt = DRAFT_PROMPT.read_text(encoding="utf-8")
        prompt = prompt.replace("<JOB_FILE_PATH>", str(job_file))
        prompt = prompt.replace("<OUTPUT_ROOT>", output_base)
        prompt = prompt.replace("<OUTPUT_DIR>", doc_dir)
        prompt = prompt.replace("<OUTPUT_SLUG>", row.slug)

        proc = await asyncio.create_subprocess_exec(
            "claude",
            "-p",
            prompt,
            "--allowedTools",
            "Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch",
            "--output-format",
            "text",
            cwd=str(REPO),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        stdout = out.decode("utf-8", "replace")
        stderr = err.decode("utf-8", "replace")

        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"\n=== job {row.idx} {row.slug} exit={proc.returncode} ===\n")
            fh.write(stdout[-4000:])
            if stderr:
                fh.write("\n--- stderr ---\n" + stderr[-2000:])

        if proc.returncode != 0:
            # Surface the child's own error text, not just the exit code.
            # "claude exited 1" sent the reader to the log for what was really a
            # quota refusal. stderr first, then the tail of stdout: the API relay
            # prints its 403 to STDOUT, so a stderr-only read reported nothing and
            # the quota guard never fired (6 jobs lost that way on 2026-08-25).
            detail = next(
                (ln.strip() for ln in stderr.splitlines() if ln.strip()), ""
            ) or next(
                (ln.strip() for ln in reversed(stdout.splitlines()) if ln.strip()), ""
            )
            return {
                "row": row,
                "ok": False,
                "error": (
                    f"claude exited {proc.returncode}: {detail[:300]}"
                    if detail
                    else f"claude exited {proc.returncode}"
                ),
            }

        parsed = extract_json_object(stdout) or {}
        jobs = parsed.get("jobs") or []
        result = jobs[0] if jobs else parsed
        if run_id:
            fixup_run_scoped_result(result, today, run_id, row.slug, output_root)
            ok = bool(result.get("cv_compiled")) and bool(result.get("cl_compiled"))
            artifacts = output_paths(output_root or REPO, today, run_id, row.slug)
            missing = [k for k, p in artifacts.items() if not (p.is_file() and p.stat().st_size > 0)]
            if missing:
                ok = False
                result["missing_artifacts"] = missing
                result["errors"] = list(result.get("errors") or []) + [
                    f"run-scoped artifacts missing: {', '.join(missing)}"
                ]
        else:
            ok = bool(result.get("cv_compiled")) and bool(result.get("cl_compiled"))
        return {
            "row": row,
            "ok": ok,
            "result": result,
            "error": None if ok else (parsed.get("errors") or ["incomplete output"])[0],
        }


def fixup_run_scoped_result(
    result: dict,
    today: str,
    run_id: str,
    slug: str,
    output_root: Optional[Path] = None,
) -> None:
    """Rewrite a drafter's reported paths onto the run-scoped layout, in place.

    The drafter (prompts/selected_job_draft.md) still reports legacy-shaped paths
    because its contract predates run scoping. The filesystem is the truth: the
    files exist at `cv/<day>/<run_id>/<slug>/…`, so the tracker and the summary
    are pointed at what is actually there — a tracker row naming a path nobody
    can open is worse than one naming the real location.

    `slug` comes from the caller's JobRow, not the drafter's JSON: the prompt's
    output contract predates run scoping and names no slug field, so a fallback
    to the parsed result would silently skip every fixup.
    """
    if not slug:
        return
    root = output_root or REPO
    paths = output_paths(root, today, run_id, slug)
    result["cv_file"] = str(paths["cv_tex"].relative_to(root))
    result["cv_pdf"] = str(paths["cv_pdf"].relative_to(root))
    result["cover_letter_file"] = str(paths["cl_tex"].relative_to(root))
    result["cl_pdf"] = str(paths["cl_pdf"].relative_to(root))


def append_tracker(
    entries: list[tuple[JobRow, dict]], today: str, tracker: Optional[Path] = None
) -> None:
    """Serially append tracker rows after all drafters have finished.

    `tracker` is overridable so a sandbox run cannot append invented companies
    to the real job_search_tracker.csv.
    """
    if not entries:
        return
    path = Path(tracker) if tracker else TRACKER
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(
                [
                    "date", "company", "sector", "role", "role_type", "channel",
                    "status", "contact_person", "fit_rating", "notes", "cv_file",
                    "cover_letter_file", "source",
                ]
            )
        for row, result in entries:
            w.writerow(tracker_row(row, result, today))


def render_summary(outcomes: list[dict]) -> str:
    """The final Telegram confirmation."""
    e = html.escape
    ok = [o for o in outcomes if o["ok"]]
    bad = [o for o in outcomes if not o["ok"]]
    lines = [f"<b>Generated {len(ok)}/{len(outcomes)} application(s)</b>", ""]
    for o in ok:
        row = o["row"]
        r = o.get("result") or {}
        lines.append(f"✅ <b>{e(row.company)}</b> — {e(row.title)}")
        for label in ("cv_file", "cover_letter_file"):
            if r.get(label):
                lines.append(f"   <code>{e(str(r[label]))}</code>")
    for o in bad:
        row = o["row"]
        lines.append(f"❌ <b>{e(row.company)}</b> — {e(row.title)}")
        lines.append(f"   <i>{e(str(o.get('error') or 'failed'))}</i>")
    if ok:
        lines.append("\nStored in the repo; status <code>drafted</code> in the tracker.")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Telegram layer
# --------------------------------------------------------------------------


async def run_interactive(rows: list[JobRow], cfg: dict, args: argparse.Namespace) -> int:
    # Imported here so the pure logic above stays importable without PTB.
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.constants import ParseMode
    from telegram.error import RetryAfter
    from telegram.ext import Application, CallbackQueryHandler, ContextTypes

    token = cfg.get("SELECTOR_BOT_TOKEN")
    chat_id = cfg.get("SELECTOR_CHAT_ID")
    if not token or not chat_id:
        raise SystemExit("SELECTOR_BOT_TOKEN and SELECTOR_CHAT_ID are both required")
    allowed = {
        int(x) for x in re.split(r"[,\s]+", cfg.get("SELECTOR_ALLOWED_USER_IDS", "")) if x
    }
    if not allowed:
        raise SystemExit("SELECTOR_ALLOWED_USER_IDS must list at least one user id")

    today = args.today or date.today().isoformat()
    state = SelectionState(Path(f"/tmp/jobsearch_selection_{today}.json"))
    log = Path(f"/tmp/jobsearch_select_generate_{today}.log")
    done = asyncio.Event()

    def job_markup(idx: int) -> "InlineKeyboardMarkup":
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        toggle_label(idx in state.selected),
                        callback_data=f"{CB_TOGGLE}:{idx}",
                    )
                ]
            ]
        )

    def control_markup() -> "InlineKeyboardMarkup":
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Select all", callback_data=CB_ALL),
                    InlineKeyboardButton("Clear", callback_data=CB_NONE),
                ],
                [
                    InlineKeyboardButton(
                        f"✅ Submit ({len(state.selected)})", callback_data=CB_SUBMIT
                    )
                ],
            ]
        )

    async def guard(query) -> bool:
        """Only the allowlisted user, tapping a button from THIS run."""
        if not (query.from_user and query.from_user.id in allowed):
            await query.answer("Not authorised.", show_alert=True)
            return False
        mid = query.message.message_id if query.message else None
        if not is_current_message(mid, state):
            await query.answer(
                "This list is from an earlier run. Scroll down to the current one.",
                show_alert=True,
            )
            return False
        return True

    async def refresh_control(bot) -> None:
        if state.control_message_id is None:
            return
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=state.control_message_id,
                text=render_control(
                    len(rows),
                    len(state.selected),
                    locked=state.locked,
                    generating=not args.no_generate,
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=None if state.locked else control_markup(),
            )
        except Exception:
            pass  # a no-op edit (identical text) is not worth failing the flow

    async def on_toggle(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        query = update.callback_query
        if not await guard(query):
            return
        if state.locked:
            await query.answer("Already submitted.")
            return
        idx = int(query.data.split(":", 1)[1])
        now = state.toggle(idx)
        await query.answer("Selected" if now else "Removed")
        await query.edit_message_reply_markup(reply_markup=job_markup(idx))
        await refresh_control(context.bot)

    async def on_bulk(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        query = update.callback_query
        if not await guard(query):
            return
        if state.locked:
            await query.answer("Already submitted.")
            return
        if query.data == CB_ALL:
            state.select_all(r.idx for r in rows)
        else:
            state.clear()
        await query.answer()
        for row in rows:
            mid = state.job_message_ids.get(row.idx)
            if mid is None:
                continue
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=chat_id, message_id=mid, reply_markup=job_markup(row.idx)
                )
            except Exception:
                pass
        await refresh_control(context.bot)

    async def on_submit(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        query = update.callback_query
        if not await guard(query):
            return
        if state.locked:
            await query.answer("Already submitted.")
            return
        chosen = [rows[i] for i in state.ordered() if 0 <= i < len(rows)]
        if not chosen:
            await query.answer("Nothing selected.", show_alert=True)
            return

        state.locked = True
        state.save()
        await query.answer("Submitted")

        # Freeze every toggle so the set cannot drift mid-generation.
        for row in rows:
            mid = state.job_message_ids.get(row.idx)
            if mid is None:
                continue
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=chat_id, message_id=mid, reply_markup=None
                )
            except Exception:
                pass
        await refresh_control(context.bot)

        if args.no_generate:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "<b>--no-generate</b>: selection recorded, no documents written.\n"
                    + "\n".join(
                        f"• {html.escape(r.company)} — {html.escape(r.title)}"
                        for r in chosen
                    )
                ),
                parse_mode=ParseMode.HTML,
            )
            done.set()
            return

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Generating {len(chosen)} application(s)… this takes a few minutes.",
            parse_mode=ParseMode.HTML,
        )

        sem = asyncio.Semaphore(MAX_PARALLEL_JOBS)
        outcomes = await asyncio.gather(
            *(
                generate_one(
                    r, today, sem, log,
                    run_id=getattr(args, "run_id", None),
                    output_root=getattr(args, "output_root", None),
                )
                for r in chosen
            )
        )
        append_tracker(
            [(o["row"], o.get("result") or {}) for o in outcomes if o["ok"]],
            today,
            args.tracker,
        )
        # Set before the summary send so a hand-run leaves the same terminal
        # state the listener would; scripts/selector_listener.py reads it to
        # refuse re-generating documents after a restart.
        state.completed = True
        state.save()
        await context.bot.send_message(
            chat_id=chat_id,
            text=render_summary(list(outcomes)),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        done.set()

    app = Application.builder().token(token).build()
    app.add_handler(CallbackQueryHandler(on_toggle, pattern=rf"^{CB_TOGGLE}:\d+$"))
    app.add_handler(CallbackQueryHandler(on_bulk, pattern=rf"^({CB_ALL}|{CB_NONE})$"))
    app.add_handler(CallbackQueryHandler(on_submit, pattern=rf"^{CB_SUBMIT}$"))

    async with app:
        await app.start()
        # PTB requires stop() to mirror start() before __aexit__ calls shutdown(),
        # and updater.stop() alone does not satisfy it. Both live in one finally so
        # an early return or a failed send still tears down in the right order.
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=f"<b>Job selection — {today}</b>\n{len(rows)} ranked job(s) follow.",
                parse_mode=ParseMode.HTML,
            )
            for row in rows:
                for attempt in range(3):
                    try:
                        msg = await app.bot.send_message(
                            chat_id=chat_id,
                            text=render_job(row),
                            parse_mode=ParseMode.HTML,
                            reply_markup=job_markup(row.idx),
                            disable_web_page_preview=True,
                        )
                        state.job_message_ids[row.idx] = msg.message_id
                        break
                    except RetryAfter as exc:
                        await asyncio.sleep(float(exc.retry_after) + 0.5)
                        if attempt == 2:
                            raise
                await asyncio.sleep(SEND_DELAY_SECONDS)

            control = await app.bot.send_message(
                chat_id=chat_id,
                text=render_control(len(rows), 0),
                parse_mode=ParseMode.HTML,
                reply_markup=control_markup(),
            )
            state.control_message_id = control.message_id
            state.save()

            await app.updater.start_polling(allowed_updates=["callback_query"])
            print(f"[select] {len(rows)} job(s) sent; waiting for Submit…", flush=True)
            try:
                await asyncio.wait_for(done.wait(), timeout=args.timeout)
            except asyncio.TimeoutError:
                print(
                    f"[select] timed out after {args.timeout}s with no Submit",
                    flush=True,
                )
                await app.bot.send_message(
                    chat_id=chat_id,
                    text="⏳ Selection window closed with no Submit. Re-run to pick again.",
                )
                return 2
        finally:
            if app.updater.running:
                await app.updater.stop()
            if app.running:
                await app.stop()
    return 0


# --------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rankset", type=Path, required=True, help="rankset JSON to read")
    ap.add_argument("--today", default=None, help="YYYY-MM-DD; names state files")
    ap.add_argument("--limit", type=int, default=25, help="max jobs to offer")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="render the messages to stdout; no network, no token, no generation",
    )
    ap.add_argument(
        "--no-generate",
        action="store_true",
        help="run the real selection UI but stop before spending money on drafting",
    )
    ap.add_argument("--env", type=Path, default=None, help="override the config path")
    ap.add_argument(
        "--tracker",
        type=Path,
        default=None,
        help="write tracker rows here instead of job_search_tracker.csv "
        "(use for sandbox runs so fake companies never reach the real log)",
    )
    ap.add_argument(
        "--run-id",
        default=None,
        help="Stage 3 run id: documents land in cv/<day>/<run_id>/<slug>/ instead "
        "of cv/<slug>/, so concurrent runs cannot overwrite each other",
    )
    ap.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="root the document tree here instead of the repo (paired with --run-id "
        "for Stage 3 run-scoped output)",
    )
    ap.add_argument(
        "--timeout", type=float, default=3600.0, help="seconds to wait for Submit"
    )
    args = ap.parse_args(argv)

    rows = load_rankset(args.rankset)[: args.limit]
    if not rows:
        print(f"[select] no jobs in {args.rankset}", file=sys.stderr)
        return 1

    if args.dry_run:
        for row in rows:
            print("-" * 60)
            print(render_job(row))
            print(f"[button] {toggle_label(False)}   (callback {CB_TOGGLE}:{row.idx})")
        print("-" * 60)
        print(render_control(len(rows), 0))
        print("[buttons] Select all | Clear | ✅ Submit (0)")
        return 0

    cfg = load_env(args.env)
    return asyncio.run(run_interactive(rows, cfg, args))


if __name__ == "__main__":
    sys.exit(main())
