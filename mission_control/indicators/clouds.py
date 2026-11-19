from __future__ import annotations

from collections.abc import Sequence

from mission_control.indicators.ema import ema, is_warm

CLOUD_1 = (8, 21)
CLOUD_2 = (34, 50)


def cloud_state(closes: Sequence[float], fast: int, slow: int) -> list[int | None]:
    """Per-bar cloud state: +1 bullish, -1 bearish, 0 mixed, None while warming up.

    Bullish: fast EMA above slow EMA, and close above both.
    Bearish: fast EMA below slow EMA, and close below both.
    """
    if fast >= slow:
        raise ValueError("fast period must be shorter than slow period")
    fast_values = ema(closes, fast)
    slow_values = ema(closes, slow)
    states: list[int | None] = []
    for index, close in enumerate(closes):
        f = fast_values[index]
        s = slow_values[index]
        if f is None or s is None or not is_warm(index + 1, slow):
            states.append(None)
        elif f > s and close > max(f, s):
            states.append(1)
        elif f < s and close < min(f, s):
            states.append(-1)
        else:
            states.append(0)
    return states
