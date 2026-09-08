"""Crash-safe cron schedule storage for the Stage 3 bot.

A schedule is the only thing the bot persists about *future* runs, so the store
is deliberately boring and strict:

- records are validated at construction — a malformed cron, an out-of-bounds
  job count, an unknown timezone or an unsafe id can never reach disk;
- saves are atomic (same-directory temp file, fsync, mode 0600, `os.replace`)
  and mirrored into a `.bak` of the same payload, so a torn primary recovers
  from the backup;
- loads validate everything again and *never guess*: a primary that is corrupt
  or fails the schema falls back to the backup, and if both are unusable a typed
  `ScheduleStoreError` is raised — the caller disables scheduling rather than
  launching runs from reconstructed data it cannot trust;
- only the fields below are serialized. No credentials, cookies, job text or
  prompts ever enter this file.

Matching follows Vixie-cron semantics, including the day-of-month /
day-of-week OR rule when both fields are restricted, `7` meaning Sunday, and
five-field expressions interpreted in each record's own IANA timezone.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

STORE_VERSION = 1

#: Must stay in step with the orchestrator's bound so a schedule can never ask
#: for a run the orchestrator will refuse at launch time.
MAX_JOB_COUNT = 50

#: Enforced upper bounds for (minute, hour, day-of-month, month, day-of-week).
#: `validate_cron` returns them so tooling can render them next to the input.
FIELD_BOUNDS = (59, 23, 31, 12, 7)

_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,15}")
_KINDS = frozenset({"once", "recurring"})
_RECORD_KEYS = (
    "id", "kind", "expression", "geo", "job_count", "timezone",
    "enabled", "last_started_at", "next_run_at",
)
_RANGE_SPECS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_SEARCH_DAYS = 400  # next_run() gives up after this many days — over a year


class ScheduleError(ValueError):
    """A schedule record or expression is invalid."""


class ScheduleStoreError(RuntimeError):
    """The schedule store (and its backup) could not be trusted."""


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError, TypeError) as exc:
        raise ScheduleError(f"unknown timezone {name!r}") from exc


def _parse_field(spec: str, lo: int, hi: int) -> tuple[frozenset, bool]:
    """Parse one cron field into (matched values, restricted?).

    `restricted` is False only for a bare `*`; the dom/dow OR rule keys off it.
    Supports `*`, `*/n`, `a`, `a-b`, `a-b/n`, `a/n` (a..hi step n) and
    comma-separated lists of those.
    """
    if spec == "*":
        return frozenset(range(lo, hi + 1)), False
    values: set[int] = set()
    for part in spec.split(","):
        if not part:
            raise ScheduleError(f"empty list item in cron field {spec!r}")
        base, slash, step_text = part.partition("/")
        step = 1
        if slash:
            if not step_text.isdigit() or int(step_text) == 0:
                raise ScheduleError(f"bad step in cron field {spec!r}")
            step = int(step_text)
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a_text, _, b_text = base.partition("-")
            if not a_text.isdigit() or not b_text.isdigit():
                raise ScheduleError(f"bad range in cron field {spec!r}")
            start, end = int(a_text), int(b_text)
        else:
            if not base.isdigit():
                raise ScheduleError(f"bad value in cron field {spec!r}")
            start = int(base)
            end = hi if slash else start  # `5/2` means 5..hi by 2, as in Vixie cron
        if start < lo or end > hi or start > end:
            raise ScheduleError(f"value out of range in cron field {spec!r}")
        values.update(range(start, end + 1, step))
    if not values:
        raise ScheduleError(f"cron field {spec!r} matches nothing")
    return frozenset(values), True


def _compile(expression: str) -> tuple:
    """Compile a five-field expression into ((values, restricted) x5)."""
    if not isinstance(expression, str):
        raise ScheduleError("cron expression must be a string")
    parts = expression.split()
    if len(parts) != 5:
        raise ScheduleError(
            f"cron expression must have exactly 5 fields "
            f"(minute hour day-of-month month day-of-week), got {len(parts)}"
        )
    fields = [_parse_field(spec, lo, hi) for spec, (lo, hi) in zip(parts, _RANGE_SPECS)]
    dow_values, dow_restricted = fields[4]
    if 7 in dow_values:  # 7 is Sunday, same as 0
        fields[4] = (frozenset(dow_values | {0}), dow_restricted)
    return tuple(fields)


def validate_cron(expression: str) -> tuple[int, int, int, int, int]:
    """Validate a five-field cron expression; return the enforced bounds.

    The raise is the real contract — callers render `ScheduleError` messages to
    the user instead of persisting the record. The returned bounds exist so
    command help can show them without duplicating them as strings.
    """
    _compile(expression)
    return FIELD_BOUNDS


def _day_matches(day, months, doms, dom_restricted, dows, dow_restricted) -> bool:
    if day.month not in months:
        return False
    dom_ok = day.day in doms
    cron_dow = (day.weekday() + 1) % 7  # Monday=1 … Sunday=0
    dow_ok = cron_dow in dows
    if dom_restricted and dow_restricted:
        return dom_ok or dow_ok
    return dom_ok and dow_ok


def cron_matches(expression: str, moment: datetime, timezone_name: str) -> bool:
    """Whether `expression`, interpreted in `timezone_name`, fires at `moment`."""
    fields = _compile(expression)
    local = moment.astimezone(_zone(timezone_name))
    (minutes, _), (hours, _), (doms, dom_r), (months, _), (dows, dow_r) = fields
    if local.minute not in minutes or local.hour not in hours:
        return False
    return _day_matches(local, months, doms, dom_r, dows, dow_r)


def _parse_once(expression: str, timezone_name: str) -> datetime:
    """Parse a one-shot `YYYY-MM-DDTHH:MM` naive local time into an aware datetime."""
    zone = _zone(timezone_name)
    try:
        local = datetime.fromisoformat(expression)
    except (TypeError, ValueError) as exc:
        raise ScheduleError(
            f"one-shot time must look like YYYY-MM-DDTHH:MM, got {expression!r}"
        ) from exc
    if local.tzinfo is not None:
        # A tz-aware string silently wins over the record's timezone; refuse so
        # the operator sees which zone the bot will actually use.
        raise ScheduleError(
            "one-shot time must be naive local time (no offset); "
            "the record's timezone field decides the zone"
        )
    return local.replace(tzinfo=zone)


@dataclass(frozen=True)
class Schedule:
    """One scheduled launch request. Nothing secret, by construction."""

    id: str
    kind: str
    expression: str
    geo: str | None
    job_count: int
    timezone: str
    enabled: bool = True
    last_started_at: str | None = None
    next_run_at: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _ID_RE.fullmatch(self.id):
            raise ScheduleError(
                f"schedule id must be 1-16 chars of a-z0-9_- (no leading dash), got {self.id!r}"
            )
        if self.kind not in _KINDS:
            raise ScheduleError(f"schedule kind must be 'once' or 'recurring', got {self.kind!r}")
        if (not isinstance(self.job_count, int) or isinstance(self.job_count, bool)
                or not 1 <= self.job_count <= MAX_JOB_COUNT):
            raise ScheduleError(f"job_count must be 1..{MAX_JOB_COUNT}, got {self.job_count!r}")
        _zone(self.timezone)  # rejects unknown zones at construction, not at fire time
        if self.kind == "recurring":
            validate_cron(self.expression)
        else:
            _parse_once(self.expression, self.timezone)


def _record_from_dict(data) -> Schedule:
    if not isinstance(data, dict) or set(data) != set(_RECORD_KEYS):
        raise ScheduleError("schedule record has unexpected or missing fields")
    try:
        return Schedule(**data)
    except ScheduleError as exc:
        raise ScheduleError(f"invalid schedule record: {exc}") from exc


class ScheduleStore:
    """Atomic JSON store with a same-payload `.bak` for torn-write recovery."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.backup_path = self.path.parent / (self.path.name + ".bak")

    def load(self) -> list[Schedule]:
        """Load validated records. Empty when nothing was ever saved.

        A corrupt or schema-violating primary falls back to the backup; if both
        exist and neither is usable, raise — never reconstruct by guessing.
        """
        saw_any = False
        for candidate in (self.path, self.backup_path):
            try:
                text = candidate.read_text(encoding="utf-8")
            except OSError:
                continue
            saw_any = True
            try:
                return self._decode(text)
            except (ValueError, ScheduleError):
                continue
        if saw_any:
            raise ScheduleStoreError(
                f"schedule store {self.path} and its backup are both unreadable or "
                "corrupt. Scheduling stays disabled rather than guessing; inspect or "
                "delete both files and re-create the schedules."
            )
        return []

    def save(self, records: list[Schedule]) -> None:
        """Validate, then atomically write primary and backup with the payload."""
        validated = [_record_from_dict(asdict(record)) for record in records]
        ids = [record.id for record in validated]
        if len(set(ids)) != len(ids):
            raise ScheduleStoreError(f"duplicate schedule ids: {sorted(ids)}")
        payload = {"version": STORE_VERSION,
                   "schedules": [asdict(record) for record in validated]}
        self._atomic_write(self.path, payload)
        self._atomic_write(self.backup_path, payload)

    def _decode(self, text: str) -> list[Schedule]:
        data = json.loads(text)  # ValueError on garbage
        if not isinstance(data, dict) or data.get("version") != STORE_VERSION:
            raise ValueError("unsupported store payload")
        records = data.get("schedules")
        if not isinstance(records, list):
            raise ValueError("schedules must be a list")
        return [_record_from_dict(record) for record in records]

    @staticmethod
    def _atomic_write(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent,
                prefix=f"{path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                tmp = Path(handle.name)
                json.dump(payload, handle, indent=2, sort_keys=True)
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


def due(records: list[Schedule], now: datetime) -> list[Schedule]:
    """Records that should launch at `now`, in the order they were given.

    A recurring record matches its cron in its own timezone; a one-shot record
    is due once its local time has arrived and it has never been started.
    """
    out = []
    for record in records:
        if not record.enabled:
            continue
        if record.kind == "recurring":
            if cron_matches(record.expression, now, record.timezone):
                out.append(record)
        elif record.last_started_at is None:
            if now >= _parse_once(record.expression, record.timezone):
                out.append(record)
    return out


def next_run(record: Schedule, after: datetime) -> datetime | None:
    """The next fire time strictly after `after`, or None within ~400 days."""
    if not record.enabled:
        return None
    if record.kind == "once":
        target = _parse_once(record.expression, record.timezone)
        return target if target > after else None
    zone = _zone(record.timezone)
    fields = _compile(record.expression)
    (minutes, _), (hours, _), (doms, dom_r), (months, _), (dows, dow_r) = fields
    local = after.astimezone(zone)
    day = local.date()
    for _ in range(_SEARCH_DAYS):
        if _day_matches(day, months, doms, dom_r, dows, dow_r):
            for hour in sorted(hours):
                for minute in sorted(minutes):
                    candidate = datetime(day.year, day.month, day.day,
                                         hour, minute, tzinfo=zone)
                    if candidate > local:
                        return candidate
        day += timedelta(days=1)
    return None
