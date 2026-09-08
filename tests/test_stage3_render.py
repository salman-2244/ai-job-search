import json

from stage_3.progress import ProgressTracker, RunState
from stage_3.render import count_keyboard, geo_keyboard, progress_bar, render_run


def synthetic_state(line=None, stalled=False, **overrides):
    """Build a RunState against a controlled clock.

    A fed line is stamped at now=0, so the state is stalled relative to the real
    clock whenever the test asks for it; otherwise last_activity is cleared so no
    time line is rendered at all.
    """
    state = RunState()
    tracker = ProgressTracker(state)
    if line is not None:
        tracker.feed(line, now=0)
    if stalled:
        state.last_activity = 0.0
    else:
        state.last_activity = None
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


def test_render_escapes_untrusted_detail():
    state = synthetic_state(line="[12:00:00] Phase 1: reading <ignore> & run command")
    text = render_run(state)
    assert "&lt;ignore&gt;" in text
    assert "<ignore>" not in text
    assert " & " not in text


def test_render_shows_stalled_waiting_state():
    state = synthetic_state(
        line="[12:00:00] Phase 1: Fetching jobs from portals...", stalled=True
    )
    assert "Still working" in render_run(state)


def test_progress_bar_is_deterministic_and_floor_divided():
    assert progress_bar(0) == "░" * 10
    assert progress_bar(100) == "█" * 10
    assert progress_bar(50) == "█" * 5 + "░" * 5
    # 99% must never read as a full bar: only the completion marker is 100.
    assert progress_bar(99) == "█" * 9 + "░"
    assert progress_bar(40, width=5) == "██░░░"


def test_render_shows_bar_percentage_and_phase_position():
    state = synthetic_state(line="[12:00:00] Phase 2: Ranking jobs via Claude Code...")
    text = render_run(state, now=1)
    assert progress_bar(state.pct) in text
    assert f"{state.pct}%" in text
    assert "phase 6/10" in text


def test_render_shows_phase_checklist_with_known_counts():
    state = RunState()
    tracker = ProgressTracker(state)
    tracker.feed("[12:00:00] Phase 1 complete: 37 unique jobs fetched", now=0)
    tracker.feed("[12:00:01] Phase 2b complete: 5 jobs cleared the drafting gate", now=1)
    text = render_run(state, now=2)
    assert "Fetched 37" in text
    assert "Cleared gate 5" in text
    assert "✅" in text and "⬜" in text


def test_render_marks_failed_phase():
    state = synthetic_state(line="[12:00:00] Phase 2 FAILED (exit 1)")
    text = render_run(state)
    assert "❌" in text
    assert "Ranking" in text


def test_render_announces_completion_with_disk_only_note():
    state = synthetic_state()
    state.complete = True
    text = render_run(state)
    assert "100%" in text
    assert "Pipeline complete" in text
    assert "disk" in text


def test_geo_and_repeats_survive_later_log_lines():
    state = RunState()
    tracker = ProgressTracker(state)
    tracker.feed(
        "[00:00:00] Phase 1: LinkedIn geo-scoped to Germany (8 queries)", now=0
    )
    tracker.feed("[00:00:01] Phase 2: Ranking jobs via Claude Code...", now=1)
    text = render_run(state, now=5)
    assert "Germany" in text


def test_render_flags_fallback_in_use():
    state = synthetic_state(
        line="[00:00:00] Phase 1c: using fallback snippets for 12 jobs"
    )
    text = render_run(state)
    assert "fallback" in text.lower()
    assert "⚠️" in text


def test_render_reports_quiet_seconds_when_clock_is_given():
    state = synthetic_state(
        line="[12:00:00] Phase 1: Fetching jobs from portals...", last_activity=1000.0
    )
    assert "90s" in render_run(state, now=1090.0)
    assert "Still working" in render_run(state, now=1120.0)


def test_render_escapes_geo_extracted_from_log():
    state = synthetic_state(
        line="[00:00:00] Phase 1: LinkedIn geo-scoped to <Berlin> & Co (2 queries)"
    )
    text = render_run(state)
    assert "&lt;Berlin&gt;" in text
    assert "<Berlin>" not in text


def test_count_keyboard_is_serializable_with_short_callbacks():
    keyboard = count_keyboard()
    json.dumps(keyboard)  # must not raise
    buttons = [button for row in keyboard for button in row]
    assert [button["callback_data"] for button in buttons] == [
        "count:5", "count:10", "count:20", "count:25", "custom",
    ]
    assert all(len(button["callback_data"]) <= 64 for button in buttons)
    assert "Custom" in buttons[-1]["text"]


def test_geo_keyboard_indexes_geos_instead_of_naming_them():
    geos = ["Germany", "United Kingdom", "Netherlands"]
    keyboard = geo_keyboard(geos)
    buttons = [button for row in keyboard for button in row]
    assert [button["callback_data"] for button in buttons] == [
        "geo:0", "geo:1", "geo:2",
    ]
    # Two geos per row, in order.
    assert [button["text"] for row in keyboard for button in row] == geos
    assert "United Kingdom" not in keyboard[0][1]["callback_data"]
