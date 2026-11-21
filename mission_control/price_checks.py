"""Read-only 1-minute and 15-minute price checks for the Market Structure Analyst.

Built only from bars already in the cache. Never calls a provider.
Same noise-band method as the five-minute check in news_analyst_desk.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter

from mission_control.news_analyst_desk import INSTRUMENTS

ONE_MIN_LOOKBACK = 20
FIFTEEN_MIN_LOOKBACK = 20
ONE_MIN_FLOOR_PCT = 0.02
FIFTEEN_MIN_FLOOR_PCT = 0.05
BAND_SIGMAS = 1.0


def closes_from_bars(bars: Any) -> list[float]:
    out: list[float] = []
    for bar in bars if isinstance(bars, list) else []:
        try:
            value = float(bar.get("c"))
        except (AttributeError, TypeError, ValueError):
            continue
        if value > 0:
            out.append(value)
    return out


def block_closes(closes: list[float], size: int) -> list[float]:
    """Last close of each complete block of `size` bars, newest block included."""
    offset = len(closes) % size
    return closes[offset:][size - 1::size]


def check_window(closes: list[float], lookback: int, floor_pct: float, label: str) -> dict[str, Any]:
    need = lookback + 1
    if len(closes) < need:
        return {"direction": None, "move_pct": None, "band_pct": None,
                "detail": f"Not enough cached {label}: {len(closes)} of {need} needed."}
    tail = closes[-need:]
    returns = [abs(b - a) / a for a, b in zip(tail, tail[1:])]
    mean_abs = sum(returns) / len(returns)
    sigma_pct = 100.0 * mean_abs * (lookback ** 0.5) * 1.25
    band = max(floor_pct, BAND_SIGMAS * sigma_pct)
    move = (tail[-1] - tail[0]) / tail[0] * 100.0
    if move > band:
        direction = "up"
    elif move < -band:
        direction = "down"
    else:
        direction = "flat"
    return {"direction": direction, "move_pct": round(move, 3), "band_pct": round(band, 3),
            "detail": f"{direction} {move:+.2f}% over {lookback} {label}; noise band +/-{band:.2f}% (last {tail[-1]:g})."}


def build_checks(snapshot: dict | None) -> list[dict[str, Any]]:
    by_symbol: dict[str, dict] = {}
    for entry in (snapshot or {}).get("instruments", []) or []:
        if isinstance(entry, dict) and entry.get("symbol"):
            by_symbol[entry["symbol"]] = entry
    rows: list[dict[str, Any]] = []
    for symbol in INSTRUMENTS:
        entry = by_symbol.get(symbol) or {}
        closes = closes_from_bars(entry.get("bars"))
        one = check_window(closes, ONE_MIN_LOOKBACK, ONE_MIN_FLOOR_PCT, "one-minute bars")
        fifteen = check_window(block_closes(closes, 15), FIFTEEN_MIN_LOOKBACK, FIFTEEN_MIN_FLOOR_PCT,
                               "fifteen-minute bars (about 315 one-minute bars)")
        rows.append({"symbol": symbol, "one_minute": one, "fifteen_minute": fifteen})
    return rows


def make_router(snapshot_provider: Callable[[], dict | None]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/agents/price-checks")
    def price_checks() -> dict[str, Any]:
        rows = build_checks(snapshot_provider())
        ready = any(r["one_minute"]["direction"] or r["fifteen_minute"]["direction"] for r in rows)
        return {"status": "ready" if ready else "awaiting_bars", "instruments": rows,
                "source": "cached one-minute bars only; no provider requests",
                "provider_requests_triggered": 0}

    return router