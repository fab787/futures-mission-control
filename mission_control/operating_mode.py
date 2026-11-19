# Operating modes for FAB787 QUANTUM.
#
# SIGNAL mode (default): the platform scans and reports. It never submits orders.
# TRADING mode: bots may submit paper orders, but only when the Master Risk Engine
# and a human have both approved that specific order.
#
# Live trading is not implemented. LIVE is rejected here until it is deliberately built.
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Mode(str, Enum):
    SIGNAL = "signal"
    TRADING = "trading"


class Venue(str, Enum):
    PAPER = "paper"
    LIVE = "live"


CONFIRM_PHRASE = "TRADING"


@dataclass(frozen=True)
class OperatingState:
    mode: Mode = Mode.SIGNAL
    venue: Venue = Venue.PAPER


def switch_to_trading(state: OperatingState, typed_phrase: str) -> OperatingState:
    """Enter TRADING mode only when the user types the exact confirmation phrase."""
    if typed_phrase != CONFIRM_PHRASE:
        raise ValueError(f"type {CONFIRM_PHRASE} exactly to enable trading mode")
    return OperatingState(mode=Mode.TRADING, venue=state.venue)


def switch_to_signal(state: OperatingState) -> OperatingState:
    """Returning to SIGNAL mode needs no confirmation: it only removes capability."""
    return OperatingState(mode=Mode.SIGNAL, venue=state.venue)


def may_submit_order(
    state: OperatingState,
    *,
    risk_approved: bool,
    human_approved: bool,
) -> bool:
    """The single gate every order must pass. Anything missing returns False."""
    if state.mode is not Mode.TRADING:
        return False
    if state.venue is not Venue.PAPER:
        return False  # live execution is not built yet
    return risk_approved and human_approved
