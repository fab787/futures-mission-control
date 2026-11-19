from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


def closed_bars(
    bars: list[dict[str, Any]],
    now: datetime,
    timeframe: timedelta,
) -> list[dict[str, Any]]:
    """Return only bars whose close time is at or before `now`.

    Each bar's "t" is its OPEN time, timezone-aware, oldest first.
    A bar closes at t + timeframe. The forming bar is always dropped.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if timeframe <= timedelta(0):
        raise ValueError("timeframe must be positive")

    result: list[dict[str, Any]] = []
    previous: datetime | None = None
    for bar in bars:
        opened = bar["t"]
        if opened.tzinfo is None:
            raise ValueError("bar times must be timezone-aware")
        if previous is not None and opened <= previous:
            raise ValueError("bars must be strictly increasing by time")
        previous = opened
        if opened + timeframe <= now:
            result.append(bar)
    return result
