import json
import stat
from datetime import datetime, timezone

import pytest

from stage_3.schedules import (
    Schedule,
    ScheduleStore,
    ScheduleStoreError,
    cron_matches,
    due,
    next_run,
    validate_cron,
)


def synthetic_schedule(sid="s17", **overrides):
    defaults = dict(
        id=sid,
        kind="recurring",
        expression="0 8 * * 1-5",
        geo=None,
        job_count=10,
        timezone="UTC",
        enabled=True,
        last_started_at=None,
        next_run_at=None,
    )
    defaults.update(overrides)
    return Schedule(**defaults)


# -- cron validation and matching -----------------------------------------


def test_weekday_cron_matches_local_time():
    # 2026-09-08 is a Tuesday; the brief's exact example.
    assert cron_matches("0 8 * * 1-5", datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc), "UTC")
    assert not cron_matches("0 8 * * 1-5", datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc), "UTC")
    assert not cron_matches("0 8 * * 6,0", datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc), "UTC")


def test_validate_cron_accepts_steps_lists_and_ranges():
    assert validate_cron("*/15 0-6/2 1,15 */3 1-5") == (59, 23, 31, 12, 7)
    assert validate_cron("* * * * *") == (59, 23, 31, 12, 7)


@pytest.mark.parametrize("bad", [
    "", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *", "* * 0 * *",
    "* * 32 * *", "* * * 0 *", "* * * 13 *", "* * * * 8", "a * * * *",
    "* * * * 1--2", "*/0 * * * *", "5-1 * * * *", "5, * * * *", "-5 * * * *",
])
def test_validate_cron_rejects_malformed_or_out_of_range(bad):
    with pytest.raises(ValueError):
        validate_cron(bad)


def test_step_and_list_matching():
    expr = "*/15 8 * * *"
    assert cron_matches(expr, datetime(2026, 9, 8, 8, 45, tzinfo=timezone.utc), "UTC")
    assert not cron_matches(expr, datetime(2026, 9, 8, 8, 50, tzinfo=timezone.utc), "UTC")
    assert cron_matches("0 8,18 * * *", datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc), "UTC")


def test_dow_seven_means_sunday():
    sunday = datetime(2026, 9, 13, 8, 0, tzinfo=timezone.utc)
    assert cron_matches("0 8 * * 7", sunday, "UTC")
    assert cron_matches("0 8 * * 0", sunday, "UTC")


def test_dom_and_dow_are_ored_when_both_restricted():
    # Vixie-cron semantics: with both fields restricted, either may match.
    # 2026-09-13 is a Sunday.
    sunday = datetime(2026, 9, 13, 8, 0, tzinfo=timezone.utc)
    assert cron_matches("0 8 13 * 1", sunday, "UTC")       # dom hits, dow misses → match
    assert cron_matches("0 8 * * 0", sunday, "UTC")        # dow alone restricted
    assert not cron_matches("0 8 14 * 1", sunday, "UTC")   # neither hits
    assert not cron_matches("0 8 14 * 2", sunday, "UTC")   # dow misses, dom misses


def test_timezone_is_honoured():
    # 08:00 Berlin is 06:00 UTC in September (CEST, UTC+2).
    assert cron_matches("0 8 * * *", datetime(2026, 9, 8, 6, 0, tzinfo=timezone.utc), "Europe/Berlin")
    assert not cron_matches("0 8 * * *", datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc), "Europe/Berlin")


def test_cron_matches_rejects_bad_expression_or_zone():
    moment = datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        cron_matches("nonsense", moment, "UTC")
    with pytest.raises(ValueError):
        cron_matches("0 8 * * *", moment, "Mars/Olympus")


# -- record construction ----------------------------------------------------


def test_invalid_schedule_fields_are_rejected_at_construction():
    with pytest.raises(ValueError):
        synthetic_schedule(kind="weekly")
    with pytest.raises(ValueError):
        synthetic_schedule(expression="not cron")
    with pytest.raises(ValueError):
        synthetic_schedule(job_count=0)
    with pytest.raises(ValueError):
        synthetic_schedule(job_count=51)
    with pytest.raises(ValueError):
        synthetic_schedule(timezone="Mars/Olympus")
    with pytest.raises(ValueError):
        synthetic_schedule(id="../evil")
    with pytest.raises(ValueError):
        synthetic_schedule(kind="once", expression="2026-13-40T99:00")
    with pytest.raises(ValueError):
        synthetic_schedule(kind="once", expression="2026-09-08T08:00:00+02:00")  # must be naive


# -- store ------------------------------------------------------------------


def test_corrupt_primary_recovers_backup(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    record = synthetic_schedule("s17")
    store.save([record])
    store.path.write_text("{broken")
    assert store.load() == [record]


def test_tampered_primary_falls_back_to_backup(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    record = synthetic_schedule("s3")
    store.save([record])
    payload = json.loads(store.path.read_text())
    payload["schedules"][0]["job_count"] = 9999
    store.path.write_text(json.dumps(payload))
    assert store.load() == [record]


def test_both_corrupt_raises_and_never_guesses(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    store.save([synthetic_schedule("s1")])
    store.path.write_text("{broken")
    store.backup_path.write_text("also broken")
    with pytest.raises(ScheduleStoreError):
        store.load()


def test_missing_store_loads_empty(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    assert store.load() == []
    assert not store.path.exists()


def test_save_is_atomic_owner_only_and_leaves_no_temp(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    store.save([synthetic_schedule("s2")])
    data = json.loads(store.path.read_text())
    assert data["version"] == 1
    assert data["schedules"][0]["id"] == "s2"
    for path in (store.path, store.backup_path):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob("*.tmp"))
    assert store.load() == [synthetic_schedule("s2")]


def test_saved_payload_holds_only_non_secret_fields(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    store.save([synthetic_schedule("s9", geo="Germany")])
    record = json.loads(store.path.read_text())["schedules"][0]
    assert set(record) == {
        "id", "kind", "expression", "geo", "job_count", "timezone",
        "enabled", "last_started_at", "next_run_at",
    }
    assert "token" not in json.dumps(record).lower()


def test_save_rejects_duplicate_ids(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    with pytest.raises(ScheduleStoreError):
        store.save([synthetic_schedule("dup"), synthetic_schedule("dup")])


def test_load_rejects_unknown_top_level_shapes(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    store.path.write_text(json.dumps({"schedules": []}))  # no version key
    with pytest.raises(ScheduleStoreError):
        store.load()


# -- due() and next_run() ---------------------------------------------------


def test_due_returns_enabled_recurring_matches_and_fired_once_records():
    now = datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc)
    hit = synthetic_schedule("s1")
    other_hour = synthetic_schedule("s2", expression="0 9 * * 1-5")
    disabled = synthetic_schedule("s3", enabled=False)
    once_past = synthetic_schedule("s4", kind="once", expression="2026-09-08T07:00")
    once_future = synthetic_schedule("s5", kind="once", expression="2026-09-08T09:00")
    assert due([hit, other_hour, disabled, once_past, once_future], now) == [hit, once_past]


def test_once_schedule_uses_its_own_timezone():
    record = synthetic_schedule(kind="once", expression="2026-09-08T09:00",
                                timezone="Europe/Berlin")  # = 07:00 UTC
    now = datetime(2026, 9, 8, 7, 0, tzinfo=timezone.utc)
    assert due([record], now) == [record]


def test_already_started_once_schedule_is_not_due_again():
    record = synthetic_schedule(kind="once", expression="2026-09-08T07:00",
                                last_started_at="2026-09-08T07:00:00+02:00")
    now = datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc)
    assert due([record], now) == []


def test_next_run_finds_the_following_weekday_morning():
    record = synthetic_schedule(expression="0 8 * * 1-5")
    after = datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc)  # Tuesday 09:00
    assert next_run(record, after) == datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc)


def test_next_run_for_once_and_disabled_records():
    once = synthetic_schedule(kind="once", expression="2026-09-08T09:00")
    after = datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc)
    assert next_run(once, after) == datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc)
    past = synthetic_schedule(kind="once", expression="2026-09-08T07:00")
    assert next_run(past, after) is None
    disabled = synthetic_schedule(enabled=False)
    assert next_run(disabled, after) is None
