"""Multi-source sentiment fusion. Pure functions, no I/O.

Every source (Webull, Yahoo, news, catalysts, social) feeds evidence items into
fuse(). Each story is counted once, weighted by source, confidence, and age, and
combined into one score per instrument. decide() turns that score plus price and
the event gate into the bias label. Nothing else should compute a score.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

SOURCE_WEIGHT = {"webull": 1.0, "news": 0.8, "catalyst": 0.6, "yahoo": 0.4, "social": 0.2}
EXPECTED_SOURCES = ("webull", "yahoo")
HALF_LIFE_HOURS = 4.0       # a story loses half its weight every 4 hours
PRIOR_WEIGHT = 2.0          # pulls thin evidence toward zero
MIN_CLUSTERS = 3            # independent stories needed before a direction shows
BULL_THRESHOLD = 15.0
BEAR_THRESHOLD = -15.0
CONFLICT_SHARE = 0.3        # opposing weight share that makes the read "conflict"


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None
    return None


def story_key(item: dict[str, Any]) -> str:
    """Same story from different sources should share a key."""
    if item.get("story_key"):
        return str(item["story_key"])
    text = re.sub(r"[^a-z0-9 ]+", " ", str(item.get("title", "")).lower())
    normalized = " ".join(text.split())[:160]
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def fuse(evidence: list[dict[str, Any]], now: datetime,
         expected_sources: tuple[str, ...] = EXPECTED_SOURCES) -> dict[str, Any]:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    dropped = {"no_source": 0, "no_time": 0}
    seen_sources: set[str] = set()
    clusters: dict[str, list[dict[str, Any]]] = {}

    for raw in evidence:
        source = str(raw.get("source", "")).lower()
        if not source:
            dropped["no_source"] += 1
            continue
        seen_sources.add(source)
        published = _to_dt(raw.get("published"))
        if published is None:
            dropped["no_time"] += 1
            continue
        stance = max(-1.0, min(1.0, _num(raw.get("stance"), 0.0)))
        confidence = max(0.0, min(1.0, _num(raw.get("confidence"), 0.5)))
        item = {"source": source, "title": str(raw.get("title", "")),
                "published": published, "stance": stance, "confidence": confidence}
        clusters.setdefault(story_key(raw), []).append(item)

    bull_m = bear_m = total_m = total_contrib = 0.0
    scored: list[dict[str, Any]] = []

    for items in clusters.values():
        live = [i for i in items if i["stance"] != 0.0]
        if not live:
            continue
        best = max(live, key=lambda i: SOURCE_WEIGHT.get(i["source"], 0.1) * i["confidence"])
        age_hours = max(0.0, (now - best["published"]).total_seconds() / 3600.0)
        decay = 0.5 ** (age_hours / HALF_LIFE_HOURS)
        weight = SOURCE_WEIGHT.get(best["source"], 0.1) * best["confidence"] * decay
        contrib = best["stance"] * weight
        magnitude = abs(contrib)
        total_contrib += contrib
        total_m += magnitude
        if best["stance"] > 0:
            bull_m += magnitude
        else:
            bear_m += magnitude
        scored.append({"title": best["title"], "source": best["source"],
                       "sources": sorted({i["source"] for i in items}),
                       "stance": best["stance"], "age_hours": round(age_hours, 2),
                       "weight": round(weight, 3), "contrib": contrib})

    used = len(scored)
    score = 100.0 * total_contrib / (total_m + PRIOR_WEIGHT)

    if used < MIN_CLUSTERS:
        stance = "neutral"
    elif total_m > 0 and min(bull_m, bear_m) / total_m >= CONFLICT_SHARE:
        stance = "conflict"
    elif score >= BULL_THRESHOLD:
        stance = "bullish"
    elif score <= BEAR_THRESHOLD:
        stance = "bearish"
    else:
        stance = "neutral"

    got = [s for s in expected_sources if s in seen_sources]
    missing = [s for s in expected_sources if s not in seen_sources]
    ratio = len(got) / len(expected_sources) if expected_sources else 0.0

    if used >= 6 and ratio == 1.0 and abs(score) >= 30 and stance in ("bullish", "bearish"):
        confidence = "high"
    elif used >= MIN_CLUSTERS and ratio >= 0.5 and stance in ("bullish", "bearish"):
        confidence = "medium"
    else:
        confidence = "low"

    scored.sort(key=lambda c: abs(c["contrib"]), reverse=True)
    contributions = [{"title": c["title"], "source": c["source"], "sources": c["sources"],
                      "stance": c["stance"], "age_hours": c["age_hours"],
                      "weight": c["weight"],
                      "points": round(100.0 * c["contrib"] / (total_m + PRIOR_WEIGHT), 1)}
                     for c in scored[:5]]

    if used < MIN_CLUSTERS:
        reason = f"Thin evidence: {used} independent stories. Need {MIN_CLUSTERS}."
    elif stance == "conflict":
        reason = "Sources disagree on direction."
    else:
        reason = f"{used} independent stories from {len(got)} of {len(expected_sources)} sources."

    return {"score": round(score, 1), "stance": stance, "confidence": confidence,
            "clusters": used, "coverage": {"ratio": round(ratio, 2), "sources": got,
                                           "missing": missing},
            "dropped": dropped, "contributions": contributions, "reason": reason}


def decide(fused: dict[str, Any], price_relation: str | None,
           block: str | None = None) -> tuple[str, str]:
    """Bias label from fused stance, price confirmation, and the event gate.
    A block overrides everything. Price must confirm before a direction shows."""
    if block:
        return "NO NEW ENTRIES", f"Event block: {block}. No new entries until it passes."
    stance = fused["stance"]
    if stance == "conflict":
        return "WAIT", "Sources disagree. No directional read."
    if stance == "neutral":
        return "NEUTRAL", "No news edge. Price context only."
    direction = "LONG BIAS" if stance == "bullish" else "SHORT BIAS"
    if price_relation == "agrees":
        return direction, f"News {stance} and price agrees. Context for your own review."
    if price_relation == "diverges":
        return "WAIT", f"News {stance} but price disagrees. Wait for price to confirm."
    return "WAIT", f"News {stance}; price not confirming yet. Wait for price to confirm."