"""Tests for the Webull GEX adapter. No test touches the network."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from mission_control.gex import GexUnavailable, calculate_gex
from mission_control.gex_sources import (
    RATIO_BANDS,
    active_session,
    build_payload,
    expiry_choices,
    option_row_to_cboe,
    settlement_time,
)

ET = ZoneInfo("America/New_York")


def at(y, m, d, h=10, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=ET)


def occ(root, expiry, cp, strike):
    return f"{root}{expiry:%y%m%d}{cp}{int(round(strike * 1000)):08d}"


def chain(root, expiry, strikes, iv="28.76%", oi=1000):
    rows = []
    for strike in strikes:
        for cp in ("C", "P"):
            rows.append({"symbol": occ(root, expiry, cp, strike), "open_interest": oi, "implied_volatility": iv})
    return rows


# --- field and unit handling -------------------------------------------------

def test_percent_string_and_plain_percent_both_convert_to_decimal():
    base = {"symbol": "QQQ261218C00660000", "open_interest": 10}
    assert option_row_to_cboe({**base, "implied_volatility": "28.76%"})["iv"] == pytest.approx(0.2876)
    assert option_row_to_cboe({**base, "implied_volatility": 28.76})["iv"] == pytest.approx(0.2876)


def test_decimal_iv_is_kept_as_decimal():
    base = {"symbol": "QQQ261218C00660000", "open_interest": 10}
    assert option_row_to_cboe({**base, "implied_volatility": 0.2876})["iv"] == pytest.approx(0.2876)


def test_missing_open_interest_fails_closed():
    row = {"symbol": "QQQ261218C00660000", "implied_volatility": "28.76%"}
    with pytest.raises(GexUnavailable, match="open_interest"):
        option_row_to_cboe(row)


def test_missing_iv_fails_closed():
    row = {"symbol": "QQQ261218C00660000", "open_interest": 10}
    with pytest.raises(GexUnavailable, match="'iv'"):
        option_row_to_cboe(row)


def test_bad_symbol_format_is_rejected():
    with pytest.raises(GexUnavailable, match="Unrecognised"):
        option_row_to_cboe({"symbol": "QQQ-DEC18-660", "open_interest": 1, "implied_volatility": "20%"})


def test_row_from_wrong_root_is_rejected():
    expiry = date(2026, 12, 18)
    rows = [{"symbol": occ("SPY", expiry, "C", 660), "open_interest": 1, "implied_volatility": "20%"}]
    with pytest.raises(GexUnavailable, match="does not belong to QQQ"):
        build_payload(rows, spot=661.75, ticker="QQQ")


def test_empty_chain_is_rejected():
    with pytest.raises(GexUnavailable, match="No option rows"):
        build_payload([], spot=661.75, ticker="QQQ")


# --- end-to-end through the unchanged gex.py math ----------------------------

def test_qqq_payload_runs_through_calculate_gex_with_etf_ratio_band():
    expiry = datetime.now(ET).date() + timedelta(days=5)
    rows = chain("QQQ", expiry, [655, 660, 665, 670])
    payload = build_payload(rows, spot=661.75, ticker="QQQ")
    result = calculate_gex(
        payload,
        27390.0,
        price_source="test",
        cache_status="fresh",
        cache_age_seconds=0.0,
        retrieved_at="2026-11-16T10:00:00-05:00",
        ratio_band=RATIO_BANDS["QQQ"],
    )
    assert "net_gex_per_1pct_move" in result
    assert "call_wall_futures" in result


def test_qqq_ratio_is_rejected_by_the_index_band():
    """Guards the bug: an ETF ratio of about 41 must not pass the NDX band."""
    expiry = datetime.now(ET).date() + timedelta(days=5)
    payload = build_payload(chain("QQQ", expiry, [660]), spot=661.75, ticker="QQQ")
    with pytest.raises(GexUnavailable, match="validation band"):
        calculate_gex(
            payload, 27390.0, price_source="test", cache_status="fresh",
            cache_age_seconds=0.0, retrieved_at="x", ratio_band=RATIO_BANDS["NDX"],
        )


# --- settlement and expiry logic ---------------------------------------------

def test_am_settled_roots_settle_at_the_open():
    assert settlement_time("NDX", date(2026, 12, 18)).time() == datetime(2026, 1, 1, 9, 30).time()


def test_pm_settled_roots_settle_at_the_close():
    assert settlement_time("NDXP", date(2026, 12, 18)).time() == datetime(2026, 1, 1, 16, 0).time()
    assert settlement_time("QQQ", date(2026, 12, 18)).time() == datetime(2026, 1, 1, 16, 0).time()


def test_zero_dte_is_today_before_the_close():
    # Monday Nov 16, 2026 at 10:00 AM New York
    assert active_session(at(2026, 11, 16, 10)) == date(2026, 11, 16)


def test_zero_dte_rolls_to_next_session_after_the_close():
    # Monday Nov 16, 2026 at 5:00 PM New York
    assert active_session(at(2026, 11, 16, 17)) == date(2026, 11, 17)


def test_thanksgiving_is_skipped():
    # Wednesday Nov 25, 2026 at 5:00 PM: next session is Friday Nov 27, not Thanksgiving Nov 26
    assert active_session(at(2026, 11, 25, 17)) == date(2026, 11, 27)


def test_four_dropdown_choices_on_monday_morning():
    choices = expiry_choices(at(2026, 11, 16, 10))
    assert choices["0DTE"] == date(2026, 11, 16)
    assert choices["next_trading_day"] == date(2026, 11, 17)
    assert choices["this_week"] == date(2026, 11, 20)
    assert choices["next_week"] == date(2026, 11, 27)


def test_weekend_default_is_monday():
    # Saturday Nov 21, 2026
    assert active_session(at(2026, 11, 21, 12)) == date(2026, 11, 23)
