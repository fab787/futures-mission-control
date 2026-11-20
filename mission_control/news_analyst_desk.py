"""News Analyst desk: one recommendation per instrument, built from cached inputs only.

Combines Webull and Yahoo headlines, the stored economic calendar, and the cached
1-minute price. No provider calls. Never sends, approves, or sizes a trade.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from mission_control.event_windows import catalyst_state, entry_gate
from mission_control.news_analyst import analyze_instrument
from mission_control.news_fusion_adapter import fused_for_instrument

INSTRUMENTS = ("MNQ", "MES", "MGC")
HIGH_LIKE = frozenset({"high", "fomc_statement", "fomc_presser"})
STATE_ORDER = {"BLOCK": 0, "COOL": 1, "WARN": 2, "UPCOMING": 3, "RECENT": 4, "BACKGROUND": 5}
CATALYST_AHEAD_MIN = 72 * 60
CATALYST_BEHIND_MIN = 120
PRICE_LOOKBACK = 20
FLAT_PCT = 0.02


def _aware(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None
    return None


def _label(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in {"bullish", "bearish", "neutral"} else None


def headline_items(instrument: str, webull: list[dict], yahoo: list[dict]) -> list[dict]:
    items: list[dict] = []
    for raw in webull:
        if instrument not in (raw.get("futures_instruments") or []):
            continue
        label = _label(raw.get("sentiment"))
        items.append({
            "title": str(raw.get("title") or ""),
            "summary": str(raw.get("summary") or ""),
            "url": raw.get("url"),
            "publisher": raw.get("publisher"),
            "source": "webull",
            "label": label,
            "confidence": "medium" if label else "low",
            "published_at": _aware(raw.get("published_at")),
        })
    for raw in yahoo:
        label = _label(raw.get("sentiment") or raw.get("ai_sentiment"))
        items.append({
            "title": str(raw.get("title") or ""),
            "summary": str(raw.get("summary") or ""),
            "url": raw.get("url"),
            "publisher": raw.get("publisher") or "Yahoo Finance",
            "source": "yahoo",
            "label": label,
            "confidence": "low",
            "published_at": _aware(raw.get("published_at")),
        })
    return items


def build_catalysts(events: list[dict], now: datetime) -> list[dict]:
    rows: list[dict] = []
    for event in events or []:
        when = _aware(event.get("time_utc"))
        if when is None:
            continue
        minutes = (when - now).total_seconds() / 60.0
        if not (-CATALYST_BEHIND_MIN <= minutes <= CATALYST_AHEAD_MIN):
            continue
        importance = str(event.get("importance") or "low")
        rows.append({
            "name": str(event.get("name") or ""),
            "time_utc": when.isoformat(),
            "importance": importance,
            "state": catalyst_state(minutes, importance),
            "minutes_to": round(minutes, 1),
        })
    rows.sort(key=lambda row: (STATE_ORDER.get(row["state"], 9), abs(row["minutes_to"])))
    return rows


def event_gate(catalysts: list[dict]) -> tuple[str, str | None, bool]:
    worst: str | None = None
    name: str | None = None
    window = False
    for row in catalysts:
        if row["state"] in ("BLOCK", "COOL", "WARN"):
            if worst is None or STATE_ORDER[row["state"]] < STATE_ORDER[worst]:
                worst, name = row["state"], row["name"]
            if row["state"] in ("BLOCK", "WARN") and row["importance"] in HIGH_LIKE:
                window = True
    return worst or "NONE", name, window


PRICE_LOOKBACK_5M = 20          # five-minute bars, about 100 minutes
BAND_SIGMAS = 1.0               # a move must exceed this many sigmas of normal noise
NOISE_FLOOR_PCT = 0.02          # minimum band, in percent
MIN_FIVE_MINUTE_CLOSES = PRICE_LOOKBACK_5M + 1


def _one_minute_closes(bars) -> list[float]:
    closes: list[float] = []
    for bar in bars if isinstance(bars, list) else []:
        try:
            value = float(bar.get("c"))
        except (AttributeError, TypeError, ValueError):
            continue
        if value > 0:
            closes.append(value)
    return closes


def _five_minute_closes(closes_1m: list[float]) -> list[float]:
    """Last close of each complete five-bar block, aligned so the newest close is included."""
    offset = len(closes_1m) % 5
    return closes_1m[offset:][4::5]


def price_check(market: dict | None, instrument: str) -> tuple[str | None, str]:
    item = next(
        (entry for entry in (market or {}).get("instruments", []) if entry.get("symbol") == instrument),
        None,
    )
    bars = item.get("bars", []) if isinstance(item, dict) else []
    five = _five_minute_closes(_one_minute_closes(bars))
    if len(five) < MIN_FIVE_MINUTE_CLOSES:
        return None, "Not enough cached one-minute bars for a five-minute price check (about 105 needed)."
    tail = five[-MIN_FIVE_MINUTE_CLOSES:]
    returns = [abs(b - a) / a for a, b in zip(tail, tail[1:])]
    mean_abs = sum(returns) / len(returns)
    sigma_pct = 100.0 * mean_abs * (PRICE_LOOKBACK_5M ** 0.5) * 1.25
    band = max(NOISE_FLOOR_PCT, BAND_SIGMAS * sigma_pct)
    last, reference = tail[-1], tail[0]
    move = (last - reference) / reference * 100.0
    if move > band:
        direction = "up"
    elif move < -band:
        direction = "down"
    else:
        direction = "flat"
    return direction, f"{direction} {move:+.2f}% over {PRICE_LOOKBACK_5M} five-minute bars; noise band ±{band:.2f}% (last {last:g})."


def price_relation(stance: str, direction: str | None) -> str | None:
    if direction in (None, "flat"):
        return None
    if stance == "bullish":
        return "agrees" if direction == "up" else "diverges"
    if stance == "bearish":
        return "agrees" if direction == "down" else "diverges"
    return None


def recommendation(stance: str, relation: str | None, gate: str, block_name: str | None,
                   headline_count: int) -> tuple[str, str]:
    if headline_count == 0:
        bias, text = "INSUFFICIENT DATA", "No cached headlines for this instrument yet."
    elif stance == "conflict":
        bias, text = "WAIT", "Sources disagree. No directional read."
    elif stance == "neutral":
        bias, text = "NEUTRAL", "No news edge. Price context only."
    else:
        direction_bias = "LONG BIAS" if stance == "bullish" else "SHORT BIAS"
        if relation == "agrees":
            bias, text = direction_bias, f"News {stance} and price agrees. Context for your own review."
        elif relation == "diverges":
            bias, text = "WAIT", f"News {stance} but price disagrees. Wait for price to confirm."
        else:
            bias, text = "WAIT", f"News {stance}; price not confirming yet. Wait for price to confirm."
    if gate == "BLOCK":
        bias = "NO NEW ENTRIES"
        text = f"Event block: {block_name}. No new entries until it passes."
    elif gate == "COOL":
        text += " Recent event: take entries only after a closed-bar confirmation."
    elif gate == "WARN":
        text += " Event approaching: review before acting."
    return bias, text


def build_news_analyst_desk(
    *,
    yahoo_items: list[dict],
    webull_items: list[dict],
    calendar_events: list[dict],
    market_snapshot: dict | None,
    now: datetime,
    calendar_status: dict | None = None,
) -> dict[str, Any]:
    now_utc = _aware(now)
    if now_utc is None:
        raise ValueError("now must be timezone-aware")
    catalysts = build_catalysts(calendar_events, now_utc)
    worst, block_name, window = event_gate(catalysts)
    gate = entry_gate(worst)

    instruments: list[dict[str, Any]] = []
    for instrument in INSTRUMENTS:
        items = headline_items(instrument, webull_items, yahoo_items)
        direction, detail = price_check(market_snapshot, instrument)
        fused = fused_for_instrument(instrument, webull_items, yahoo_items, now_utc)
        result = analyze_instrument(instrument, items, [], now_utc, price_check=None)
        relation = price_relation(fused["stance"], direction)
        headline_count = sum(result["buckets"].values())
        bias, text = recommendation(fused["stance"], relation, worst, block_name, headline_count)
        instruments.append({
            "instrument": instrument,
            "bias": bias,
            "recommendation": text,
            "score": fused["score"],
            "stance": fused["stance"],
            "confidence": {"label": fused["confidence"], "value": {"low": 0.3, "medium": 0.6, "high": 0.85}.get(fused["confidence"], 0.3)},
            "fused": fused,
            "news_state": result["state"],
            "event_gate": gate,
            "event_window": window,
            "price": {"check": relation or direction, "direction": direction, "relation": relation, "detail": detail},
            "headline_count": headline_count,
            "source_counts": {
                "webull": sum(1 for item in items if item["source"] == "webull"),
                "yahoo": sum(1 for item in items if item["source"] == "yahoo"),
            },
            "top_headlines": result["top_headlines"],
            "counts": result["counts"],
        })

    return {
        "generated_at": now_utc.isoformat(),
        "instruments": instruments,
        "catalysts": catalysts,
        "event_gate": worst,
        "calendar": calendar_status or {},
        "note": "The analyst confirms or warns. It never sends, approves, or sizes a trade.",
        "trade_authority": "none",
        "order_execution": "disabled",
        "human_approval_required": True,
    }

def _has_bars(snapshot: dict | None) -> bool:
    for entry in (snapshot or {}).get("instruments", []) or []:
        if isinstance(entry, dict) and isinstance(entry.get("bars"), list) and entry["bars"]:
            return True
    return False


def choose_market_snapshot(primary: dict | None, fallback: dict | None) -> dict | None:
    """Use the primary snapshot (Webull) when it has bars; otherwise the fallback (TradingView)."""
    return primary if _has_bars(primary) else fallback