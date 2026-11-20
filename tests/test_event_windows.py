import pytest

from mission_control.event_windows import catalyst_state, entry_gate


def test_high_blocks_before_release_and_just_after():
    assert catalyst_state(5, "high") == "BLOCK"
    assert catalyst_state(10, "high") == "BLOCK"
    assert catalyst_state(-3, "high") == "BLOCK"


def test_high_warns_then_confirms_then_recent():
    assert catalyst_state(11, "high") == "WARN"
    assert catalyst_state(60, "high") == "WARN"
    assert catalyst_state(-4, "high") == "COOL"
    assert catalyst_state(-15, "high") == "COOL"
    assert catalyst_state(-16, "high") == "RECENT"


def test_high_far_future_is_upcoming_then_background():
    assert catalyst_state(200, "high") == "UPCOMING"
    assert catalyst_state(300, "high") == "BACKGROUND"


def test_fomc_press_conference_confirms_longer_than_statement():
    assert catalyst_state(-30, "fomc_presser") == "COOL"
    assert catalyst_state(-30, "fomc_statement") == "RECENT"


def test_medium_is_tighter_than_high():
    assert catalyst_state(2, "medium") == "BLOCK"
    assert catalyst_state(4, "medium") == "WARN"
    assert catalyst_state(-2, "medium") == "COOL"
    assert catalyst_state(-6, "medium") == "RECENT"


def test_low_events_never_block():
    for m in (-3, -1, 0, 1, 3):
        assert catalyst_state(m, "low") != "BLOCK"
    assert catalyst_state(5, "low") == "UPCOMING"


def test_unscheduled_headline_confirms_for_15_minutes_only():
    assert catalyst_state(-10, "unscheduled") == "COOL"
    assert catalyst_state(-20, "unscheduled") == "BACKGROUND"


def test_unknown_importance_uses_medium_window():
    assert catalyst_state(2, "mystery") == "BLOCK"


def test_entry_gate_mapping():
    assert entry_gate("BLOCK") == "block"
    assert entry_gate("COOL") == "confirm"
    assert entry_gate("WARN") == "warn"
    assert entry_gate("RECENT") == "open"
