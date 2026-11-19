from datetime import UTC, datetime, timedelta

import pytest

from mission_control.indicators.closed_bars import closed_bars
from mission_control.indicators.ema import ema, is_warm


def test_ema_seed_and_recursion_match_hand_calculation():
    # Period 3: seed = (1+2+3)/3 = 2.0, alpha = 0.5
    result = ema([1, 2, 3, 4, 5], 3)
    assert result[:2] == [None, None]
    assert result[2] == pytest.approx(2.0)
    assert result[3] == pytest.approx(3.0)   # 0.5*4 + 0.5*2
    assert result[4] == pytest.approx(4.0)   # 0.5*5 + 0.5*3


def test_ema_short_input_returns_all_none():
    assert ema([1.0, 2.0], 5) == [None, None]


def test_is_warm_requires_three_periods_by_default():
    assert not is_warm(8, 3)
    assert is_warm(9, 3)


def test_forming_bar_is_dropped():
    tf = timedelta(minutes=5)
    bars = [
        {"t": datetime(2026, 11, 18, 14, 50, tzinfo=UTC), "c": 1.0},
        {"t": datetime(2026, 11, 18, 14, 55, tzinfo=UTC), "c": 2.0},  # closes 15:00
    ]
    # At 14:59:59 the 14:55 bar is still forming
    kept = closed_bars(bars, datetime(2026, 11, 18, 14, 59, 59, tzinfo=UTC), tf)
    assert [b["c"] for b in kept] == [1.0]
    # At 15:00:00 it has closed
    kept = closed_bars(bars, datetime(2026, 11, 18, 15, 0, 0, tzinfo=UTC), tf)
    assert [b["c"] for b in kept] == [1.0, 2.0]


def test_naive_timestamps_are_rejected():
    tf = timedelta(minutes=5)
    with pytest.raises(ValueError):
        closed_bars([{"t": datetime(2026, 11, 18, 14, 50), "c": 1.0}],
                    datetime(2026, 11, 18, 15, 0, tzinfo=UTC), tf)
