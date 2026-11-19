from datetime import UTC, datetime, date, timedelta

import pytest

from mission_control.indicators.clouds import cloud_state
from mission_control.indicators.display import format_session_open
from mission_control.indicators.vwap import (
    GLOBEX,
    LONDON,
    NEW_YORK,
    TOKYO,
    anchored_vwap,
    latest_session_start,
    session_start_utc,
    session_vwap,
)


def bar(t, h, low, c, v):
    return {"t": t, "h": h, "l": low, "c": c, "v": v}


# ---- Session opens and daylight saving ----

def test_new_york_open_in_standard_time_is_1430_utc():
    # US DST ended 2026-11-01, so 2026-11-18 is EST (UTC-5)
    assert session_start_utc(NEW_YORK, date(2026, 11, 18)) == datetime(2026, 11, 18, 14, 30, tzinfo=UTC)


def test_new_york_open_in_daylight_time_is_1330_utc():
    # US DST began 2026-03-08, so 2026-03-09 is EDT (UTC-4)
    assert session_start_utc(NEW_YORK, date(2026, 3, 9)) == datetime(2026, 3, 9, 13, 30, tzinfo=UTC)


def test_new_york_open_before_us_dst_is_1430_utc():
    assert session_start_utc(NEW_YORK, date(2026, 3, 6)) == datetime(2026, 3, 6, 14, 30, tzinfo=UTC)


def test_london_open_follows_uk_clock():
    # UK DST begins 2026-03-29: 08:00 GMT before, 08:00 BST after
    assert session_start_utc(LONDON, date(2026, 3, 27)) == datetime(2026, 3, 27, 8, 0, tzinfo=UTC)
    assert session_start_utc(LONDON, date(2026, 3, 30)) == datetime(2026, 3, 30, 7, 0, tzinfo=UTC)


def test_tokyo_open_has_no_daylight_shift():
    assert session_start_utc(TOKYO, date(2026, 3, 30)) == datetime(2026, 3, 30, 0, 0, tzinfo=UTC)
    assert session_start_utc(TOKYO, date(2026, 11, 18)) == datetime(2026, 11, 18, 0, 0, tzinfo=UTC)


def test_globex_reset_follows_new_york_clock():
    # 17:00 EST is before the 18:00 reset, so the latest reset is the previous evening
    assert latest_session_start(GLOBEX, datetime(2026, 11, 17, 22, 0, tzinfo=UTC)) == datetime(
        2026, 11, 16, 23, 0, tzinfo=UTC
    )
    assert latest_session_start(GLOBEX, datetime(2026, 11, 17, 23, 30, tzinfo=UTC)) == datetime(
        2026, 11, 17, 23, 0, tzinfo=UTC
    )


def test_latest_session_start_rejects_naive_time():
    with pytest.raises(ValueError):
        latest_session_start(NEW_YORK, datetime(2026, 11, 18, 14, 30))


# ---- VWAP ----

def test_vwap_is_volume_weighted_typical_price():
    bars = [
        bar(datetime(2026, 11, 18, 14, 30, tzinfo=UTC), 10, 8, 9, 100),    # typical 9
        bar(datetime(2026, 11, 18, 14, 35, tzinfo=UTC), 12, 10, 11, 300),  # typical 11
    ]
    result = session_vwap(bars, NEW_YORK)
    assert result[0] == pytest.approx(9.0)
    assert result[1] == pytest.approx(10.5)  # (9*100 + 11*300) / 400


def test_vwap_resets_at_session_open():
    bars = [
        bar(datetime(2026, 11, 17, 14, 30, tzinfo=UTC), 10, 8, 9, 100),
        bar(datetime(2026, 11, 17, 14, 35, tzinfo=UTC), 12, 10, 11, 300),
        bar(datetime(2026, 11, 18, 14, 30, tzinfo=UTC), 20, 18, 19, 50),   # new session
    ]
    result = session_vwap(bars, NEW_YORK)
    assert result[1] == pytest.approx(10.5)
    assert result[2] == pytest.approx(19.0)  # reset: prior session not carried in


def test_zero_volume_returns_none():
    bars = [bar(datetime(2026, 11, 18, 14, 30, tzinfo=UTC), 10, 8, 9, 0)]
    assert session_vwap(bars, NEW_YORK) == [None]


def test_anchored_vwap_starts_at_anchor():
    bars = [
        bar(datetime(2026, 11, 18, 14, 30, tzinfo=UTC), 10, 8, 9, 100),
        bar(datetime(2026, 11, 18, 14, 35, tzinfo=UTC), 12, 10, 11, 300),
        bar(datetime(2026, 11, 18, 14, 40, tzinfo=UTC), 14, 12, 13, 100),
    ]
    result = anchored_vwap(bars, datetime(2026, 11, 18, 14, 35, tzinfo=UTC))
    assert result[0] is None
    assert result[1] == pytest.approx(11.0)
    assert result[2] == pytest.approx(11.5)  # (11*300 + 13*100) / 400


def test_session_vwap_rejects_out_of_order_bars():
    bars = [
        bar(datetime(2026, 11, 18, 14, 35, tzinfo=UTC), 12, 10, 11, 300),
        bar(datetime(2026, 11, 18, 14, 30, tzinfo=UTC), 10, 8, 9, 100),
    ]
    with pytest.raises(ValueError):
        session_vwap(bars, NEW_YORK)


# ---- Cloud states ----

def test_cloud_is_bullish_on_rising_series():
    closes = [float(i) for i in range(1, 81)]
    assert cloud_state(closes, 8, 21)[-1] == 1


def test_cloud_is_bearish_on_falling_series():
    closes = [float(200 - i) for i in range(80)]
    assert cloud_state(closes, 8, 21)[-1] == -1


def test_cloud_is_neutral_on_flat_series():
    closes = [100.0] * 80
    assert cloud_state(closes, 8, 21)[-1] == 0


def test_cloud_is_none_during_warmup():
    closes = [float(i) for i in range(1, 11)]
    assert all(state is None for state in cloud_state(closes, 8, 21))


def test_cloud_rejects_fast_not_shorter_than_slow():
    with pytest.raises(ValueError):
        cloud_state([1.0] * 80, 21, 8)


# ---- Display labels ----

def test_london_open_label_matches_agreed_format():
    # 07:00 UTC on 2026-03-30 = 8:00 AM London (BST) = 3:00 AM New York (EDT)
    main, faded = format_session_open(datetime(2026, 3, 30, 7, 0, tzinfo=UTC), "Europe/London", "London")
    assert main == "3:00 AM ET/NY"
    assert faded == "(8:00 AM London)"


def test_tokyo_open_label_shows_previous_day_in_new_york():
    # 00:00 UTC on 2026-11-18 = 9:00 AM Tokyo = 7:00 PM ET on 2026-11-17
    main, faded = format_session_open(datetime(2026, 11, 18, 0, 0, tzinfo=UTC), "Asia/Tokyo", "Tokyo")
    assert main == "7:00 PM ET/NY, previous day"
    assert faded == "(9:00 AM Tokyo)"


def test_display_rejects_naive_time():
    with pytest.raises(ValueError):
        format_session_open(datetime(2026, 11, 18, 0, 0), "Asia/Tokyo", "Tokyo")
