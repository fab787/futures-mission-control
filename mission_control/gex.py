"""Cboe delayed-options GEX estimates for the NQ/MNQ dashboard workspace.

This adapter is intentionally independent from TradingView. It performs no
provider request until the user opens the GEX workspace and requests an update.
The raw options chain is cached for 15 minutes; futures-price changes only
recalculate levels from that cached chain.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
import urllib.request
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"
SOURCE_SYMBOL = "_NDX"
SOURCE_DELAY_MINUTES = 15
CACHE_TTL_SECONDS = 900.0
ERROR_COOLDOWN_SECONDS = 300.0
MAX_DAYS = 45.0
STRIKE_BAND = 0.15
CONTRACT_MULTIPLIER = 100.0
ET = ZoneInfo("America/New_York")
OCC = re.compile(r"^(.+?)(\d{6})([CP])(\d{8})$")


class GexUnavailable(RuntimeError):
    """Cboe data is unavailable or cannot produce a valid GEX estimate."""


def fetch_cboe_options(symbol: str = SOURCE_SYMBOL) -> dict[str, Any]:
    """Fetch one Cboe delayed options-chain snapshot."""
    url = CBOE_URL.format(symbol=symbol)
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 FAB787-QUANTUM"}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise GexUnavailable("Cboe returned an unexpected options-chain structure")
    return data


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _find_spot(payload: dict[str, Any]) -> float:
    data = _data(payload)
    for key in ("current_price", "close", "last_trade_price", "price"):
        value = _number(data.get(key))
        if value > 0:
            return value
    raise GexUnavailable("Cboe options chain did not include a usable NDX spot price")


def _parse_options(
    payload: dict[str, Any], now: datetime, max_days: float = MAX_DAYS
) -> list[dict[str, Any]]:
    data = _data(payload)
    options = data.get("options", [])
    if not isinstance(options, list):
        raise GexUnavailable("Cboe options list is missing or invalid")

    rows: list[dict[str, Any]] = []
    for option in options:
        if not isinstance(option, dict):
            continue
        match = OCC.match(str(option.get("option", "")))
        if not match:
            continue
        _, ymd, call_put, strike_raw = match.groups()
        try:
            expiration = datetime.strptime(ymd, "%y%m%d").replace(
                hour=16, tzinfo=ET
            )
        except ValueError:
            continue
        seconds_to_expiration = (expiration - now).total_seconds()
        if seconds_to_expiration <= 0 or seconds_to_expiration > max_days * 86400:
            continue
        open_interest = _number(option.get("open_interest"))
        implied_volatility = _number(option.get("iv"))
        strike = int(strike_raw) / 1000.0
        if open_interest <= 0 or implied_volatility <= 0 or strike <= 0:
            continue
        rows.append(
            {
                "strike": strike,
                "type": call_put,
                "oi": open_interest,
                "iv": implied_volatility,
                "T": max(
                    seconds_to_expiration / (365.0 * 86400.0),
                    1.0 / (365.0 * 24.0),
                ),
                "expiration": expiration.date().isoformat(),
            }
        )
    return rows


def _bs_gamma(spot: float, strike: float, iv: float, years: float, rate: float = 0.04) -> float:
    sigma_time = iv * math.sqrt(years)
    d1 = (
        math.log(spot / strike) + (rate + 0.5 * iv * iv) * years
    ) / sigma_time
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (
        spot * sigma_time
    )


def _row_gex(row: dict[str, Any], spot: float) -> float:
    sign = 1.0 if row["type"] == "C" else -1.0
    gamma = _bs_gamma(spot, row["strike"], row["iv"], row["T"])
    return (
        sign
        * gamma
        * row["oi"]
        * CONTRACT_MULTIPLIER
        * spot
        * spot
        * 0.01
    )


def _aggregate(rows: list[dict[str, Any]], spot: float) -> dict[float, dict[str, float]]:
    aggregate: defaultdict[float, dict[str, float]] = defaultdict(
        lambda: {"call_gex": 0.0, "put_gex": 0.0, "call_oi": 0.0, "put_oi": 0.0}
    )
    for row in rows:
        values = aggregate[row["strike"]]
        exposure = _row_gex(row, spot)
        if row["type"] == "C":
            values["call_gex"] += exposure
            values["call_oi"] += row["oi"]
        else:
            values["put_gex"] += exposure
            values["put_oi"] += row["oi"]
    return dict(sorted(aggregate.items()))


def _gamma_flip(rows: list[dict[str, Any]], spot: float) -> float | None:
    points = []
    for step in range(-20, 21):
        test_spot = spot * (1 + 0.005 * step)
        points.append((test_spot, sum(_row_gex(row, test_spot) for row in rows)))
    flips: list[float] = []
    for (s1, g1), (s2, g2) in zip(points, points[1:]):
        if g1 == 0 or g1 * g2 < 0:
            flips.append(s1 + (s2 - s1) * (0 - g1) / (g2 - g1) if g2 != g1 else s1)
    return min(flips, key=lambda value: abs(value - spot)) if flips else None


def calculate_gex(
    payload: dict[str, Any],
    futures_price: float,
    *,
    price_source: str,
    cache_status: str,
    cache_age_seconds: float,
    retrieved_at: str,
) -> dict[str, Any]:
    if futures_price <= 0:
        raise GexUnavailable("A valid MNQ/NQ futures price is required")
    spot = _find_spot(payload)
    now = datetime.now(ET)
    rows = [
        row
        for row in _parse_options(payload, now)
        if abs(row["strike"] / spot - 1) <= STRIKE_BAND
    ]
    if not rows:
        raise GexUnavailable(
            "No usable NDX options were found in the configured 45-day, 15% strike band"
        )

    aggregate = _aggregate(rows, spot)
    net_by_strike = {
        strike: values["call_gex"] + values["put_gex"]
        for strike, values in aggregate.items()
    }
    total_net = sum(net_by_strike.values())
    call_wall = max(aggregate, key=lambda strike: aggregate[strike]["call_gex"])
    put_wall = min(aggregate, key=lambda strike: aggregate[strike]["put_gex"])
    flip = _gamma_flip(rows, spot)
    scale = futures_price / spot

    strikes = []
    for strike, values in aggregate.items():
        strikes.append(
            {
                "ndx_strike": round(strike, 2),
                "futures_level": round(strike * scale, 2),
                "call_gex": round(values["call_gex"]),
                "put_gex": round(values["put_gex"]),
                "net_gex": round(values["call_gex"] + values["put_gex"]),
                "call_oi": int(values["call_oi"]),
                "put_oi": int(values["put_oi"]),
            }
        )
    top_strikes = sorted(strikes, key=lambda row: abs(row["net_gex"]), reverse=True)[:20]

    return {
        "status": cache_status,
        "source": "Cboe delayed options",
        "source_symbol": "NDX",
        "source_delay_minutes_estimate": SOURCE_DELAY_MINUTES,
        "retrieved_at": retrieved_at,
        "cache_age_seconds": round(max(0.0, cache_age_seconds), 1),
        "underlying_spot": round(spot, 2),
        "futures_price": round(futures_price, 2),
        "futures_price_source": price_source,
        "futures_spot_scale": round(scale, 6),
        "net_gex_per_1pct_move": round(total_net),
        "call_wall_ndx": round(call_wall, 2),
        "call_wall_futures": round(call_wall * scale, 2),
        "put_wall_ndx": round(put_wall, 2),
        "put_wall_futures": round(put_wall * scale, 2),
        "gamma_flip_ndx": round(flip, 2) if flip is not None else None,
        "gamma_flip_futures": round(flip * scale, 2) if flip is not None else None,
        "contracts_used": len(rows),
        "expirations_used": sorted({row["expiration"] for row in rows}),
        "top_strikes": top_strikes,
        "all_strikes": strikes,
        "assumptions": [
            "Calls are assigned positive gamma and puts negative gamma; this is a dealer-positioning assumption, not observed dealer inventory.",
            "Open interest is generally prior-session data; the options quote feed is delayed (approximately 15 minutes).",
            "Levels are estimates for context, not trade signals or guaranteed support/resistance.",
            "NDX options levels are mapped to MNQ/NQ using the futures-to-NDX spot ratio.",
        ],
    }


class GexService:
    """15-minute Cboe chain cache with stale fallback and retry cooldown."""

    def __init__(
        self,
        *,
        fetcher: Callable[[str], dict[str, Any]] = fetch_cboe_options,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = CACHE_TTL_SECONDS,
        error_cooldown_seconds: float = ERROR_COOLDOWN_SECONDS,
    ) -> None:
        self._fetcher = fetcher
        self._clock = clock
        self.ttl_seconds = ttl_seconds
        self.error_cooldown_seconds = error_cooldown_seconds
        self._cache: tuple[float, dict[str, Any], str] | None = None
        self._lock = asyncio.Lock()
        self._cooldown_until = 0.0
        self.fetch_count = 0

    async def get_levels(
        self, futures_price: float, *, price_source: str
    ) -> dict[str, Any]:
        now = self._clock()
        cached = self._cache
        if cached and now - cached[0] < self.ttl_seconds:
            return calculate_gex(
                cached[1], futures_price, price_source=price_source,
                cache_status="cached", cache_age_seconds=now - cached[0],
                retrieved_at=cached[2],
            )
        if now < self._cooldown_until:
            if cached:
                return calculate_gex(
                    cached[1], futures_price, price_source=price_source,
                    cache_status="stale", cache_age_seconds=now - cached[0],
                    retrieved_at=cached[2],
                )
            raise GexUnavailable(
                f"Cboe refresh is cooling down for {self._cooldown_until - now:.0f}s"
            )

        async with self._lock:
            now = self._clock()
            cached = self._cache
            if cached and now - cached[0] < self.ttl_seconds:
                return calculate_gex(
                    cached[1], futures_price, price_source=price_source,
                    cache_status="cached", cache_age_seconds=now - cached[0],
                    retrieved_at=cached[2],
                )
            if now < self._cooldown_until:
                if cached:
                    return calculate_gex(
                        cached[1], futures_price, price_source=price_source,
                        cache_status="stale", cache_age_seconds=now - cached[0],
                        retrieved_at=cached[2],
                    )
                raise GexUnavailable(
                    f"Cboe refresh is cooling down for {self._cooldown_until - now:.0f}s"
                )

            self.fetch_count += 1
            try:
                payload = await asyncio.to_thread(self._fetcher, SOURCE_SYMBOL)
                retrieved_at = datetime.now(ET).isoformat(timespec="seconds")
                # Validate before replacing the last known-good options chain.
                result = calculate_gex(
                    payload, futures_price, price_source=price_source,
                    cache_status="fresh", cache_age_seconds=0.0,
                    retrieved_at=retrieved_at,
                )
            except Exception as exc:
                self._cooldown_until = self._clock() + self.error_cooldown_seconds
                if cached:
                    return calculate_gex(
                        cached[1], futures_price, price_source=price_source,
                        cache_status="stale", cache_age_seconds=self._clock() - cached[0],
                        retrieved_at=cached[2],
                    )
                if isinstance(exc, GexUnavailable):
                    raise
                raise GexUnavailable(f"Cboe delayed options request failed: {type(exc).__name__}") from exc

            self._cache = (self._clock(), payload, retrieved_at)
            return result


gex_service = GexService()
