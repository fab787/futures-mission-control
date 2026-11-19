"""Cache-only Market Structure specialist for FAB787 QUANTUM.

Calculates descriptive EMA context and confirmed swing levels from validated
instrument-agent reports. It never calls a market-data provider or creates trades.
"""
from __future__ import annotations

from typing import Any


def _valid_bars(instrument: dict[str, Any]) -> list[dict[str, float]]:
    raw = instrument.get("bars")
    if not isinstance(raw, list):
        return []
    bars: list[dict[str, float]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            bar = {key: float(item[key]) for key in ("h", "l", "c")}
        except (KeyError, TypeError, ValueError):
            continue
        if any(value != value or abs(value) == float("inf") for value in bar.values()):
            continue
        if bar["h"] < bar["l"] or bar["c"] <= 0:
            continue
        bars.append(bar)
    return bars


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    multiplier = 2.0 / (period + 1.0)
    value = sum(values[:period]) / period
    for item in values[period:]:
        value = (item - value) * multiplier + value
    return value


def _confirmed_pivots(
    bars: list[dict[str, float]], *, kind: str, left: int = 2, right: int = 2
) -> list[tuple[int, float]]:
    pivots: list[tuple[int, float]] = []
    field = "h" if kind == "high" else "l"
    for index in range(left, len(bars) - right):
        value = bars[index][field]
        window = [bar[field] for bar in bars[index - left:index + right + 1]]
        if kind == "high" and value == max(window) and window.count(value) == 1:
            pivots.append((index, value))
        elif kind == "low" and value == min(window) and window.count(value) == 1:
            pivots.append((index, value))
    return pivots


def _level(value: float | None, price: float, label: str) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "price": round(value, 4),
        "distance_points": round(price - value, 4),
        "distance_pct": round(((price - value) / price) * 100.0, 4),
        "type": label,
    }


def analyze_market_structure(
    instrument_reports: dict[str, dict[str, Any]],
    snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """Analyze only instruments already marked ready by the cache-only base agents."""
    if snapshot is None:
        return {
            "specialist_id": "market_structure",
            "specialist_name": "Market Structure Analyst",
            "status": "blocked",
            "reason": "No cached market snapshot is available.",
            "instruments": [],
            "provider_requests_triggered": 0,
            "trade_authority": "none",
        }

    output: list[dict[str, Any]] = []
    snapshot_items = {
        item.get("symbol"): item
        for item in snapshot.get("instruments", [])
        if isinstance(item, dict) and isinstance(item.get("symbol"), str)
    }
    for symbol, report in instrument_reports.items():
        if report.get("status") != "ready":
            output.append({
                "symbol": symbol,
                "status": "blocked",
                "reason": "Base instrument report is not ready; specialist analysis withheld.",
                "market_structure": None,
            })
            continue
        instrument = snapshot_items.get(symbol)
        bars = _valid_bars(instrument or {})
        if len(bars) < 50:
            output.append({
                "symbol": symbol,
                "status": "blocked",
                "reason": f"At least 50 valid bars are required for structure analysis; found {len(bars)}.",
                "market_structure": None,
            })
            continue

        closes = [bar["c"] for bar in bars]
        price = closes[-1]
        ema20 = _ema(closes, 20)
        ema50 = _ema(closes, 50)
        if ema20 is None or ema50 is None:
            output.append({
                "symbol": symbol,
                "status": "blocked",
                "reason": "Insufficient closes for EMA context.",
                "market_structure": None,
            })
            continue

        if price > ema20 > ema50:
            trend = "bullish_alignment"
        elif price < ema20 < ema50:
            trend = "bearish_alignment"
        elif ema20 > ema50:
            trend = "mixed_bullish"
        elif ema20 < ema50:
            trend = "mixed_bearish"
        else:
            trend = "neutral"

        highs = _confirmed_pivots(bars, kind="high")
        lows = _confirmed_pivots(bars, kind="low")
        supports = [value for _, value in lows if value < price]
        resistances = [value for _, value in highs if value > price]
        nearest_support = max(supports) if supports else None
        nearest_resistance = min(resistances) if resistances else None

        swing_state = "insufficient_confirmed_swings"
        if len(highs) >= 2 and len(lows) >= 2:
            higher_high = highs[-1][1] > highs[-2][1]
            higher_low = lows[-1][1] > lows[-2][1]
            lower_high = highs[-1][1] < highs[-2][1]
            lower_low = lows[-1][1] < lows[-2][1]
            if higher_high and higher_low:
                swing_state = "higher_highs_higher_lows"
            elif lower_high and lower_low:
                swing_state = "lower_highs_lower_lows"
            else:
                swing_state = "mixed_or_range"

        output.append({
            "symbol": symbol,
            "status": "ready",
            "reason": None,
            "market_structure": {
                "last_close": round(price, 4),
                "ema_20": round(ema20, 4),
                "ema_50": round(ema50, 4),
                "ema_alignment": trend,
                "confirmed_swing_structure": swing_state,
                "nearest_support": _level(nearest_support, price, "confirmed_swing_low"),
                "nearest_resistance": _level(nearest_resistance, price, "confirmed_swing_high"),
                "confirmed_swing_high_count": len(highs),
                "confirmed_swing_low_count": len(lows),
                "pivot_confirmation": "2 bars left and 2 bars right; unconfirmed latest swings excluded",
                "bars_analyzed": len(bars),
                "timeframe": snapshot.get("interval"),
            },
        })

    ready = sum(item["status"] == "ready" for item in output)
    return {
        "specialist_id": "market_structure",
        "specialist_name": "Market Structure Analyst",
        "status": "ready" if ready == len(output) and output else "partial" if ready else "blocked",
        "instruments": output,
        "summary": {
            "total_instruments": len(output),
            "ready_instruments": ready,
            "blocked_instruments": len(output) - ready,
        },
        "interpretation": (
            "Descriptive technical context only. EMA alignment and swing levels are not "
            "forecasts, entry instructions, or trade approvals."
        ),
        "provider_requests_triggered": 0,
        "risk_authority": "none",
        "trade_authority": "none",
    }
