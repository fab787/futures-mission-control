# Signal cards for SIGNAL mode. Pure logic: no I/O, no order fields.
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from mission_control.indicators.display import format_session_open

ACTIONS = ("BUY", "SELL", "HOLD", "EXIT")
SETUP_STATES = ("WATCHING", "FORMING", "TRIGGERED", "INVALIDATED")
DIRECTIONS = ("long", "short", None)


@dataclass(frozen=True)
class SignalCard:
    symbol: str
    action: str
    setup_state: str
    bar_time_label: str
    entry_reference: float | None
    invalidation_level: float | None
    target_reference: float | None
    score: int | None
    reasons: tuple[str, ...]
    data_tier: str
    delay_minutes: float | None
    note: str = "Signal only. No order is sent."


def _positive_or_none(value: float | None, label: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be a positive finite number")
    return number


def decide_action(
    setup_state: str,
    direction: str | None,
    data_tier: str,
    delay_minutes: float | None,
) -> tuple[str, list[str]]:
    """Return (action, reasons). BUY/SELL need a triggered, full-tier, zero-delay setup."""
    if setup_state not in SETUP_STATES:
        raise ValueError(f"unknown setup state: {setup_state}")
    if direction not in DIRECTIONS:
        raise ValueError(f"unknown direction: {direction}")

    if setup_state == "INVALIDATED":
        return "EXIT", ["Invalidation level crossed on a closed bar"]

    reasons: list[str] = []
    if setup_state != "TRIGGERED":
        reasons.append(f"Setup is {setup_state.lower()}; no trigger yet")
        return "HOLD", reasons

    if direction is None:
        return "HOLD", ["Triggered setup has no direction"]
    if data_tier != "full":
        reasons.append(f"Trigger held: data tier is {data_tier}, full tier required")
    if delay_minutes is None or delay_minutes > 0:
        reasons.append("Trigger held: feed is delayed or delay is unknown")
    if reasons:
        return "HOLD", reasons

    return ("BUY" if direction == "long" else "SELL"), []


def build_card(
    *,
    symbol: str,
    setup_state: str,
    direction: str | None,
    bar_time_utc: datetime,
    entry_reference: float | None,
    invalidation_level: float | None,
    target_reference: float | None,
    score: int | None,
    reasons: list[str],
    data_tier: str,
    delay_minutes: float | None,
) -> SignalCard:
    if bar_time_utc.tzinfo is None or bar_time_utc.utcoffset() is None:
        raise ValueError("bar_time_utc must be timezone-aware")

    action, gate_reasons = decide_action(setup_state, direction, data_tier, delay_minutes)
    main, _faded = format_session_open(bar_time_utc, "America/New_York", "NY")

    return SignalCard(
        symbol=symbol,
        action=action,
        setup_state=setup_state,
        bar_time_label=main,
        entry_reference=_positive_or_none(entry_reference, "entry_reference"),
        invalidation_level=_positive_or_none(invalidation_level, "invalidation_level"),
        target_reference=_positive_or_none(target_reference, "target_reference"),
        score=score,
        reasons=tuple(list(reasons) + gate_reasons),
        data_tier=data_tier,
        delay_minutes=delay_minutes,
    )
