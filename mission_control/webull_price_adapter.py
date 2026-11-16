"""Webull futures bar adapter for FAB787 QUANTUM (Stage 1).

Pure conversion only: no network I/O, no provider calls, no trading.
Webull returns candles newest-first with fields open/high/low/close/time.
The analysis code expects oldest-first bars with keys t/o/h/l/c/v and
ISO-8601 UTC timestamps. This module performs that conversion and drops
malformed candles instead of guessing values.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

SOURCE_NAME = "Webull Cloud MCP"
SUPPORTED_ROOTS = ("MNQ", "MES", "MGC")


def _parse_time(value: Any) -> datetime | None:
    """Parse Webull timestamps such as 2026-11-16T03:47:00.000+0000."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    # Webull uses +0000 rather than +00:00; normalize before parsing.
    if len(text) >= 5 and text[-5] in "+-" and text[-4:].isdigit():
        text = f"{text[:-2]}:{text[-2:]}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _find_contract_entry(payload: Any, root: str) -> dict[str, Any] | None:
    if not isinstance(payload, list):
        return None
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        symbol = entry.get("symbol")
        if isinstance(symbol, str) and symbol.upper().startswith(root):
            return entry
    return None


def convert_webull_futures_bars(payload: Any, root: str) -> dict[str, Any]:
    """Convert one Webull futures bar response into analysis-ready bars.

    root must be one of MNQ, MES, or MGC. Contract codes such as MNQZ6 are
    matched by prefix, so an MES contract is never used for MNQ.
    """
    if root not in SUPPORTED_ROOTS:
        raise ValueError(f"Unsupported futures root: {root}")

    entry = _find_contract_entry(payload, root)
    base: dict[str, Any] = {
        "symbol": root,
        "source": SOURCE_NAME,
        "contract": None,
        "bars": [],
        "dropped_candles": 0,
        "delay_minutes": None,
        "order": "oldest_first",
        "provider_requests_triggered": 0,
    }
    if entry is None:
        return {**base, "status": "unavailable", "reason": f"No Webull contract found for {root}."}

    base["contract"] = entry.get("symbol")
    delay = entry.get("delay_minutes")
    base["delay_minutes"] = _number(delay)
    candles = entry.get("result")
    if not isinstance(candles, list):
        return {**base, "status": "unavailable", "reason": "Webull response has no candle list."}

    parsed: dict[datetime, dict[str, float]] = {}
    dropped = 0
    for candle in candles:
        if not isinstance(candle, dict):
            dropped += 1
            continue
        stamp = _parse_time(candle.get("time"))
        o = _number(candle.get("open"))
        h = _number(candle.get("high"))
        low = _number(candle.get("low"))
        c = _number(candle.get("close"))
        v = _number(candle.get("volume"))
        if stamp is None or None in (o, h, low, c):
            dropped += 1
            continue
        if h < low or c <= 0 or o <= 0:
            dropped += 1
            continue
        # Keep the first occurrence of a duplicated timestamp.
        parsed.setdefault(stamp, {"o": o, "h": h, "l": low, "c": c, "v": v if v is not None and v >= 0 else 0.0})

    bars = []
    for stamp in sorted(parsed):
        item = parsed[stamp]
        bars.append({"t": stamp.isoformat(), **item})

    base["bars"] = bars
    base["dropped_candles"] = dropped
    if not bars:
        return {**base, "status": "unavailable", "reason": "No valid candles after validation."}
    return {**base, "status": "ready", "reason": None}
