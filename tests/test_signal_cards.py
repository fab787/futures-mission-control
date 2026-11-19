from dataclasses import asdict
from datetime import UTC, datetime

import pytest

from mission_control.signal_cards import SignalCard, build_card, decide_action

BAR = datetime(2026, 11, 18, 14, 30, tzinfo=UTC)  # 9:30 AM ET


def _card(**overrides):
    values = dict(
        symbol="MNQ", setup_state="TRIGGERED", direction="long", bar_time_utc=BAR,
        entry_reference=27412.0, invalidation_level=27398.0, target_reference=27450.0,
        score=72, reasons=["Both clouds bullish"], data_tier="full", delay_minutes=0.0,
    )
    values.update(overrides)
    return build_card(**values)


def test_triggered_full_fresh_long_is_buy():
    assert _card().action == "BUY"


def test_triggered_full_fresh_short_is_sell():
    assert _card(direction="short").action == "SELL"


def test_triggered_but_delayed_is_hold_with_reason():
    card = _card(delay_minutes=2.0)
    assert card.action == "HOLD"
    assert any("delayed" in reason for reason in card.reasons)


def test_triggered_but_partial_data_is_hold():
    card = _card(data_tier="partial")
    assert card.action == "HOLD"
    assert any("data tier" in reason for reason in card.reasons)


def test_forming_setup_is_hold():
    assert _card(setup_state="FORMING").action == "HOLD"


def test_invalidated_setup_is_exit():
    assert _card(setup_state="INVALIDATED").action == "EXIT"


def test_card_time_is_new_york_market_time():
    assert _card().bar_time_label == "9:30 AM ET/NY"


def test_naive_bar_time_is_rejected():
    with pytest.raises(ValueError):
        _card(bar_time_utc=datetime(2026, 11, 18, 14, 30))


def test_nonpositive_levels_are_rejected():
    with pytest.raises(ValueError):
        _card(entry_reference=0.0)


def test_card_has_no_order_fields():
    fields = set(asdict(_card()).keys())
    assert not any(word in field for field in fields for word in ("order", "quantity", "route", "account"))
