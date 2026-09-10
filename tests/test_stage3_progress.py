from stage_3.progress import ProgressTracker, parse_log_line, replay


def test_long_phase_id_is_not_truncated():
    tracker = ProgressTracker()
    tracker.feed("[12:00:00] Phase 1b-final complete: 4 jobs cleared")
    assert tracker.state.current_phase == "1b-final"
    assert tracker.state.phases["1b-final"].status == "done"


def test_running_phase_is_half_weight_and_never_reaches_100():
    state = replay("[12:00:00] Phase 0b complete\n[12:00:01] Phase 1 running")
    assert state.pct == 15
    assert state.pct < 100


def test_pipeline_marker_is_the_only_100_percent_state():
    assert replay("[12:00:00] Phase 5 complete\n").pct == 99
    assert replay("[12:00:00] Pipeline complete.\n").pct == 100


def test_later_phase_implicitly_completes_earlier_pending_phases():
    state = replay("[12:00:00] Phase 2: Ranking jobs via Claude Code...\n")
    assert all(
        state.phases[phase].status == "done"
        for phase in ("0b", "1", "1b", "1c", "1b-final")
    )
    assert state.phases["2"].status == "running"


def test_status_and_count_are_parsed_from_real_pipeline_lines():
    tracker = ProgressTracker()
    tracker.feed("[12:00:00] Phase 1 complete: 37 unique jobs fetched", now=10)
    assert tracker.state.phases["1"].count == 37
    tracker.feed("[12:00:01] Phase 2 FAILED (exit 1)", now=11)
    assert tracker.state.phases["2"].status == "failed"
    tracker.feed("[12:00:02] Phase 3: skipped (no ranked jobs to offer)", now=12)
    assert tracker.state.phases["3"].status == "skipped"


def test_only_meaningful_lines_update_last_activity_and_stalled_state():
    tracker = ProgressTracker()
    tracker.feed("unstructured child output", now=10)
    assert tracker.state.last_activity is None
    tracker.feed("[12:00:00] Phase 1: Fetching jobs from portals...", now=20)
    assert tracker.state.last_activity == 20
    assert tracker.state.stalled(139) is False
    assert tracker.state.stalled(140) is True


def test_parser_keeps_timestamp_message_and_recognizes_waiting_activity():
    parsed = parse_log_line("[12:34:56]   Phase 2 still running... (120s elapsed)")
    assert parsed is not None
    assert parsed.timestamp == "12:34:56"
    assert parsed.phase_id == "2"
    assert parsed.status == "running"


def test_replay_preserves_last_meaningful_message_and_counts():
    state = replay(
        "noise\n"
        "[12:00:00] Phase 2b complete: 5 jobs cleared the drafting gate\n"
        "[12:00:01] unrelated output\n"
    )
    assert state.current_phase == "2b"
    assert state.phases["2b"].count == 5
    assert "5 jobs cleared" in state.message
