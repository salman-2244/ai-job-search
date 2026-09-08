"""Pure progress model for Stage 3 pipeline log streams."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Phase:
    id: str
    label: str
    weight: int


PHASES = (
    Phase("0b", "Job alerts", 4),
    Phase("1", "Portal search", 22),
    Phase("1b", "Shortlist", 3),
    Phase("1c", "LinkedIn details", 12),
    Phase("1b-final", "Deep-rank selection", 3),
    Phase("2", "Ranking", 38),
    Phase("2b", "Drafting gate", 2),
    Phase("3", "Telegram selection", 5),
    Phase("4", "QA review", 6),
    Phase("5", "Report", 5),
)
PHASE_IDS = tuple(phase.id for phase in PHASES)
PHASE_BY_ID = {phase.id: phase for phase in PHASES}
_PHASE_POSITION = {phase_id: index for index, phase_id in enumerate(PHASE_IDS)}
_PHASE_ALTERNATION = "|".join(re.escape(value) for value in sorted(PHASE_IDS, key=len, reverse=True))
_LOG_RE = re.compile(r"^\[(?P<timestamp>\d{2}:\d{2}:\d{2})\]\s*(?P<message>.*)$")
_PHASE_RE = re.compile(rf"\bPhase\s+(?P<phase>{_PHASE_ALTERNATION})(?![\w-])", re.IGNORECASE)
_COUNT_RE = re.compile(
    r"(?:complete(?:\s+on\s+attempt[^:]*)?:\s*|offering\s+)(?P<count>\d+)\s+"
    r"(?:of\s+\d+\s+)?(?:unique\s+)?(?:job|jobs|job\(s\))\b",
    re.IGNORECASE,
)
_VALID_STATUSES = frozenset({"pending", "running", "done", "skipped", "failed"})


@dataclass
class PhaseState:
    phase: Phase
    status: str = "pending"
    count: int | None = None
    message: str = ""

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"invalid phase status: {self.status}")


@dataclass
class RunState:
    phases: dict[str, PhaseState] = field(
        default_factory=lambda: {phase.id: PhaseState(phase) for phase in PHASES}
    )
    current_phase: str | None = None
    message: str = "Waiting to start"
    last_activity: float | None = None
    complete: bool = False

    @property
    def pct(self) -> int:
        if self.complete:
            return 100
        value = 0.0
        for phase in PHASES:
            status = self.phases[phase.id].status
            if status in {"done", "skipped", "failed"}:
                value += phase.weight
            elif status == "running":
                value += phase.weight / 2
        return min(99, int(value))

    def stalled(self, now: float, threshold: float = 120) -> bool:
        return (
            not self.complete
            and self.last_activity is not None
            and now - self.last_activity >= threshold
        )


@dataclass(frozen=True)
class ParsedLogLine:
    timestamp: str
    message: str
    phase_id: str | None = None
    status: str | None = None
    count: int | None = None
    pipeline_complete: bool = False


def parse_log_line(line: str) -> ParsedLogLine | None:
    """Return structured state for a meaningful pipeline log line."""
    match = _LOG_RE.match(line.rstrip("\n"))
    if not match:
        return None
    timestamp = match.group("timestamp")
    message = match.group("message").strip()
    if message == "Pipeline complete.":
        return ParsedLogLine(timestamp, message, pipeline_complete=True)

    phase_match = _PHASE_RE.search(message)
    if not phase_match:
        return None
    phase_id = phase_match.group("phase").lower()
    lowered = message.lower()
    if "failed" in lowered or "fatal" in lowered or "timeout" in lowered:
        status = "failed"
    elif "skipped" in lowered:
        status = "skipped"
    elif "complete" in lowered:
        status = "done"
    else:
        status = "running"
    count_match = _COUNT_RE.search(message)
    count = int(count_match.group("count")) if count_match else None
    return ParsedLogLine(timestamp, message, phase_id, status, count)


class ProgressTracker:
    """Incrementally project pipeline log lines into a mutable run state."""

    def __init__(self, state: RunState | None = None):
        self.state = state or RunState()

    def feed(self, line: str, now: float | None = None) -> RunState:
        parsed = parse_log_line(line)
        if parsed is None:
            return self.state
        observed_at = time.monotonic() if now is None else now
        self.state.last_activity = observed_at
        self.state.message = parsed.message
        if parsed.pipeline_complete:
            self.state.complete = True
            for phase_state in self.state.phases.values():
                if phase_state.status in {"pending", "running"}:
                    phase_state.status = "done"
            return self.state

        assert parsed.phase_id is not None and parsed.status is not None
        current_index = _PHASE_POSITION[parsed.phase_id]
        for earlier_id in PHASE_IDS[:current_index]:
            earlier = self.state.phases[earlier_id]
            if earlier.status in {"pending", "running"}:
                earlier.status = "done"
        phase_state = self.state.phases[parsed.phase_id]
        phase_state.status = parsed.status
        phase_state.message = parsed.message
        if parsed.count is not None:
            phase_state.count = parsed.count
        self.state.current_phase = parsed.phase_id
        return self.state


def replay(text: str) -> RunState:
    """Replay a complete log through the same incremental parser."""
    tracker = ProgressTracker()
    for line in text.splitlines():
        tracker.feed(line)
    return tracker.state
