"""Cache-only News & Catalyst Intelligence specialist.

Classifies cached Yahoo Finance headlines by broad catalyst theme. It does not
fetch news, invent event-calendar rows, or infer article-to-contract mapping.
"""
from __future__ import annotations

from typing import Any

THEMES: dict[str, tuple[str, ...]] = {
    "monetary_policy": (
        "federal reserve", "fed ", "fomc", "powell", "interest rate",
        "rate cut", "rate hike", "central bank", "monetary policy",
    ),
    "macro_economic_data": (
        "cpi", "pce", "inflation", "payroll", "nonfarm", "jobs report",
        "unemployment", "gdp", "pmi", "retail sales", "jobless claims",
        "producer price", "consumer price",
    ),
    "geopolitical_trade": (
        "tariff", "sanction", "trade war", "geopolitical", "military",
        "invasion", "conflict", "ceasefire", "oil embargo",
    ),
    "equity_index": (
        "nasdaq", "s&p 500", "dow jones", "stock market", "equities",
        "tech stocks", "semiconductor", "mega-cap",
    ),
    "technology_earnings": (
        "earnings", "revenue", "guidance", "quarterly results",
        "artificial intelligence", "chipmaker", "semiconductor",
    ),
    "precious_metals": (
        "gold", "silver", "bullion", "precious metals", "comex",
    ),
    "energy_commodities": (
        "crude oil", "wti", "brent", "natural gas", "opec",
    ),
}
HIGH_PRIORITY = {"monetary_policy", "macro_economic_data", "geopolitical_trade"}


def _classify(item: dict[str, Any]) -> dict[str, Any]:
    title = str(item.get("title") or "")
    summary = str(item.get("summary") or "")
    text = f" {title} {summary} ".lower()
    matched = [
        theme
        for theme, keywords in THEMES.items()
        if any(keyword in text for keyword in keywords)
    ]
    if any(theme in HIGH_PRIORITY for theme in matched):
        priority = "high"
    elif matched:
        priority = "medium"
    else:
        priority = "unclassified"
    return {
        "id": item.get("id"),
        "title": title,
        "summary": summary,
        "publisher": item.get("publisher") or "Yahoo Finance",
        "url": item.get("url"),
        "published_at": item.get("published_at"),
        "received_at": item.get("received_at"),
        "themes": matched,
        "priority": priority,
        "directional_bias": "not_assessed",
        "instrument_mapping": "unavailable_from_combined_feed",
    }


def analyze_news_catalysts(news_payload: dict[str, Any] | None) -> dict[str, Any]:
    """Classify an already-cached Yahoo RSS payload without provider I/O."""
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
            "note": (
                "Structured event extraction is not verified. No event rows or "
                "release times are inferred."
            ),
        },
    }
    if not news_payload or news_payload.get("status") == "not_loaded":
        return {
            **base,
            "status": "blocked",
            "reason": "No cached Yahoo headlines are available; this endpoint does not fetch them.",
            "source_status": "not_loaded",
            "headlines": [],
            "summary": {"headline_count": 0, "high_priority": 0, "unclassified": 0},
        }

    source_status = str(news_payload.get("status") or "unknown")
    if source_status in {"unavailable", "cooldown"}:
        return {
            **base,
            "status": "blocked",
            "reason": "Yahoo headline cache has no usable content.",
            "source_status": source_status,
            "headlines": [],
            "summary": {"headline_count": 0, "high_priority": 0, "unclassified": 0},
        }

    raw_items = news_payload.get("items")
    if not isinstance(raw_items, list):
        raw_items = []
    headlines = [
        _classify(item)
        for item in raw_items
        if isinstance(item, dict) and item.get("title")
    ]
    status = "stale_context" if source_status == "stale" else "ready"
    return {
        **base,
        "status": status,
        "reason": (
            "Headlines are stale and shown only as historical context."
            if status == "stale_context" else None
        ),
        "source": news_payload.get("source", "Yahoo Finance RSS"),
        "source_status": source_status,
        "fetched_at": news_payload.get("fetched_at"),
        "cache_age_seconds": news_payload.get("cache_age_seconds"),
        "feed_symbols": news_payload.get("symbols", []),
        "headline_scope": (
            "Combined watchlist feed. Individual headlines are not reliably mapped "
            "to MNQ, MES, or MGC by the source."
        ),
        "headlines": headlines,
        "summary": {
            "headline_count": len(headlines),
            "high_priority": sum(item["priority"] == "high" for item in headlines),
            "medium_priority": sum(item["priority"] == "medium" for item in headlines),
            "unclassified": sum(item["priority"] == "unclassified" for item in headlines),
        },
        "interpretation": (
            "Theme and priority labels are keyword-based triage only. They do not "
            "establish market impact, direction, surprise, or trade opportunity."
        ),
    }
