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
from datetime import date, datetime, timedelta
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


def _trading_days_to_expiry(expiration: str, now: datetime) -> int:
    """Weekday count to expiry; exchange holidays are not removed."""
    expiry_date = date.fromisoformat(expiration)
    cursor = now.date()
    days = 0
    while cursor < expiry_date:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            days += 1
    return max(1, days)


def _expected_move(rows: list[dict[str, Any]], spot: float, now: datetime) -> dict[str, Any]:
    expirations = sorted({row["expiration"] for row in rows})
    if not expirations:
        return {"status": "unavailable", "reason": "No eligible expiration with usable option IV."}

    expiration = expirations[0]
    expiry_rows = [row for row in rows if row["expiration"] == expiration]
    if not expiry_rows:
        return {"status": "unavailable", "reason": "Nearest expiration has no usable IV observations."}

    atm = min(expiry_rows, key=lambda row: abs(row["strike"] - spot))["strike"]
    atm_rows = [row for row in expiry_rows if row["strike"] == atm and row["iv"] > 0]
    if not atm_rows:
        return {"status": "unavailable", "reason": "Nearest-expiration ATM implied volatility is unavailable."}

    # Average call/put IV at the closest listed strike; this is chain IV, not a forecast.
    iv = sum(row["iv"] for row in atm_rows) / len(atm_rows)
    if not math.isfinite(iv) or iv <= 0:
        return {"status": "unavailable", "reason": "Nearest-expiration ATM implied volatility is invalid."}

    trading_days = _trading_days_to_expiry(expiration, now)
    move_points = spot * iv * math.sqrt(trading_days / 252.0)
    return {
        "status": "ready",
        "points_ndx": round(move_points, 2),
        "percent": round(move_points / spot * 100.0, 3),
        "lower_ndx": round(spot - move_points, 2),
        "upper_ndx": round(spot + move_points, 2),
        "expiration": expiration,
        "trading_days": trading_days,
        "atm_iv": round(iv, 6),
        "atm_iv_pct": round(iv * 100.0, 2),
        "iv_source": "Cboe delayed option-chain IV at the nearest listed ATM strike",
        "holiday_handling": "Weekdays counted; exchange holidays are not removed",
    }


def calculate_gex(
    payload: dict[str, Any],
    futures_price: float,
    *,
    price_source: str,
    cache_status: str,
    cache_age_seconds: float,
    retrieved_at: str,
) -> dict[str, Any]:
    if not math.isfinite(futures_price) or futures_price <= 0:
        raise GexUnavailable("A finite, positive MNQ/NQ futures price is required")

    spot = _find_spot(payload)
    scale = futures_price / spot
    if not math.isfinite(scale) or not 0.50 <= scale <= 1.50:
        raise GexUnavailable(
            f"The futures-to-NDX proxy ratio ({scale:.4f}) is outside the validation band "
            "0.50–1.50. Verify that the selected quote is MNQ/NQ and not another instrument."
        )

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

    strikes: list[dict[str, Any]] = []
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

    # Rankings are based on modeled gamma exposure, never OI alone.
    call_ranked = sorted(
        (row for row in strikes if row["call_gex"] > 0),
        key=lambda row: row["call_gex"],
        reverse=True,
    )[:3]
    put_ranked = sorted(
        (row for row in strikes if row["put_gex"] < 0),
        key=lambda row: row["put_gex"],
    )[:3]
    call_levels = [
        {"label": f"C{i}", "ndx_strike": row["ndx_strike"], "futures_level": row["futures_level"], "gex": row["call_gex"], "oi": row["call_oi"]}
        for i, row in enumerate(call_ranked, start=1)
    ]
    put_levels = [
        {"label": f"P{i}", "ndx_strike": row["ndx_strike"], "futures_level": row["futures_level"], "gex": row["put_gex"], "oi": row["put_oi"]}
        for i, row in enumerate(put_ranked, start=1)
    ]
    call_wall = call_ranked[0]["ndx_strike"] if call_ranked else None
    put_wall = put_ranked[0]["ndx_strike"] if put_ranked else None
    flip = _gamma_flip(rows, spot)
    expected = _expected_move(rows, spot, now)
    top_strikes = sorted(strikes, key=lambda row: abs(row["net_gex"]), reverse=True)[:20]

    expected_ready = expected.get("status") == "ready"
    expected_points = expected.get("points_ndx") if expected_ready else None
    expected_lower = expected.get("lower_ndx") if expected_ready else None
    expected_upper = expected.get("upper_ndx") if expected_ready else None

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
        "call_wall_ndx": round(call_wall, 2) if call_wall is not None else None,
        "call_wall_futures": round(call_wall * scale, 2) if call_wall is not None else None,
        "put_wall_ndx": round(put_wall, 2) if put_wall is not None else None,
        "put_wall_futures": round(put_wall * scale, 2) if put_wall is not None else None,
        "gamma_flip_ndx": round(flip, 2) if flip is not None else None,
        "gamma_flip_futures": round(flip * scale, 2) if flip is not None else None,
        "call_levels": call_levels,
        "put_levels": put_levels,
        "expected_move": expected,
        "expected_move_points_ndx": round(expected_points, 2) if expected_points is not None else None,
        "expected_move_points_futures": round(expected_points * scale, 2) if expected_points is not None else None,
        "expected_move_pct": expected.get("percent") if expected_ready else None,
        "expected_move_lower_ndx": round(expected_lower, 2) if expected_lower is not None else None,
        "expected_move_upper_ndx": round(expected_upper, 2) if expected_upper is not None else None,
        "expected_move_lower_futures": round(expected_lower * scale, 2) if expected_lower is not None else None,
        "expected_move_upper_futures": round(expected_upper * scale, 2) if expected_upper is not None else None,
        "expected_move_expiration": expected.get("expiration"),
        "expected_move_trading_days": expected.get("trading_days"),
        "expected_move_atm_iv_pct": expected.get("atm_iv_pct"),
        "expected_move_iv_source": expected.get("iv_source"),
        "realized_volatility": {
            "status": "unavailable",
            "annualized_pct": None,
            "source": None,
            "observations": 0,
            "reason": "No verified daily-return history was supplied to this chain calculation; cache-only daily data is checked by the API layer.",
        },
        "contracts_used": len(rows),
        "expirations_used": sorted({row["expiration"] for row in rows}),
        "top_strikes": top_strikes,
        "all_strikes": strikes,
        "assumptions": [
            "C1–C3 rank the three largest positive modeled call GEX strikes; P1–P3 rank the three most negative modeled put GEX strikes. Rankings use GEX, not open interest alone; the signed call/put convention is a dealer-positioning assumption.",
            "Calls are assigned positive gamma and puts negative gamma; this is a dealer-positioning assumption, not observed dealer inventory.",
            "Open interest is generally prior-session data; the options quote feed is delayed (approximately 15 minutes).",
            "Expected move uses nearest-expiration ATM implied volatility and spot × IV × sqrt(weekday trading days / 252); weekdays are counted without removing exchange holidays.",
            "Realized volatility is shown only when sufficient verified daily futures bars are already cached; this calculation does not request additional market data.",
            "Levels are estimates for context, not trade signals or guaranteed support/resistance.",
            "NDX options levels are mapped to MNQ/NQ using the futures-to-NDX spot ratio. This is an index-options proxy, not futures-native options data.",
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

    def peek_levels(self, futures_price: float, *, price_source: str) -> dict[str, Any] | None:
        """Recalculate from the last known chain without initiating provider I/O."""
        cached = self._cache
        if cached is None:
            return None
        stored_at, payload, retrieved_at = cached
        age = max(0.0, self._clock() - stored_at)
        status = "cached" if age < self.ttl_seconds else "stale"
        return calculate_gex(
            payload,
            futures_price,
            price_source=price_source,
            cache_status=status,
            cache_age_seconds=age,
            retrieved_at=retrieved_at,
        )

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
