"""Cache-only News & Catalyst Intelligence specialist.

Triage only. Classifies cached headlines by theme, sets priority, notes which
futures a headline may relate to (keyword hints, not mapping), and sorts them.
It never fetches news, never infers market direction, and never invents event rows.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

# Whole-word patterns, matched case-insensitively.
THEMES: dict[str, tuple[str, ...]] = {
    "monetary_policy": (
        r"\bfed\b", r"\bfederal reserve\b", r"\bfomc\b", r"\bpowell\b", r"\bwarsh\b",
        r"\bwaller\b", r"\bgoolsbee\b", r"\bmusalem\b", r"\brate (?:cut|hike)s?\b",
        r"\binterest[- ]rates?\b", r"\bcentral bank\b", r"\bhikes?\b", r"\bfedwatch\b",
    ),
    "macro_data": (
        r"\bcpi\b", r"\bpce\b", r"\binflation\b", r"\bpayrolls?\b", r"\bnonfarm\b",
        r"\bjobs report\b", r"\bunemployment\b", r"\bgdp\b", r"\bpmi\b", r"\bretail sales\b",
        r"\bjobless claims\b", r"\bproducer price\b", r"\bconsumer price\b",
    ),
    "geopolitical_trade": (
        r"\btariffs?\b", r"\bsanctions?\b", r"\btrade war\b", r"\bgeopolitical\b",
        r"\bmilitary\b", r"\binvasion\b", r"\bconflict\b", r"\bceasefire\b", r"\biranian?\b",
    ),
    "market_session": (
        r"\bcloses?\b", r"\bclosing bell\b", r"\bclos(?:e|ed|ing) (?:higher|lower|flat)\b",
        r"\bends? (?:higher|lower|flat)\b", r"\bended (?:higher|lower|flat)\b",
        r"\bafter[- ]hours\b", r"\bpre-?market\b", r"\bfutures\b", r"\bmarket today\b",
        r"\bwall street\b", r"\bsession\b",
    ),
    "earnings": (
        r"\bearnings\b", r"\bquarterly results\b", r"\bq[1-4]\b", r"\bfiscal 20\d\d\b",
        r"\bguidance\b", r"\brevenue\b", r"\beps\b",
        r"\b(?:beats?|miss(?:es|ed)?) (?:estimates?|expectations|the (?:street|consensus))\b",
    ),
    "ipo_listing": (
        r"\bipo\b", r"\bdebut\b", r"\bgoes public\b", r"\bpriced (?:its|shares)\b", r"\blisting\b",
    ),
    "equity_index": (
        r"\bnasdaq\b", r"\bs&p 500\b", r"\bdow( jones)?\b", r"\bstock market\b",
        r"\bequit(?:y|ies)\b", r"\betfs?\b", r"\bqqq\b", r"\bspy\b", r"\bindex funds?\b",
    ),
    "precious_metals": (
        r"\bgold\b", r"\bsilver\b", r"\bbullion\b", r"\bprecious metals?\b", r"\bgld\b",
        r"\bcomex\b", r"\bminers?\b",
    ),
    "energy_commodities": (
        r"\bcrude\b", r"\bwti\b", r"\bbrent\b", r"\bnatural gas\b", r"\bopec\b", r"\boil\b",
    ),
    "ai_technology": (
        r"\bartificial intelligence\b", r"\bai\b", r"\bsemiconductors?\b", r"\bchips?\b",
        r"\bnvidia\b", r"\bdata centers?\b", r"\bsoftware\b",
    ),
}
BROAD_THEMES = {"monetary_policy", "macro_data", "geopolitical_trade"}
INSTRUMENT_HINTS: dict[str, tuple[str, ...]] = {
    "MNQ": (r"\bnasdaq\b", r"\bqqq\b", r"\btech\b", r"\bai\b", r"\bsemiconductors?\b", r"\bnvidia\b", r"\bchips?\b"),
    "MES": (r"\bs&p\b", r"\bspy\b", r"\bdow\b", r"\bbroad market\b", r"\bstock market\b", r"\bequit(?:y|ies)\b"),
    "MGC": (r"\bgold\b", r"\bsilver\b", r"\bbullion\b", r"\bgld\b", r"\bprecious metals?\b"),
}
_BOILERPLATE = re.compile(r"Today's big story:[^.]*\.", re.IGNORECASE)
_THEME_RE = {k: [re.compile(p, re.IGNORECASE) for p in v] for k, v in THEMES.items()}
_HINT_RE = {k: [re.compile(p, re.IGNORECASE) for p in v] for k, v in INSTRUMENT_HINTS.items()}
_PRIORITY_RANK = {"high": 0, "medium": 1, "unclassified": 2}


def _clean(text: str) -> str:
    return _BOILERPLATE.sub("", text).strip()


def _classify(item: dict[str, Any]) -> dict[str, Any]:
    title = str(item.get("title") or "").strip()
    summary = _clean(str(item.get("summary") or ""))
    text = f"{title} {summary}"
    themes = [name for name, pats in _THEME_RE.items() if any(p.search(text) for p in pats)]
    hints = [sym for sym, pats in _HINT_RE.items() if any(p.search(text) for p in pats)]
    broad = any(t in BROAD_THEMES for t in themes)
    if broad:
        priority, affects = "high", "broad_macro"
    elif themes:
        priority, affects = "medium", "instrument_specific" if hints else "market_context"
    else:
        priority, affects = "unclassified", "unknown"
    return {
        "id": item.get("id"),
        "title": title,
        "summary": summary,
        "publisher": item.get("publisher") or "Yahoo Finance",
        "url": item.get("url"),
        "published_at": item.get("published_at"),
        "received_at": item.get("received_at"),
        "themes": themes,
        "priority": priority,
        "affects": affects,
        "instrument_hints": hints,
        "directional_bias": "not_assessed",
        "instrument_mapping": "keyword_hint_only",
    }


def _dedupe_key(title: str) -> str:
    return re.sub(r"\W+", " ", title.lower()).strip()[:90]


def analyze_news_catalysts(news_payload: dict[str, Any] | None) -> dict[str, Any]:
    """Classify an already-cached Yahoo/Webull payload without provider I/O."""
    base = {
        "specialist_id": "news_catalyst_intelligence",
        "specialist_name": "News & Catalyst Intelligence",
        "provider_requests_triggered": 0,
        "trade_authority": "none",
        "risk_authority": "none",
        "calendar": {
            "status": "page_link_only",
            "source": "Yahoo Finance Economic Calendar",
            "events": [],
            "note": "Structured event extraction is not verified. No event rows or release times are inferred.",
        },
    }
    if not news_payload or news_payload.get("status") == "not_loaded":
        return {
            **base,
            "status": "blocked",
            "reason": "No cached headlines are available; this endpoint does not fetch them.",
            "source_status": "not_loaded",
            "headlines": [],
            "summary": {"headline_count": 0, "high_priority": 0, "medium_priority": 0, "unclassified": 0, "by_theme": {}},
        }

    source_status = str(news_payload.get("status") or "unknown")
    if source_status in {"unavailable", "cooldown"}:
        return {
            **base,
            "status": "blocked",
            "reason": "Headline cache has no usable content.",
            "source_status": source_status,
            "headlines": [],
            "summary": {"headline_count": 0, "high_priority": 0, "medium_priority": 0, "unclassified": 0, "by_theme": {}},
        }

    raw_items = news_payload.get("items")
    if not isinstance(raw_items, list):
        raw_items = []

    seen: set[str] = set()
    headlines: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        key = _dedupe_key(str(item["title"]))
        if not key or key in seen:
            continue
        seen.add(key)
        headlines.append(_classify(item))

    headlines.sort(key=lambda h: str(h.get("published_at") or ""), reverse=True)
    headlines.sort(key=lambda h: _PRIORITY_RANK[h["priority"]])

    by_theme = Counter(theme for h in headlines for theme in h["themes"])
    status = "stale_context" if source_status == "stale" else "ready"
    return {
        **base,
        "status": status,
        "reason": "Headlines are stale and shown only as historical context." if status == "stale_context" else None,
        "source": news_payload.get("source", "Yahoo Finance RSS"),
        "source_status": source_status,
        "fetched_at": news_payload.get("fetched_at"),
        "cache_age_seconds": news_payload.get("cache_age_seconds"),
        "feed_symbols": news_payload.get("symbols", []),
        "headline_scope": "Combined watchlist feeds. Headlines are not reliably mapped to MNQ, MES, or MGC by the sources; instrument hints are keyword-based only.",
        "headlines": headlines,
        "summary": {
            "headline_count": len(headlines),
            "high_priority": sum(h["priority"] == "high" for h in headlines),
            "medium_priority": sum(h["priority"] == "medium" for h in headlines),
            "unclassified": sum(h["priority"] == "unclassified" for h in headlines),
            "by_theme": dict(by_theme.most_common()),
        },
        "interpretation": "Themes, priority, affects, and instrument hints are keyword-based triage only. They do not establish market impact, direction, surprise, or trade opportunity.",
    }
