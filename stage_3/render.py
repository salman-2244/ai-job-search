"""Telegram-side rendering of run state: pure functions, no network calls.

Every dynamic value is `html.escape`d before it enters the message. Log lines echo
job postings, and job postings are untrusted input — a description that says
`<ignore previous instructions>` must render as text, never as markup. Static
vocabulary (labels, icons) is ours, but it goes through the same escaper anyway so
a future edit cannot quietly introduce a raw path through the template.

The module never touches Telegram: `render_run` returns the HTML body and the two
keyboard builders return plain JSON-serialisable dicts the bot layer converts to
`InlineKeyboardMarkup`. Callback payloads are short counters (`count:10`,
`geo:3`, `custom`) — Telegram caps callback data at 64 bytes, and no geo name,
path or run id belongs in there.
"""

from __future__ import annotations

import html
import re
import time

from .progress import PHASE_IDS, PHASES, RunState

#: Presentation-only icons. `progress.Phase` stays pure data; emoji are a Telegram
#: concern, so they live here.
PHASE_ICONS = {
    "0b": "📬",
    "1": "🔍",
    "1b": "✂️",
    "1c": "📖",
    "1b-final": "🎯",
    "2": "🧠",
    "2b": "🚦",
    "3": "📱",
    "4": "🔬",
    "5": "📝",
}
_DEFAULT_PHASE_ICON = "📄"

_STATUS_ICONS = {
    "pending": "⬜",
    "running": "🔄",
    "done": "✅",
    "skipped": "⏭️",
    "failed": "❌",
}

#: Phase ids whose count is worth surfacing as a headline number, in pipeline order.
_COUNT_LABELS = (
    ("1", "Fetched"),
    ("1b", "Pre-filtered"),
    ("1b-final", "Shortlisted"),
    ("2", "Ranked"),
    ("2b", "Cleared gate"),
    ("3", "Offered"),
)

# Anchored on the pipeline's own wording, per docs/STAGE_3_HANDOFF.md §5. Scanned
# across *all* phase messages, not just the latest line: the geo line is logged once
# during Phase 1 but must stay visible for the rest of the run.
_GEO_RE = re.compile(r"geo-scoped to\s+([^(]+?)(?:\s*\(|$)")
_REPEATS_RE = re.compile(r"\b(\d+)\s+re-included\b", re.IGNORECASE)
_FALLBACK_RE = re.compile(r"\bfallback\b|\bfall(?:s|ing)?\s+back\b", re.IGNORECASE)

_MAX_DETAIL_CHARS = 200
_BAR_WIDTH = 10


def _esc(value) -> str:
    """Escape a dynamic value for Telegram's HTML parse mode."""
    return html.escape(str(value))


def progress_bar(pct: int, width: int = _BAR_WIDTH) -> str:
    """A fixed-width bar. Floor-division on purpose: 99% must not read as full."""
    pct = max(0, min(100, int(pct)))
    filled = (pct * width) // 100
    return "█" * filled + "░" * (width - filled)


def count_keyboard(values: tuple[int, ...] = (5, 10, 20, 25)) -> list[list[dict]]:
    """Job-count buttons: one row of presets plus a custom entry."""
    row = [{"text": f"{value} jobs", "callback_data": f"count:{value}"} for value in values]
    return [row, [{"text": "✏️ Custom", "callback_data": "custom"}]]


def geo_keyboard(geos: list[str], per_row: int = 2) -> list[list[dict]]:
    """Geo buttons keyed by index, so the geo name never enters callback data."""
    buttons = [
        {"text": _esc(geo), "callback_data": f"geo:{index}"}
        for index, geo in enumerate(geos)
    ]
    return [buttons[start:start + per_row] for start in range(0, len(buttons), per_row)]


def _phase_line(phase_state, current_phase: str | None) -> str:
    icon = PHASE_ICONS.get(phase_state.phase.id, _DEFAULT_PHASE_ICON)
    label = _esc(phase_state.phase.label)
    if phase_state.phase.id == current_phase:
        label = f"<b>{label}</b>"
    line = f"{_STATUS_ICONS.get(phase_state.status, '⬜')} {icon} {label}"
    if phase_state.count is not None:
        line += f" · {phase_state.count}"
    return line


def _counts_line(state: RunState) -> str | None:
    parts = []
    for phase_id, label in _COUNT_LABELS:
        count = state.phases[phase_id].count
        if count is not None:
            parts.append(f"{label} {count}")
    return " · ".join(parts) if parts else None


def _context_parts(state: RunState) -> list[str]:
    """Geo, repeats and fallback hints, from the newest mention in any phase message."""
    geo = None
    repeats = None
    fallback = False
    for phase_state in state.phases.values():
        message = phase_state.message
        if not message:
            continue
        geo_match = _GEO_RE.search(message)
        if geo_match:
            geo = geo_match.group(1).strip()
        repeats_match = _REPEATS_RE.search(message)
        if repeats_match:
            repeats = int(repeats_match.group(1))
        if not fallback and _FALLBACK_RE.search(message):
            fallback = True
    parts = []
    if geo:
        parts.append(f"📍 {_esc(geo)}")
    if repeats:
        parts.append(f"🔁 {repeats} re-included")
    if fallback:
        parts.append("⚠️ fallback source in use")
    return parts


def _time_line(state: RunState, now: float) -> str | None:
    if state.complete or state.last_activity is None:
        return None
    quiet = int(now - state.last_activity)
    if quiet < 0:
        return None
    if state.stalled(now):
        return f"💬 <b>Still working</b> — no log update for {quiet}s"
    return f"⏱ last update {quiet}s ago"


def render_run(state: RunState, now: float | None = None) -> str:
    """Render a RunState as Telegram HTML. Deterministic for a given (state, now)."""
    if now is None:
        now = time.time()
    pct = state.pct
    lines: list[str] = []

    bar_line = f"<code>{progress_bar(pct)}</code> <b>{pct}%</b>"
    if state.complete:
        lines.append(bar_line)
        lines.append(
            "🎉 <b>Pipeline complete</b> — documents saved to disk "
            "(PDFs are never sent here)"
        )
    else:
        if state.current_phase is None:
            lines.append(bar_line)
            lines.append("🌙 <b>Waiting to start</b>")
        else:
            position = PHASE_IDS.index(state.current_phase) + 1
            lines.append(f"{bar_line} · phase {position}/{len(PHASE_IDS)}")
            icon = PHASE_ICONS.get(state.current_phase, _DEFAULT_PHASE_ICON)
            label = _esc(state.phases[state.current_phase].phase.label)
            failed = state.phases[state.current_phase].status == "failed"
            lead = "❌" if failed else "🔄"
            lines.append(f"{lead} <b>{icon} {label}</b>")

    failed_phases = [
        phase_state for phase_state in state.phases.values()
        if phase_state.status == "failed"
    ]
    if failed_phases and not state.complete:
        names = ", ".join(_esc(ps.phase.label) for ps in failed_phases)
        lines.append(f"❌ <b>Failed: {names}</b> — the run log has the detail")

    lines.append("")  # blank separator before the checklist
    for phase in PHASES:
        lines.append(_phase_line(state.phases[phase.id], state.current_phase))

    counts = _counts_line(state)
    if counts:
        lines.append("")
        lines.append(f"📊 {_esc(counts)}")

    context = _context_parts(state)
    if context:
        lines.append(" · ".join(context))

    if state.current_phase is not None and state.message:
        detail = state.message
        if len(detail) > _MAX_DETAIL_CHARS:
            detail = detail[:_MAX_DETAIL_CHARS] + "…"
        lines.append(f"└ <i>{_esc(detail)}</i>")

    time_line = _time_line(state, now)
    if time_line:
        lines.append(time_line)

    return "\n".join(lines)
