import math

import pytest

from mission_control.trade_tracker import (
    StopPlan,
    TargetPlan,
    apply_bar,
    move_stop,
    open_trade,
    style_for,
)


def bar(h, low):
    return {"h": h, "l": low}


def test_default_targets_are_one_one_point_five_three_r():
    assert TargetPlan().multiples == (1.0, 1.5, 3.0)


def test_plan_rejects_shares_not_adding_to_one():
    with pytest.raises(ValueError):
        TargetPlan(splits=(0.5, 0.5, 0.5))


def test_plan_rejects_non_increasing_targets():
    with pytest.raises(ValueError):
        TargetPlan(multiples=(1.0, 1.0, 3.0), splits=(1 / 3, 1 / 3, 1 / 3))


def test_stop_plan_rejects_zero_trail_distance():
    with pytest.raises(ValueError):
        StopPlan(trail_r=0.0)


def test_long_targets_are_r_multiples_above_entry():
    trade = open_trade("MNQ", "long", entry=100.0, stop=99.0)
    assert trade.targets == pytest.approx((101.0, 101.5, 103.0))


def test_short_targets_are_r_multiples_below_entry():
    trade = open_trade("MNQ", "short", entry=100.0, stop=101.0)
    assert trade.targets == pytest.approx((99.0, 98.5, 97.0))


def test_stop_must_be_on_the_losing_side():
    with pytest.raises(ValueError):
        open_trade("MNQ", "long", entry=100.0, stop=101.0)
    with pytest.raises(ValueError):
        open_trade("MNQ", "short", entry=100.0, stop=99.0)


def test_stop_is_checked_before_target_on_same_bar():
    trade = open_trade("MNQ", "long", entry=100.0, stop=99.0)
    trade, events = apply_bar(trade, bar(h=101.5, low=98.9))
    assert events == [("STOP_HIT", None)]
    assert trade.closed is True
    assert trade.realized_r == pytest.approx(-1.0)


def test_breakeven_after_first_target_then_stop_hit_keeps_one_third():
    trade = open_trade("MNQ", "long", entry=100.0, stop=99.0)
    trade, events = apply_bar(trade, bar(h=101.2, low=100.5))
    assert ("TARGET_HIT", 1) in events
    assert ("BREAKEVEN_STOP", None) in events
    assert trade.stop == pytest.approx(100.0)
    trade, events = apply_bar(trade, bar(h=100.4, low=99.9))
    assert events == [("STOP_HIT", None)]
    assert trade.realized_r == pytest.approx(1 / 3, abs=1e-6)


def test_full_ladder_closes_trade_with_expected_r():
    trade = open_trade("MNQ", "long", entry=100.0, stop=99.0)
    trade, _ = apply_bar(trade, bar(h=101.2, low=100.5))
    trade, _ = apply_bar(trade, bar(h=101.5, low=100.5))
    trade, events = apply_bar(trade, bar(h=103.0, low=101.0))
    assert events == [("TARGET_FINAL", 3)]
    assert trade.closed is True
    assert trade.realized_r == pytest.approx(1 / 3 * 1 + 1 / 3 * 1.5 + 1 / 3 * 3)


def test_trailing_stop_locks_in_profit_when_stopped():
    stop_plan = StopPlan(breakeven_after_hits=None, trail_after_hits=1, trail_r=1.0)
    trade = open_trade("MNQ", "long", entry=100.0, stop=99.0, stop_plan=stop_plan)
    trade, events = apply_bar(trade, bar(h=101.2, low=100.5))
    assert ("STOP_TRAILED", None) in events
    assert trade.stop == pytest.approx(100.2)  # best 101.2 minus 1R
    trade, events = apply_bar(trade, bar(h=100.4, low=100.1))
    assert events == [("STOP_HIT", None)]
    # 1/3 banked at target 1, remaining 2/3 stopped at +0.2R
    assert trade.realized_r == pytest.approx(1 / 3 + 2 / 3 * 0.2)


def test_trailing_stop_never_moves_backward():
    stop_plan = StopPlan(breakeven_after_hits=None, trail_after_hits=1, trail_r=1.0)
    trade = open_trade("MNQ", "long", entry=100.0, stop=99.0, stop_plan=stop_plan)
    trade, _ = apply_bar(trade, bar(h=101.5, low=100.5))
    high_stop = trade.stop
    trade, _ = apply_bar(trade, bar(h=101.0, low=100.6))
    assert trade.stop == pytest.approx(high_stop)


def test_short_trailing_stop_follows_price_down():
    stop_plan = StopPlan(breakeven_after_hits=None, trail_after_hits=1, trail_r=1.0)
    trade = open_trade("MNQ", "short", entry=100.0, stop=101.0, stop_plan=stop_plan)
    trade, events = apply_bar(trade, bar(h=99.5, low=98.8))
    assert ("STOP_TRAILED", None) in events
    assert trade.stop == pytest.approx(99.8)  # best 98.8 plus 1R
    trade, events = apply_bar(trade, bar(h=99.9, low=99.5))
    assert events == [("STOP_HIT", None)]
    assert trade.realized_r == pytest.approx(1 / 3 + 2 / 3 * 0.2)


def test_manual_move_can_tighten_but_not_widen():
    trade = open_trade("MNQ", "long", entry=100.0, stop=99.0)
    trade, events = move_stop(trade, 99.5)
    assert trade.stop == pytest.approx(99.5)
    assert events == [("STOP_MOVED", None)]
    with pytest.raises(ValueError):
        move_stop(trade, 98.0)


def test_closed_trade_rejects_manual_move():
    trade = open_trade("MNQ", "long", entry=100.0, stop=99.0)
    trade, _ = apply_bar(trade, bar(h=99.0, low=98.0))
    with pytest.raises(ValueError):
        move_stop(trade, 99.5)


def test_closed_trade_ignores_further_bars():
    trade = open_trade("MNQ", "long", entry=100.0, stop=99.0)
    trade, _ = apply_bar(trade, bar(h=99.0, low=98.0))
    again, events = apply_bar(trade, bar(h=110.0, low=90.0))
    assert events == []
    assert again == trade


def test_nonfinite_bar_is_rejected():
    trade = open_trade("MNQ", "long", entry=100.0, stop=99.0)
    with pytest.raises(ValueError):
        apply_bar(trade, bar(h=math.nan, low=99.0))


def test_message_colors_match_the_agreed_palette():
    assert style_for("FORMING") == ("FORMING", "amber")
    assert style_for("ENTRY_LONG") == ("ENTRY SIGNAL · LONG", "green")
    assert style_for("ENTRY_SHORT") == ("ENTRY SIGNAL · SHORT", "red")
    assert style_for("TARGET_HIT", 1) == ("TARGET 1 HIT", "green")
    assert style_for("TARGET_FINAL", 3) == ("TARGET 3 HIT · TRADE CLOSED", "green")
    assert style_for("STOP_HIT") == ("STOP HIT", "red")
    assert style_for("STOP_TRAILED") == ("STOP TRAILED", "blue")
    assert style_for("INVALIDATED") == ("EXIT · SETUP INVALIDATED", "orange")
