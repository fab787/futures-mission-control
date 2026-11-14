"""Cache-only instrument analysis agents for FAB787 QUANTUM.

These deterministic agents summarize validated historical bars. They never fetch
provider data, call an LLM, generate orders, or modify account risk settings.
"""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any

MAX_ANALYSIS_AGE_SECONDS = 900.0
MIN_BARS = 20


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        # TradingView payloads may encode Unix seconds or milliseconds.
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    return None


def _number(bar: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = bar.get(key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number == number and abs(number) != float("inf"):
            return number
    return None


def _bar_time(bar: dict[str, Any]) -> datetime | None:
    return _timestamp(bar.get("t", bar.get("time", bar.get("timestamp"))))


def _empty_report(
    symbol: str,
    config: dict[str, Any],
    reason: str,
    *,
    freshness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "agent_id": f"{symbol.lower()}_instrument_agent",
        "agent_name": f"{symbol} Instrument Agent",
        "symbol": symbol,
        "instrument_name": config.get("name", symbol),
        "analysis_profile": config.get("analysis_profile"),
        "status": "blocked",
        "reason": reason,
        "freshness": freshness or {"status": "unknown", "age_seconds": None},
        "market_structure": None,
        "risk_authority": "none",
        "trade_authority": "none",
        "provider_requests_triggered": 0,
    }


def analyze_instrument(
    symbol: str,
    config: dict[str, Any],
    snapshot: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build one instrument report from an already-cached market snapshot."""
    if snapshot is None:
        return _empty_report(symbol, config, "No cached market snapshot is available.")

    instrument = next(
        (
            item for item in snapshot.get("instruments", [])
            if isinstance(item, dict) and item.get("symbol") == symbol
        ),
        None,
    )
    if instrument is None:
        return _empty_report(symbol, config, "Instrument is absent from the cached snapshot.")

    bars = instrument.get("bars")
    if not isinstance(bars, list):
        return _empty_report(symbol, config, "Cached OHLCV payload is malformed.")
    valid_bars = [bar for bar in bars if isinstance(bar, dict)]
    if len(valid_bars) < MIN_BARS:
        return _empty_report(
            symbol, config,
            f"Insufficient history: {len(valid_bars)} valid bars; {MIN_BARS} required.",
        )

    normalized: list[dict[str, float]] = []
    for bar in valid_bars:
        values = {key: _number(bar, key) for key in ("o", "h", "l", "c")}
        if any(value is None for value in values.values()):
            continue
        if values["h"] < values["l"] or values["c"] <= 0:
            continue
        normalized.append({key: float(value) for key, value in values.items()})
        volume = _number(bar, "v", "volume")
        if volume is not None:
            normalized[-1]["v"] = volume

    if len(normalized) < MIN_BARS:
        return _empty_report(
            symbol, config,
            f"Insufficient valid OHLC bars: {len(normalized)}; {MIN_BARS} required.",
        )

    latest_time = _bar_time(valid_bars[-1])
    if latest_time is None:
        return _empty_report(
            symbol, config,
            "Latest bar timestamp is missing or has no timezone; freshness cannot be verified.",
        )

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    age = max(0.0, (current.astimezone(timezone.utc) - latest_time).total_seconds())
    cache_status = (snapshot.get("cache") or {}).get("status", "unknown")
    freshness_status = "fresh" if age <= MAX_ANALYSIS_AGE_SECONDS else "stale"
    freshness = {
        "status": freshness_status,
        "age_seconds": round(age, 1),
        "latest_bar_at": latest_time.isoformat(),
        "cache_status": cache_status,
        "max_age_seconds": MAX_ANALYSIS_AGE_SECONDS,
    }
    if freshness_status != "fresh":
        return _empty_report(
            symbol, config,
            "Latest market bar exceeds the 15-minute analysis freshness limit.",
            freshness=freshness,
        )
    if cache_status == "stale":
        return _empty_report(
            symbol, config,
            "Market-data manager marked this snapshot stale; analysis is blocked.",
            freshness=freshness,
        )

    closes = [bar["c"] for bar in normalized]
    recent = normalized[-20:]
    first_close, last_close = closes[-20], closes[-1]
    change_pct = ((last_close / first_close) - 1.0) * 100.0
    if change_pct > 0.05:
        direction = "upward"
    elif change_pct < -0.05:
        direction = "downward"
    else:
        direction = "sideways"

    true_ranges: list[float] = []
    for index in range(max(1, len(normalized) - 14), len(normalized)):
        current_bar = normalized[index]
        previous_close = normalized[index - 1]["c"]
        true_ranges.append(
            max(
                current_bar["h"] - current_bar["l"],
                abs(current_bar["h"] - previous_close),
                abs(current_bar["l"] - previous_close),
            )
        )
    atr = sum(true_ranges) / len(true_ranges) if true_ranges else None
    recent_high = max(bar["h"] for bar in recent)
    recent_low = min(bar["l"] for bar in recent)

    volumes = [bar["v"] for bar in normalized[-21:] if "v" in bar and bar["v"] >= 0]
    relative_volume = None
    if len(volumes) >= 2:
        baseline = median(volumes[:-1])
        if baseline > 0:
            relative_volume = round(volumes[-1] / baseline, 2)

    return {
        "agent_id": f"{symbol.lower()}_instrument_agent",
        "agent_name": f"{symbol} Instrument Agent",
        "symbol": symbol,
        "instrument_name": config.get("name", symbol),
        "analysis_profile": config.get("analysis_profile"),
        "status": "ready",
        "reason": None,
        "freshness": freshness,
        "market_structure": {
            "last_close": round(last_close, 4),
            "direction_20_bars": direction,
            "change_20_bars_pct": round(change_pct, 4),
            "recent_20_bar_high": round(recent_high, 4),
            "recent_20_bar_low": round(recent_low, 4),
            "atr_14": round(atr, 4) if atr is not None else None,
            "relative_volume": relative_volume,
            "bars_analyzed": len(normalized),
            "timeframe": snapshot.get("interval"),
            "source": instrument.get("source", snapshot.get("source", "unknown")),
        },
        "interpretation": (
            "Descriptive historical market context only; not a forecast or trade signal."
        ),
        "risk_authority": "none",
        "trade_authority": "none",
        "provider_requests_triggered": 0,
    }


def analyze_instruments(
    instruments: dict[str, dict[str, Any]],
    snapshot: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return reports for all enabled instruments without loading provider data."""
    reports = [
        analyze_instrument(symbol, config, snapshot, now=now)
        for symbol, config in instruments.items()
    ]
    ready = sum(report["status"] == "ready" for report in reports)
    return {
        "status": "ready" if ready == len(reports) and reports else "partial" if ready else "blocked",
        "mode": "cache_only_deterministic_analysis",
        "agents": reports,
        "summary": {
            "total_agents": len(reports),
            "ready_agents": ready,
            "blocked_agents": len(reports) - ready,
            "provider_requests_triggered": 0,
            "trade_proposals_created": 0,
        },
        "safety": {
            "risk_engine_bypass": False,
            "order_execution": "disabled",
            "human_approval_required": True,
        },
    }
