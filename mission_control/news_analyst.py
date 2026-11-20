"""News Analyst: evidence-adjusted score, relevance filter, time buckets, catalyst timing.

Pure functions. No network, no cache reads, no order fields. The output confirms or
warns for the agent desk. It never generates a trade.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
K = 2.0
HALF_LIFE_HOURS = 6.0
SOURCE_WEIGHT = {"webull": 0.9, "yahoo": 0.6, "calendar": 1.0, "etf_social": 0.3}
LABEL_CONFIDENCE = {"high": 0.9, "medium": 0.6, "low": 0.3}
STANCE_THRESHOLD = 25.0
CONFLICT_SIDE_MIN = 1.0
PRE_EVENT_MIN = 15
POST_EVENT_MIN = 30
IMMINENT_MIN = 60
UPCOMING_MIN = 240
ELEVATED_WEIGHT = 1.0
INSTRUMENTS = ("MNQ", "MES", "MGC")

# Direction rules: (pattern, {instrument: sign}). Editable. Each entry needs approval before use.
DIRECTION_RULES: tuple[tuple[str, dict[str, int]], ...] = (
    (r"\bhawkish\b|\brate hikes?\b|\bhikes?\b|\bhigher for longer\b", {"MNQ": -1, "MES": -1, "MGC": -1}),
    (r"\bdovish\b|\brate cuts?\b", {"MNQ": 1, "MES": 1, "MGC": 1}),
    (r"\bgold (?:drops?|falls?|slides?|slips?|dips?|sinks?)\b", {"MGC": -1}),
    (r"\bgold (?:rises?|jumps?|climbs?|surges?|gains?)\b", {"MGC": 1}),
)

_MACRO = re.compile(
    r"\b(?:fed|fomc|powell|cpi|pce|payrolls?|nonfarm|inflation|tariffs?|hormuz|oil|crude|"
    r"gold|silver|nasdaq|s&p|dow|futures|treasur(?:y|ies)|rate cuts?|rate hikes?|yields?)\b",
    re.IGNORECASE,
)
_SINGLE_STOCK = re.compile(
    r"\((?:NASDAQ|NYSE|AMEX|OTC):[A-Z.]+\)|after[- ]hours|"
    r"\b(?:stock|shares?) (?:jumps?|surges?|tumbles?|drops?|falls?|soars?|slides?|sinks?)\b",
    re.IGNORECASE,
)


def _require_aware(moment: datetime, label: str) -> None:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def is_relevant(title: str, summary: str = "") -> bool:
    """Keep macro and futures-relevant items. Drop single-company movers with no macro term in the title."""
    text = f"{title} {summary}"
    if not _MACRO.search(text):
        return False
    if _SINGLE_STOCK.search(title) and not _MACRO.search(title):
        return False
    return True


def _tokens(title: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", title.lower()))


def is_duplicate(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a.get("url") and a.get("url") == b.get("url"):
        return True
    ta, tb = _tokens(str(a.get("title", ""))), _tokens(str(b.get("title", "")))
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.8


def headline_stance(item: dict[str, Any], instrument: str) -> int:
    """+1 bullish, -1 bearish, 0 neutral or unclear. A source label wins; otherwise the direction table."""
    label = str(item.get("label") or "").strip().lower()
    if label in ("bullish", "bearish", "neutral"):
        return {"bullish": 1, "bearish": -1, "neutral": 0}[label]
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    signs = []
    for pattern, mapping in DIRECTION_RULES:
        if instrument in mapping and re.search(pattern, text, re.IGNORECASE):
            signs.append(mapping[instrument])
    if not signs or any(s != signs[0] for s in signs):
        return 0
    return signs[0]


def item_weight(item: dict[str, Any], now: datetime) -> float:
    source = SOURCE_WEIGHT.get(str(item.get("source") or ""), 0.3)
    confidence = LABEL_CONFIDENCE.get(str(item.get("confidence") or "low").lower(), 0.3)
    age_h = max(0.0, (now - item["published_at"]).total_seconds() / 3600.0)
    return source * confidence * 0.5 ** (age_h / HALF_LIFE_HOURS)


def bucket_of(published: datetime, now: datetime, priority_high: bool) -> str:
    age_h = (now - published).total_seconds() / 3600.0
    if age_h <= 6:
        return "last_6h"
    if published.astimezone(NY).date() == now.astimezone(NY).date():
        return "today"
    if age_h > 48 or (not priority_high and age_h > 24):
        return "background"
    return "earlier"


def catalyst_state(minutes_to: float) -> str:
    if -POST_EVENT_MIN <= minutes_to <= PRE_EVENT_MIN:
        return "ACTIVE"
    if PRE_EVENT_MIN < minutes_to <= IMMINENT_MIN:
        return "IMMINENT"
    if IMMINENT_MIN < minutes_to <= UPCOMING_MIN:
        return "UPCOMING"
    if -UPCOMING_MIN <= minutes_to < -POST_EVENT_MIN:
        return "RECENT"
    return "BACKGROUND"


def catalysts_for(instrument: str, events: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    _require_aware(now, "now")
    rows = []
    for ev in events:
        scope = ev.get("instruments") or ["ALL"]
        if "ALL" not in scope and instrument not in scope:
            continue
        when = ev.get("time")
        if not isinstance(when, datetime) or when.tzinfo is None:
            continue
        minutes_to = (when - now).total_seconds() / 60.0
        rows.append({
            "name": str(ev.get("name", "")),
            "time_ny": when.astimezone(NY).strftime("%I:%M %p ET"),
            "importance": str(ev.get("importance", "medium")),
            "minutes_to": round(minutes_to, 1),
            "state": catalyst_state(minutes_to),
        })
    return rows


def analyze_instrument(
    instrument: str,
    items: list[dict[str, Any]],
    events: list[dict[str, Any]],
    now: datetime,
    price_check: str | None = None,
) -> dict[str, Any]:
    _require_aware(now, "now")
    if instrument not in INSTRUMENTS:
        raise ValueError(f"unknown instrument: {instrument}")
    if price_check not in (None, "agrees", "diverges"):
        raise ValueError("price_check must be None, 'agrees', or 'diverges'")

    counts = {"received": len(items), "dropped_no_time": 0, "dropped_future": 0,
              "dropped_irrelevant": 0, "dropped_duplicate": 0}
    kept: list[dict[str, Any]] = []
    for it in items:
        when = it.get("published_at")
        if not isinstance(when, datetime) or when.tzinfo is None:
            counts["dropped_no_time"] += 1
            continue
        if when > now:
            counts["dropped_future"] += 1
            continue
        if not is_relevant(str(it.get("title", "")), str(it.get("summary", ""))):
            counts["dropped_irrelevant"] += 1
            continue
        if any(is_duplicate(it, k) for k in kept):
            counts["dropped_duplicate"] += 1
            continue
        kept.append(it)

    rows = []
    for it in kept:
        priority_high = bool(_MACRO.search(f"{it.get('title', '')} {it.get('summary', '')}"))
        rows.append({
            "item": it,
            "s": headline_stance(it, instrument),
            "w": item_weight(it, now),
            "bucket": bucket_of(it["published_at"], now, priority_high),
        })

    num = sum(r["w"] * r["s"] for r in rows)
    den = sum(r["w"] for r in rows)
    score = 100.0 * num / (den + K) if rows else 0.0
    bull = sum(r["w"] for r in rows if r["s"] > 0)
    bear = sum(r["w"] for r in rows if r["s"] < 0)
    directional = bull + bear
    confidence = directional / (directional + K) if directional > 0 else 0.0
    conf_label = "high" if confidence >= 0.7 else "medium" if confidence >= 0.4 else "low"

    if bull >= CONFLICT_SIDE_MIN and bear >= CONFLICT_SIDE_MIN:
        stance = "conflict"
    elif abs(score) < STANCE_THRESHOLD:
        stance = "neutral"
    else:
        stance = "bullish" if score > 0 else "bearish"

    catalysts = catalysts_for(instrument, events, now)
    event_window = any(c["state"] in ("ACTIVE", "IMMINENT") and c["importance"] == "high" for c in catalysts)
    recent_w = sum(r["w"] for r in rows if r["s"] != 0 and r["bucket"] == "last_6h")
    if event_window:
        state = "event_window"
    elif recent_w >= ELEVATED_WEIGHT:
        state = "elevated"
    else:
        state = "normal"

    directional_stance = stance in ("bullish", "bearish")
    if state == "event_window":
        action = "warn"
    elif directional_stance and price_check == "agrees" and conf_label != "low":
        action = "confirm"
    elif directional_stance and price_check == "diverges":
        action = "warn"
    else:
        action = "none"

    top = sorted((r for r in rows if r["s"] != 0), key=lambda r: r["w"], reverse=True)[:3]
    buckets: dict[str, int] = {}
    for r in rows:
        buckets[r["bucket"]] = buckets.get(r["bucket"], 0) + 1

    return {
        "instrument": instrument,
        "score": round(score, 1),
        "stance": stance,
        "confidence": {"value": round(confidence, 2), "label": conf_label},
        "state": state,
        "action": action,
        "top_headlines": [
            {
                "title": str(r["item"].get("title", "")),
                "source": str(r["item"].get("source", "")),
                "published_ny": r["item"]["published_at"].astimezone(NY).strftime("%b %d, %I:%M %p ET"),
                "stance": r["s"],
                "weight": round(r["w"], 3),
            }
            for r in top
        ],
        "buckets": buckets,
        "catalysts": catalysts,
        "counts": counts,
        "interpretation": "Keyword and source-based triage. Confirms or warns; never sets a trade or veto.",
    }
