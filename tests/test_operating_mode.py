import pytest

from mission_control.operating_mode import (
    CONFIRM_PHRASE,
    Mode,
    OperatingState,
    Venue,
    may_submit_order,
    switch_to_signal,
    switch_to_trading,
)


def test_default_state_is_signal_and_paper():
    state = OperatingState()
    assert state.mode is Mode.SIGNAL
    assert state.venue is Venue.PAPER


def test_signal_mode_never_allows_orders_even_when_approved():
    state = OperatingState()
    assert may_submit_order(state, risk_approved=True, human_approved=True) is False


def test_trading_requires_exact_confirmation_phrase():
    with pytest.raises(ValueError):
        switch_to_trading(OperatingState(), "trading")
    with pytest.raises(ValueError):
        switch_to_trading(OperatingState(), "")
    assert switch_to_trading(OperatingState(), CONFIRM_PHRASE).mode is Mode.TRADING


def test_trading_mode_still_needs_risk_and_human_approval():
    state = switch_to_trading(OperatingState(), CONFIRM_PHRASE)
    assert may_submit_order(state, risk_approved=True, human_approved=False) is False
    assert may_submit_order(state, risk_approved=False, human_approved=True) is False
    assert may_submit_order(state, risk_approved=True, human_approved=True) is True


def test_live_venue_is_always_blocked():
    state = OperatingState(mode=Mode.TRADING, venue=Venue.LIVE)
    assert may_submit_order(state, risk_approved=True, human_approved=True) is False


def test_returning_to_signal_removes_capability():
    state = switch_to_signal(switch_to_trading(OperatingState(), CONFIRM_PHRASE))
    assert may_submit_order(state, risk_approved=True, human_approved=True) is False
