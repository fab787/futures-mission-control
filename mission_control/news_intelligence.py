"""Transparent per-instrument news sentiment derived only from source labels."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mission_control.news_catalyst import analyze_news_catalysts

PROXIES = {"MNQ": "QQQ", "MES": "SPY", "MGC": "GLD"}
VALID = {"bullish", "bearish", "neutral"}
VALID_AGGREGATED = VALID | {"mixed"}
MAX_BIAS_AGE_SECONDS = 24 * 60 * 60


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _stance(items: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"bullish": 0, "bearish": 0, "neutral": 0}
    newest: datetime | None = None
    now = datetime.now(UTC)
    considered = 0
    for item in items:
        sentiment = str(item.get("sentiment") or "").lower()
        published = _time(item.get("published_at"))
        if sentiment not in VALID or published is None:
            continue
        age = (now - published).total_seconds()
        if age < -300 or age > MAX_BIAS_AGE_SECONDS:
            continue
        counts[sentiment] += 1
        considered += 1
        if newest is None or published > newest:
            newest = published
    positive, negative, neutral = counts["bullish"], counts["bearish"], counts["neutral"]
    if considered == 0:
        bias = "unavailable"
    elif positive and negative:
        bias = "mixed"
    elif positive:
        bias = "bullish"
    elif negative:
        bias = "bearish"
    else:
        bias = "neutral"
    confidence = "unavailable" if considered == 0 else "low" if considered < 3 else "medium"
    return {
        "stance": bias,
        "confidence": confidence,
        "article_count": considered,
        "sentiment_counts": counts,
        "latest_article_at": newest.isoformat() if newest else None,
        "freshness_window_hours": 24,
        "assessment_method": "count of Webull's native per-article POSITIVE/NEGATIVE/NEUTRAL labels; no sentiment is inferred from headlines",
    }


def build_instrument_news_intelligence(
    yahoo_payload: dict[str, Any] | None,
    webull_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build Yahoo/Webull source views and a conservative combined bias."""
    yahoo_payload = yahoo_payload or {}
    webull_payload = webull_payload or {}
    yahoo_items = yahoo_payload.get("items") if isinstance(yahoo_payload.get("items"), list) else []
    webull_items = webull_payload.get("items") if isinstance(webull_payload.get("items"), list) else []
    catalyst_items: list[dict[str, Any]] = []
    for item in yahoo_items + webull_items:
        if isinstance(item, dict) and item.get("title"):
            catalyst_items.append({**item, "publisher": item.get("publisher") or item.get("source") or "Unknown publisher"})
    source_statuses = {str(yahoo_payload.get("status") or ""), str(webull_payload.get("status") or "")}
    if not catalyst_items:
        catalyst_status = "not_loaded"
    elif "fresh" in source_statuses or "partial" in source_statuses:
        catalyst_status = "fresh"
    elif "cached" in source_statuses:
        catalyst_status = "cached"
    elif "stale" in source_statuses and not source_statuses.intersection({"fresh", "partial", "cached"}):
        catalyst_status = "stale"
    else:
        catalyst_status = "cached"
    catalyst_report = analyze_news_catalysts({
        "status": catalyst_status,
        "source": "Yahoo Finance RSS + Webull Cloud MCP",
        "items": catalyst_items,
    })
    output = []
    for instrument, proxy in PROXIES.items():
        # Yahoo's combined RSS response has no reliable item-to-instrument mapping.
        # Only honor Yahoo sentiment if a future payload explicitly includes a
        # symbol/proxy and a valid native sentiment field.
        yahoo_scoped = [
            item for item in yahoo_items
            if isinstance(item, dict)
            and str(item.get("symbol") or item.get("proxy_symbol") or "").upper() in {instrument, proxy}
            and str(item.get("sentiment") or "").lower() in VALID
        ]
        webull_scoped = [
            item for item in webull_items
            if isinstance(item, dict)
            and instrument in (item.get("futures_instruments") or [])
            and item.get("sentiment") in VALID
        ]
        yahoo_bias = _stance(yahoo_scoped)
        webull_bias = _stance(webull_scoped)
        yahoo_bias.update({"status": "available" if yahoo_bias["article_count"] else "not_provided", "source": "Yahoo Finance RSS"})
        webull_bias.update({"status": "available" if webull_bias["article_count"] else "unavailable_or_stale", "source": "Webull Cloud MCP", "proxy_symbol": proxy})

        available = [item for item in (yahoo_bias, webull_bias) if item["stance"] in VALID_AGGREGATED]
        if len(available) == 2:
            stances = {item["stance"] for item in available}
            combined_stance = next(iter(stances)) if len(stances) == 1 else "mixed"
            combined_status = "multi_source_agreement" if len(stances) == 1 else "source_disagreement"
            combined_confidence = "medium" if len(stances) == 1 and all(item["confidence"] == "medium" for item in available) else "low"
        elif len(available) == 1:
            combined_stance = available[0]["stance"]
            combined_status = "single_source"
            combined_confidence = "low" if available[0]["confidence"] == "unavailable" else available[0]["confidence"]
        else:
            combined_stance = "unavailable"
            combined_status = "no_fresh_sentiment"
            combined_confidence = "unavailable"

        output.append({
            "symbol": instrument,
            "proxy_symbol": proxy,
            "yahoo_bias": yahoo_bias,
            "webull_bias": webull_bias,
            "combined_bias": {
                "stance": combined_stance,
                "status": combined_status,
                "confidence": combined_confidence,
                "source_count": len(available),
                "sources": [item["source"] for item in available],
                "disclosure": (
                    "Yahoo does not supply a sentiment field for this item set; combined bias uses Webull native article sentiment only."
                    if len(available) == 1 and available[0]["source"] == "Webull Cloud MCP" else
                    "Combined bias compares the available provider sentiment fields; mixed indicates conflicting source labels."
                    if available else
                    "No fresh provider sentiment is available; no directional bias is inferred."
                ),
            },
        })

    return {
        "status": "ready" if any(item["combined_bias"]["source_count"] for item in output) else "awaiting_news",
        "instruments": output,
        "catalyst_watch": {
            "status": catalyst_report.get("status", "blocked"),
            "headlines": catalyst_report.get("headlines", []),
            "summary": catalyst_report.get("summary", {}),
            "interpretation": catalyst_report.get("interpretation", "Catalysts are keyword-tagged for review only; no directional market impact is inferred."),
            "provider_requests_triggered": 0,
        },
        "sources": {
            "yahoo": {"status": yahoo_payload.get("status", "not_loaded"), "headline_count": len(yahoo_items), "sentiment_field": "not supplied by the currently observed Yahoo RSS payload"},
            "webull": {"status": webull_payload.get("status", "not_loaded"), "headline_count": len(webull_items), "proxy_symbols": PROXIES},
        },
        "limitations": [
            "News bias uses native provider article sentiment labels only; headline text is not assigned a fabricated sentiment.",
            "Webull proxy mapping is QQQ to MNQ, SPY to MES, and GLD to MGC; these are related instruments, not futures-native news feeds.",
            "Only articles with valid timestamps no older than 24 hours contribute to bias.",
            "Confidence is capped at medium because this is a single provider's article sentiment, not independent confirmation or a probability.",
            "News bias is contextual information, not a trade signal or execution instruction.",
        ],
        "provider_requests_triggered": 0,
        "trade_authority": "none",
        "risk_authority": "none",
        "execution": "disabled",
        "human_approval_required": True,
    }
