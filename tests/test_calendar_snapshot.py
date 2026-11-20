from datetime import UTC, date, datetime

import pytest

from mission_control.calendar_snapshot import (
    build_snapshot,
    classify_event,
    daily_window,
    load_latest,
    weekly_window,
    write_snapshot,
)

WIN_START, WIN_END = date(2026, 11, 23), date(2026, 11, 29)
PULLED = datetime(2026, 11, 20, 21, 10, tzinfo=UTC)


def raw(name, publish_date="2026-11-25", publish_time="2026-11-25T13:30:00.000+0000", fed=False):
    return {"event_name": name, "publish_date": publish_date, "publish_time": publish_time, "is_fed_related": fed}


def test_friday_pull_covers_next_monday_through_sunday():
    assert weekly_window(date(2026, 11, 20)) == (date(2026, 11, 23), date(2026, 11, 29))


def test_daily_pull_covers_today_plus_six_days():
    assert daily_window(date(2026, 11, 20)) == (date(2026, 11, 20), date(2026, 11, 26))


def test_classes_for_scalping_windows():
    assert classify_event("Consumer Price Index (CPI) YoY") == "high"
    assert classify_event("Nonfarm Payrolls") == "high"
    assert classify_event("FOMC Rate Decision") == "fomc_statement"
    assert classify_event("FOMC Press Conference") == "fomc_presser"
    assert classify_event("FOMC Minutes Release") == "low"
    assert classify_event("PPI") == "medium"
    assert classify_event("Fed Speaker Waller", is_fed_related=True) == "medium"
    assert classify_event("Pork Export Sales Net") == "low"


def test_unconfirmed_time_is_kept_but_marked_unconfirmed():
    snap = build_snapshot([raw("CPI", publish_time=None)], window_start=WIN_START, window_end=WIN_END,
                          pulled_at=PULLED, status="fresh")
    event = snap["events"][0]
    assert event["confirmed"] is False
    assert event["time_utc"] is None
    assert event["importance"] == "high"


def test_duplicate_rows_collapse_to_one_event():
    snap = build_snapshot([raw("CPI"), raw("CPI")], window_start=WIN_START, window_end=WIN_END,
                          pulled_at=PULLED, status="fresh")
    assert snap["event_count"] == 1


def test_events_outside_the_window_are_dropped():
    snap = build_snapshot([raw("CPI", publish_date="2026-12-03")], window_start=WIN_START, window_end=WIN_END,
                          pulled_at=PULLED, status="fresh")
    assert snap["event_count"] == 0


def test_unavailable_snapshot_is_never_written(tmp_path):
    snap = build_snapshot([], window_start=WIN_START, window_end=WIN_END, pulled_at=PULLED, status="unavailable")
    with pytest.raises(ValueError):
        write_snapshot(snap, tmp_path)


def test_partial_snapshot_is_written_and_loaded_as_partial(tmp_path):
    snap = build_snapshot([raw("CPI")], window_start=WIN_START, window_end=WIN_END, pulled_at=PULLED,
                          status="partial", failures=["2026-11-24: HTTP error"])
    write_snapshot(snap, tmp_path)
    latest = load_latest(tmp_path, datetime(2026, 11, 20, 22, 0, tzinfo=UTC))
    assert latest["status"] == "partial"
    assert latest["event_count"] == 1


def test_old_snapshot_is_marked_stale(tmp_path):
    snap = build_snapshot([raw("CPI")], window_start=WIN_START, window_end=WIN_END, pulled_at=PULLED, status="fresh")
    write_snapshot(snap, tmp_path)
    latest = load_latest(tmp_path, datetime(2026, 11, 22, 0, 0, tzinfo=UTC))
    assert latest["status"] == "stale"


def test_missing_snapshot_reports_not_loaded(tmp_path):
    assert load_latest(tmp_path, PULLED)["status"] == "not_loaded"
