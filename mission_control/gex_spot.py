"""ETF spot price for GEX (QQQ, SPY, GLD) from Webull get_stock_snapshot.

Read-only and cache-first. Index tickers (NDX, SPX) have no quote here and must
pass spot explicitly. Stale data is served only if a refresh fails, and is marked.
"""

from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime
from typing import Any, Awaitable, Callable

from mission_control.gex import GexUnavailable
from mission_control.gex_sources import ET
from mission_control.webull import _records, call_readonly_tool

ETF_SPOT_TICKERS = frozenset({"QQQ", "SPY", "GLD"})
SPOT_TTL_SECONDS = 15.0
SPOT_FIELDS = ("price", "close")

ToolCaller = Callable[[str, dict[str, Any]], Awaitable[Any]]


def _parse_price(row: dict[str, Any]) -> tuple[float, str]:
    for key in SPOT_FIELDS:
        raw = row.get(key)
        if raw in (None, ""):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            return value, key
    raise GexUnavailable("Webull stock snapshot has no usable price")


class WebullSpotService:
    def __init__(
        self,
        *,
        call_tool: ToolCaller | None = None,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = SPOT_TTL_SECONDS,
    ) -> None:
        self._call_tool = call_tool or call_readonly_tool
        self._clock = clock
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    async def get_spot(self, ticker: str, force_refresh: bool = False) -> dict[str, Any]:
        if ticker not in ETF_SPOT_TICKERS:
            raise GexUnavailable(f"{ticker} has no Webull ETF quote; pass spot explicitly")
        entry = self._cache.get(ticker)
        if entry and not force_refresh and self._clock() - entry[0] < self.ttl_seconds:
            return {**entry[1], "cache_status": "cached"}
        try:
            records = _records(await self._call_tool("get_stock_snapshot", {
                "symbols": ticker,
                "category": "US_ETF",
                "extend_hour_required": False,
                "overnight_required": False,
            }))
            row = next((r for r in records if str(r.get("symbol", "")).upper() == ticker), None)
            if row is None:
                raise GexUnavailable(f"Webull returned no quote for {ticker}")
            price, field = _parse_price(row)
        except GexUnavailable:
            if entry:
                return {**entry[1], "cache_status": "stale"}
            raise
        except Exception as exc:
            if entry:
                return {**entry[1], "cache_status": "stale"}
            raise GexUnavailable(f"Webull spot request failed: {type(exc).__name__}") from exc

        result = {
            "ticker": ticker,
            "spot": price,
            "spot_field": field,
            "trade_status": row.get("trade_status"),
            "quote_time_ms": row.get("quote_time"),
            "retrieved_at": datetime.now(ET).isoformat(timespec="seconds"),
        }
        self._cache[ticker] = (self._clock(), result)
        return {**result, "cache_status": "fresh"}


webull_spot_service = WebullSpotService()
