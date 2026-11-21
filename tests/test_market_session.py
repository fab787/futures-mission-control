from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from mission_control import market_session as ms

NY = ZoneInfo("America/New_York")


def at(*args):
    return datetime(*args, tzinfo=NY)


def test_friday_afternoon_is_open_post_cash():
    s = ms.globex_status(at(2026, 11, 20, 16, 59))
    assert s["status"] == "open" and s["session"] == "post_cash"


def test_friday_close_starts_weekend():
    s = ms.globex_status(at(2026, 11, 20, 17, 0))
    assert s["status"] == "closed"
    assert s["next_open"].startswith("2026-11-22T18:00:00")


def test_saturday_is_closed_until_sunday_evening():
    s = ms.globex_status(at(2026, 11, 21, 12, 0))
    assert s["status"] == "closed"
    assert s["next_open"].startswith("2026-11-22T18:00:00")


def test_sunday_before_open_is_closed():
    assert ms.globex_status(at(2026, 11, 22, 17, 59))["status"] == "closed"


def test_sunday_evening_opens_overnight():
    s = ms.globex_status(at(2026, 11, 22, 18, 0))
    assert s["status"] == "open" and s["session"] == "overnight"


def test_weekday_maintenance_halt():
    s = ms.globex_status(at(2026, 11, 23, 17, 30))
    assert s["status"] == "halt"
    assert s["next_open"].startswith("2026-11-23T18:00:00")


def test_london_and_us_cash_sessions():
    assert ms.globex_status(at(2026, 11, 23, 4, 30))["session"] == "london"
    assert ms.globex_status(at(2026, 11, 23, 10, 0))["session"] == "us_cash"


def test_holiday_closes_full_day(monkeypatch):
    monkeypatch.setattr(ms, "HOLIDAYS", {date(2026, 11, 23)})
    assert ms.globex_status(at(2026, 11, 23, 10, 0))["status"] == "closed"


def test_naive_time_is_rejected():
    with pytest.raises(ValueError):
        ms.globex_status(datetime(2026, 11, 23, 10, 0))


def test_missing_arm_file_is_disarmed(tmp_path):
    assert ms.read_arm_state(tmp_path / "arm.json")["armed"] is False


def test_arm_state_round_trip(tmp_path):
    p = tmp_path / "arm.json"
    ms.write_arm_state(True, p, now=at(2026, 11, 20, 10, 0))
    assert ms.read_arm_state(p)["armed"] is True
    ms.write_arm_state(False, p, now=at(2026, 11, 20, 10, 1))
    assert ms.read_arm_state(p)["armed"] is False


def test_disarmed_blocks_pulls_even_when_market_is_open(tmp_path):
    ok, why = ms.auto_pull_allowed(at(2026, 11, 23, 10, 0), tmp_path / "arm.json")
    assert ok is False and "Disarmed" in why


def test_armed_allows_pulls_only_while_open(tmp_path):
    p = tmp_path / "arm.json"
    ms.write_arm_state(True, p, now=at(2026, 11, 20, 10, 0))
    assert ms.auto_pull_allowed(at(2026, 11, 23, 10, 0), p)[0] is True
    assert ms.auto_pull_allowed(at(2026, 11, 21, 10, 0), p)[0] is False