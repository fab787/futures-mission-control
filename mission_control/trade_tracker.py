# Trade tracking for SIGNAL mode. Records trades YOU took. Never places orders.
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

MAX_TARGETS = 5
STYLES = {
    "WATCHING": ("WATCHING", "grey"),
    "FORMING": ("FORMING", "amber"),
    "ENTRY_LONG": ("ENTRY SIGNAL · LONG", "green"),
    "ENTRY_SHORT": ("ENTRY SIGNAL · SHORT", "red"),
    "IN_TRADE": ("IN TRADE", "blue"),
    "TARGET_HIT": ("TARGET {n} HIT", "green"),
    "TARGET_FINAL": ("TARGET {n} HIT · TRADE CLOSED", "green"),
    "STOP_HIT": ("STOP HIT", "red"),
    "BREAKEVEN_STOP": ("STOP MOVED TO ENTRY", "blue"),
    "STOP_TRAILED": ("STOP TRAILED", "blue"),
    "STOP_MOVED": ("STOP MOVED", "blue"),
    "INVALIDATED": ("EXIT · SETUP INVALIDATED", "orange"),
    "HOLD": ("HOLD", "grey"),
}


def style_for(key: str, n: int | None = None) -> tuple[str, str]:
    """Return (label, color) for a message key. Colors are semantic names for the UI."""
    label, color = STYLES[key]
    return (label.format(n=n) if n is not None else label), color


def _finite_positive(value: float, label: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be a positive finite number")
    return number


@dataclass(frozen=True)
class TargetPlan:
    multiples: tuple[float, ...] = (1.0, 1.5, 3.0)
    splits: tuple[float, ...] = (1 / 3, 1 / 3, 1 / 3)

    def __post_init__(self) -> None:
        if not 1 <= len(self.multiples) <= MAX_TARGETS:
            raise ValueError(f"use between 1 and {MAX_TARGETS} targets")
        if len(self.splits) != len(self.multiples):
            raise ValueError("each target needs exactly one share of the position")
        if any(not math.isfinite(m) or m <= 0 for m in self.multiples):
            raise ValueError("target multiples must be positive")
        if any(later <= earlier for earlier, later in zip(self.multiples, self.multiples[1:])):
            raise ValueError("target multiples must increase strictly")
        if any(s <= 0 for s in self.splits):
            raise ValueError("each share must be positive")
        if abs(sum(self.splits) - 1.0) > 1e-9:
            raise ValueError("shares must add up to 100 percent")


@dataclass(frozen=True)
class StopPlan:
    breakeven_after_hits: int | None = 1   # move stop to entry after this many targets; None = never
    trail_after_hits: int | None = None    # start trailing after this many targets; None = no trail
    trail_r: float = 1.0                   # trailing distance behind the best price, in R

    def __post_init__(self) -> None:
        for name, value in (
            ("breakeven_after_hits", self.breakeven_after_hits),
            ("trail_after_hits", self.trail_after_hits),
        ):
            if value is not None and value < 1:
                raise ValueError(f"{name} must be at least 1 or None")
        if not math.isfinite(self.trail_r) or self.trail_r <= 0:
            raise ValueError("trail_r must be a positive number")


@dataclass(frozen=True)
class Trade:
    symbol: str
    direction: str
    entry: float
    stop: float
    initial_risk: float
    plan: TargetPlan
    stop_plan: StopPlan
    contracts: int
    targets: tuple[float, ...]
    best_price: float
    hits: int = 0
    closed: bool = False
    realized_r: float = 0.0


def open_trade(
    symbol: str,
    direction: str,
    entry: float,
    stop: float,
    plan: TargetPlan | None = None,
    stop_plan: StopPlan | None = None,
    contracts: int = 1,
) -> Trade:
    """Record a trade you took. Targets are fixed multiples of the initial risk."""
    if direction not in ("long", "short"):
        raise ValueError("direction must be long or short")
    entry = _finite_positive(entry, "entry")
    stop = _finite_positive(stop, "stop")
    if direction == "long" and not stop < entry:
        raise ValueError("a long stop must be below entry")
    if direction == "short" and not stop > entry:
        raise ValueError("a short stop must be above entry")
    if contracts < 1:
        raise ValueError("contracts must be at least 1")
    plan = plan or TargetPlan()
    stop_plan = stop_plan or StopPlan()
    risk = abs(entry - stop)
    sign = 1.0 if direction == "long" else -1.0
    targets = tuple(round(entry + sign * m * risk, 6) for m in plan.multiples)
    return Trade(symbol, direction, entry, stop, risk, plan, stop_plan, contracts, targets, best_price=entry)


def _tightens(direction: str, new_stop: float, current: float) -> bool:
    return new_stop > current if direction == "long" else new_stop < current


def _tighter(direction: str, a: float, b: float) -> float:
    return max(a, b) if direction == "long" else min(a, b)


def move_stop(trade: Trade, new_stop: float) -> tuple[Trade, list[tuple[str, int | None]]]:
    """Manual stop move. A stop may tighten toward profit, never widen."""
    if trade.closed:
        raise ValueError("trade is closed")
    new_stop = _finite_positive(new_stop, "stop")
    if new_stop != trade.stop and not _tightens(trade.direction, new_stop, trade.stop):
        raise ValueError("stops can only tighten, never widen")
    return replace(trade, stop=new_stop), [("STOP_MOVED", None)]


def _hit_fraction(trade: Trade) -> float:
    return sum(trade.plan.splits[: trade.hits])


def apply_bar(trade: Trade, bar: dict[str, Any]) -> tuple[Trade, list[tuple[str, int | None]]]:
    """Apply one CLOSED bar. Returns the updated trade and the events it produced.

    Order of checks within a bar:
      1. Stop, using the stop as it stood before this bar (intrabar order is unknown,
         so a stop touched on the same bar as a target is assumed first).
      2. Targets reached on this bar, including breakeven moves they trigger.
      3. Best price updated, then trailing applied for the next bar.
    """
    if trade.closed:
        return trade, []
    high = float(bar["h"])
    low = float(bar["l"])
    if not (math.isfinite(high) and math.isfinite(low)) or high < low:
        raise ValueError("bar must have finite high >= low")

    long = trade.direction == "long"
    sign = 1.0 if long else -1.0

    stop_touched = low <= trade.stop if long else high >= trade.stop
    if stop_touched:
        remaining = 1.0 - _hit_fraction(trade)
        stop_r = sign * (trade.stop - trade.entry) / trade.initial_risk
        realized = trade.realized_r + remaining * stop_r
        return replace(trade, closed=True, realized_r=realized), [("STOP_HIT", None)]

    events: list[tuple[str, int | None]] = []
    hits = trade.hits
    realized = trade.realized_r
    stop = trade.stop
    sp = trade.stop_plan
    total = len(trade.targets)

    while hits < total:
        level = trade.targets[hits]
        reached = high >= level if long else low <= level
        if not reached:
            break
        realized += trade.plan.splits[hits] * trade.plan.multiples[hits]
        hits += 1
        if hits == total:
            events.append(("TARGET_FINAL", hits))
        else:
            events.append(("TARGET_HIT", hits))
            if sp.breakeven_after_hits == hits and _tightens(trade.direction, trade.entry, stop):
                stop = trade.entry
                events.append(("BREAKEVEN_STOP", None))

    best = _tighter(trade.direction, trade.best_price, high if long else low)
    best = max(trade.best_price, high) if long else min(trade.best_price, low)

    closed = hits == total
    if not closed and sp.trail_after_hits is not None and hits >= sp.trail_after_hits:
        candidate = best - sign * sp.trail_r * trade.initial_risk
        if _tightens(trade.direction, candidate, stop):
            stop = candidate
            events.append(("STOP_TRAILED", None))

    return replace(
        trade, stop=stop, hits=hits, realized_r=realized, closed=closed, best_price=best
    ), events
