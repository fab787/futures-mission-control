from __future__ import annotations

from collections.abc import Sequence


def ema(values: Sequence[float], period: int) -> list[float | None]:
    """Exponential moving average, seeded with the SMA of the first `period` values.

    Returns None for every index before the seed. Values before a
    comfortable warmup are still computed; use `is_warm` to gate them.
    """
    if period < 1:
        raise ValueError("period must be at least 1")

    count = len(values)
    output: list[float | None] = [None] * count
    if count < period:
        return output

    alpha = 2.0 / (period + 1.0)
    current = sum(values[:period]) / period
    output[period - 1] = current
    for index in range(period, count):
        current = alpha * values[index] + (1.0 - alpha) * current
        output[index] = current
    return output


def is_warm(bar_count: int, period: int, multiplier: int = 3) -> bool:
    """True once the EMA has had enough bars to stop depending on its seed."""
    return bar_count >= multiplier * period
