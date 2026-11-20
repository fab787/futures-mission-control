"""Adds one fused score per instrument to the news intelligence payload.

Additive only: yahoo_bias, webull_bias and combined_bias are unchanged. The
dashboard can switch to 'fused' and 'fused_overall' once it is wired.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from mission_control.news_intelligence import PROXIES
from mission_control.sentiment_fusion import decide, fuse

LABEL_STANCE = {"POSITIVE": 1.0, "BULLISH": 1.0, "NEGATIVE": -1.0, "BEARISH": -1.0, "NEUTRAL": 0.0}
PROVIDER_CONFIDENCE = 0.8
AI_ESTIMATE_CONFIDENCE = 0.3


def _webull_scope(item: dict[str, Any], instrument: str) -> bool:
    return instrument in (item.get("futures_instruments") or [])


def _yahoo_scope(item: dict[str, Any], instrument: str) -> bool:
    proxy = PROXIES.get(instrument, "")
    symbol = str(item.get("symbol") or item.get("proxy_symbol") or "").upper()
    return symbol in {instrument, proxy}


def _evidence(items: list[Any], instrument: str, source: str,
              in_scope: Callable[[dict[str, Any], str], bool]) -> list[dict[str, Any]]:
    evidence = []
    for item in items:
        if not isinstance(item, dict) or not in_scope(item, instrument):
            continue
        stance = LABEL_STANCE.get(str(item.get("sentiment") or "").upper())
        if stance is None:
            continue
        ai = item.get("sentiment_source") == "ai_estimate"
        evidence.append({
            "source": source,
            "title": str(item.get("title", "")),
            "stance": stance,
            "confidence": AI_ESTIMATE_CONFIDENCE if ai else PROVIDER_CONFIDENCE,
            "published": item.get("published_at") or item.get("published"),
        })
    return evidence


def attach_fused_scores(result: dict[str, Any], yahoo_payload: dict[str, Any] | None,
                        webull_payload: dict[str, Any] | None,
                        now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    yahoo_items = (yahoo_payload or {}).get("items") or []
    webull_items = (webull_payload or {}).get("items") or []
    reporting: list[float] = []
    for row in result.get("instruments", []):
        instrument = row.get("symbol")
        evidence = _evidence(webull_items, instrument, "webull", _webull_scope)
        evidence += _evidence(yahoo_items, instrument, "yahoo", _yahoo_scope)
        fused = fuse(evidence, now)
        bias, reason = decide(fused, None)
        row["fused"] = {**fused, "bias": bias, "bias_reason": reason}
        if fused["clusters"]:
            reporting.append(fused["score"])
    result["fused_overall"] = {
        "score": round(sum(reporting) / len(reporting), 1) if reporting else None,
        "instruments_reporting": len(reporting),
        "method": "Mean of per-instrument fused scores. Each story counted once per instrument.",
        "price_confirmation": "not wired: directional bias stays WAIT until price confirms",
    }
    return result

def fused_for_instrument(instrument: str, webull_items, yahoo_items, now: datetime) -> dict[str, Any]:
    """One news read per instrument, using the same evidence rules as the Sentiment desk."""
    evidence = _evidence(list(webull_items or []), instrument, "webull", _webull_scope)
    evidence += _evidence(list(yahoo_items or []), instrument, "yahoo", _yahoo_scope)
    return fuse(evidence, now)