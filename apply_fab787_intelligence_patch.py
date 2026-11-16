from __future__ import annotations

import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path.cwd()
MC = ROOT / "mission_control"
TESTS = ROOT / "tests"

WEBULL_NEWS_SOURCE = r'''"""Read-only Webull news and US macro calendar adapters.

This module is deliberately separate from mission_control.webull: the existing
futures-market tool allowlist and OAuth scopes are not modified. Only the
verified get_stock_news and get_macroeconomic_calendar tools are called.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from mission_control.webull import (
    MCP_URL,
    _oauth_provider,
    _stamp_user_agent,
)

NEWS_TOOL = "get_stock_news"
CALENDAR_TOOL = "get_macroeconomic_calendar"
READ_ONLY_NEWS_TOOLS = frozenset({NEWS_TOOL, CALENDAR_TOOL})
NEWS_SYMBOLS = {"QQQ": "MNQ", "SPY": "MES", "GLD": "MGC"}
NEWS_CACHE_TTL_SECONDS = 600.0
CALENDAR_CACHE_TTL_SECONDS = 900.0
ERROR_COOLDOWN_SECONDS = 900.0
MAX_NEWS_ITEMS_PER_SYMBOL = 30
MAX_CALENDAR_PAGES = 3
MAX_CALENDAR_ITEMS = 90


class WebullNewsUnavailable(RuntimeError):
    """Webull news could not be refreshed."""


class WebullCalendarUnavailable(RuntimeError):
    """Webull's structured macro calendar could not be refreshed."""


def _decode_tool_result(result: Any) -> Any:
    if getattr(result, "is_error", False):
        raise RuntimeError("Webull MCP tool returned an error")
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    decoded: list[Any] = []
    for part in getattr(result, "content", []) or []:
        value = getattr(part, "text", None)
        if value is None:
            decoded.append(str(part))
            continue
        try:
            decoded.append(json.loads(value))
        except (TypeError, json.JSONDecodeError):
            decoded.append(value)
    if len(decoded) == 1:
        return decoded[0]
    return decoded


async def _call_readonly_news_tool(client: Client, name: str, arguments: dict[str, Any]) -> Any:
    """Call only the two Webull read-only news/calendar tools verified for this adapter."""
    if name not in READ_ONLY_NEWS_TOOLS:
        raise ValueError(f"Webull news tool is not enabled: {name}")
    return _decode_tool_result(await client.call_tool(name, arguments))


def _unwrap_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        for child in value:
            if isinstance(child, dict):
                return child
        return {}
    if isinstance(value, dict):
        # MCP wrappers occasionally nest a JSON result under "result" or "data".
        if isinstance(value.get("result"), dict) and not value.get("data"):
            nested = value["result"]
            if "data" in nested or "pagination_key" in nested:
                return nested
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return _unwrap_payload(parsed)
    return {}


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def normalize_webull_news(
    payload: Any,
    *,
    requested_symbol: str,
    received_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Normalize Webull's observed US stock-news response without inventing fields."""
    if requested_symbol not in NEWS_SYMBOLS:
        raise ValueError(f"Unsupported Webull news proxy: {requested_symbol}")
    data = _unwrap_payload(payload)
    rows = data.get("data", [])
    if not isinstance(rows, list):
        return []
    received = (received_at or datetime.now(UTC)).astimezone(UTC).isoformat()
    instrument = NEWS_SYMBOLS[requested_symbol]
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows[:MAX_NEWS_ITEMS_PER_SYMBOL]:
        if not isinstance(row, dict):
            continue
        news_id = str(row.get("news_id") or row.get("id") or "").strip()
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or "").strip()
        if not news_id or not title or not url.startswith("https://"):
            continue
        if news_id in seen:
            continue
        seen.add(news_id)
        raw_sentiment = str(row.get("sentiment") or "").strip().upper()
        sentiment = {
            "POSITIVE": "bullish",
            "NEGATIVE": "bearish",
            "NEUTRAL": "neutral",
        }.get(raw_sentiment, "unavailable")
        published = _parse_iso(row.get("publish_time") or row.get("published_at"))
        related = row.get("related_symbols", [])
        if not isinstance(related, list):
            related = []
        items.append({
            "id": news_id,
            "news_id": news_id,
            "title": title[:500],
            "summary": str(row.get("summary") or "")[:4000],
            "url": url,
            "publisher": str(row.get("source") or "Webull publisher")[:120],
            "source": "Webull Cloud MCP",
            "requested_symbol": requested_symbol,
            "proxy_symbol": requested_symbol,
            "futures_instruments": [instrument],
            "related_symbols": [str(item)[:32] for item in related[:30]],
            "provider_sentiment_raw": raw_sentiment or None,
            "sentiment": sentiment,
            "published_at": published.isoformat() if published else None,
            "received_at": received,
        })
    return items


def normalize_calendar_events(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    """Normalize Webull macro-calendar events; preserve source timestamps and values."""
    data = _unwrap_payload(payload)
    rows = data.get("data", [])
    if not isinstance(rows, list):
        rows = []
    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("event_name") or "").strip()
        publish_date = str(row.get("publish_date") or "").strip()
        raw_time = row.get("publish_time")
        parsed_time = _parse_iso(raw_time)
        if not name or not publish_date:
            continue
        identity = (name.casefold(), parsed_time.isoformat() if parsed_time else publish_date)
        if identity in seen:
            continue
        seen.add(identity)
        events.append({
            "event_name": name[:500],
            "publish_date": publish_date,
            "publish_time": parsed_time.isoformat() if parsed_time else None,
            "timezone": "UTC" if parsed_time else None,
            "period": row.get("period"),
            "unit": row.get("unit"),
            "frequency": row.get("frequency"),
            "forecast_value": row.get("forecast_value"),
            "prior_value": row.get("prior_value"),
            "is_fed_related": bool(row.get("is_fed_related", False)),
            "indicator_type": row.get("indicator_type"),
            "source": "Webull Cloud MCP",
        })
    events.sort(key=lambda item: (item.get("publish_time") or item["publish_date"], item["event_name"]))
    cursor = data.get("pagination_key")
    return events[:MAX_CALENDAR_ITEMS], str(cursor) if cursor else None


async def _open_client():
    """Context helper kept small so both services use the existing Webull OAuth."""
    oauth = _oauth_provider()
    http = httpx2.AsyncClient(
        auth=oauth,
        timeout=httpx2.Timeout(30.0, read=90.0),
        event_hooks={"request": [_stamp_user_agent]},
    )
    return http


async def _fetch_news_from_webull() -> dict[str, Any]:
    received = datetime.now(UTC)
    instruments: dict[str, dict[str, Any]] = {}
    all_items: list[dict[str, Any]] = []
    request_count = 0
    oauth_http = await _open_client()
    try:
        transport = streamable_http_client(MCP_URL, http_client=oauth_http)
        async with Client(transport) as client:
            for proxy_symbol, instrument in NEWS_SYMBOLS.items():
                request_count += 1
                try:
                    payload = await _call_readonly_news_tool(client, NEWS_TOOL, {"symbol": proxy_symbol})
                    data = _unwrap_payload(payload)
                    items = normalize_webull_news(
                        data, requested_symbol=proxy_symbol, received_at=received
                    )
                    instruments[instrument] = {
                        "status": "available",
                        "proxy_symbol": proxy_symbol,
                        "item_count": len(items),
                        "pagination_available": bool(data.get("pagination_key")),
                    }
                    all_items.extend(items)
                except Exception as exc:
                    instruments[instrument] = {
                        "status": "unavailable",
                        "proxy_symbol": proxy_symbol,
                        "item_count": 0,
                        "error": type(exc).__name__,
                    }
    finally:
        await oauth_http.aclose()

    deduped: dict[str, dict[str, Any]] = {}
    for item in all_items:
        key = str(item["news_id"])
        if key not in deduped:
            deduped[key] = item
        else:
            existing = deduped[key]
            existing["futures_instruments"] = sorted(set(existing["futures_instruments"] + item["futures_instruments"]))
            existing["requested_symbols"] = sorted(set(existing.get("requested_symbols", [existing["requested_symbol"]]) + [item["requested_symbol"]]))
    items = list(deduped.values())
    items.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    available = sum(value["status"] == "available" for value in instruments.values())
    if available == 0:
        raise WebullNewsUnavailable("Webull did not return news for any configured proxy symbol")
    return {
        "source": "Webull Cloud MCP",
        "status": "fresh" if available == len(NEWS_SYMBOLS) else "partial",
        "fetched_at": datetime.now(UTC).isoformat(),
        "symbols": dict(NEWS_SYMBOLS),
        "instruments": instruments,
        "items": items,
        "item_count": len(items),
        "provider_tool_requests": request_count,
        "pagination_note": "First page only for each proxy symbol; pagination is available from Webull but not automatically expanded for headline refreshes.",
    }


async def _fetch_calendar_from_webull(
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    request_count = 0
    cursor: str | None = None
    seen_cursors: set[str] = set()
    events_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    oauth_http = await _open_client()
    try:
        transport = streamable_http_client(MCP_URL, http_client=oauth_http)
        async with Client(transport) as client:
            for _ in range(MAX_CALENDAR_PAGES):
                arguments: dict[str, Any] = {"start_date": start_date, "end_date": end_date}
                if cursor:
                    arguments["pagination_key"] = cursor
                request_count += 1
                payload = await _call_readonly_news_tool(client, CALENDAR_TOOL, arguments)
                events, next_cursor = normalize_calendar_events(payload)
                for event in events:
                    key = (event["event_name"].casefold(), event.get("publish_time") or event["publish_date"])
                    events_by_key[key] = event
                if not next_cursor or next_cursor in seen_cursors:
                    cursor = None
                    break
                seen_cursors.add(next_cursor)
                cursor = next_cursor
    finally:
        await oauth_http.aclose()
    events = sorted(
        events_by_key.values(),
        key=lambda item: (item.get("publish_time") or item["publish_date"], item["event_name"]),
    )[:MAX_CALENDAR_ITEMS]
    return {
        "source": "Webull US Macroeconomic Calendar",
        "status": "fresh",
        "start_date": start_date,
        "end_date": end_date,
        "fetched_at": datetime.now(UTC).isoformat(),
        "timezone": "UTC",
        "display_timezone": "America/New_York",
        "events": events,
        "event_count": len(events),
        "provider_tool_requests": request_count,
        "pagination_complete": cursor is None,
        "pagination_remaining": cursor is not None,
        "pagination_note": "At most three pages are fetched per refresh; additional pages are not silently treated as complete.",
    }


class _CachedService:
    def __init__(self, *, clock: Callable[[], float], ttl_seconds: float, error_cooldown_seconds: float) -> None:
        self._clock = clock
        self.ttl_seconds = ttl_seconds
        self.error_cooldown_seconds = error_cooldown_seconds
        self._cache: tuple[float, dict[str, Any]] | None = None
        self._lock = asyncio.Lock()
        self._cooldown_until = 0.0
        self.fetch_count = 0

    def _response(self, status: str, now: float) -> dict[str, Any]:
        assert self._cache is not None
        stored, payload = self._cache
        return {
            **payload,
            "status": status,
            "upstream_refreshes": self.fetch_count,
            "cache_age_seconds": round(max(0.0, now - stored), 1),
            "next_refresh_seconds": round(max(0.0, stored + self.ttl_seconds - now), 1),
            "provider_requests_triggered": int(payload.get("provider_tool_requests", 0)) if status in {"fresh", "partial"} else 0,
        }

    def peek(self, *, empty: dict[str, Any]) -> dict[str, Any]:
        now = self._clock()
        if self._cache is None:
            return {**empty, "upstream_refreshes": self.fetch_count, "provider_requests_triggered": 0}
        stored = self._cache[0]
        status = "cached" if now - stored < self.ttl_seconds else "stale"
        response = self._response(status, now)
        if status == "stale" and now < self._cooldown_until:
            response["retry_after_seconds"] = round(self._cooldown_until - now, 1)
        return response

    async def _get(self, fetcher: Callable[[], Awaitable[dict[str, Any]]], *, unavailable: dict[str, Any]) -> dict[str, Any]:
        now = self._clock()
        if self._cache and now - self._cache[0] < self.ttl_seconds:
            return self._response("cached", now)
        if now < self._cooldown_until:
            if self._cache:
                return self._response("stale", now)
            return {**unavailable, "status": "cooldown", "upstream_refreshes": self.fetch_count, "retry_after_seconds": round(self._cooldown_until - now, 1), "provider_requests_triggered": 0}
        async with self._lock:
            now = self._clock()
            if self._cache and now - self._cache[0] < self.ttl_seconds:
                return self._response("cached", now)
            if now < self._cooldown_until:
                if self._cache:
                    return self._response("stale", now)
                return {**unavailable, "status": "cooldown", "upstream_refreshes": self.fetch_count, "retry_after_seconds": round(self._cooldown_until - now, 1), "provider_requests_triggered": 0}
            self.fetch_count += 1
            try:
                payload = await fetcher()
            except Exception as exc:
                self._cooldown_until = self._clock() + self.error_cooldown_seconds
                if self._cache:
                    response = self._response("stale", self._clock())
                    response["last_error"] = type(exc).__name__
                    return response
                return {**unavailable, "status": "unavailable", "upstream_refreshes": self.fetch_count, "error": type(exc).__name__, "retry_after_seconds": self.error_cooldown_seconds, "provider_requests_triggered": 0}
            self._cache = (self._clock(), payload)
            return self._response(str(payload.get("status") or "fresh"), self._clock())


class WebullNewsService(_CachedService):
    def __init__(self, *, fetcher: Callable[[], Awaitable[dict[str, Any]]] = _fetch_news_from_webull, clock: Callable[[], float] = time.monotonic, ttl_seconds: float = NEWS_CACHE_TTL_SECONDS, error_cooldown_seconds: float = ERROR_COOLDOWN_SECONDS) -> None:
        super().__init__(clock=clock, ttl_seconds=ttl_seconds, error_cooldown_seconds=error_cooldown_seconds)
        self._fetcher = fetcher

    def peek_news(self) -> dict[str, Any]:
        return self.peek(empty={"status": "not_loaded", "source": "Webull Cloud MCP", "items": [], "item_count": 0, "instruments": {}})

    async def get_news(self) -> dict[str, Any]:
        return await self._get(self._fetcher, unavailable={"source": "Webull Cloud MCP", "items": [], "item_count": 0, "instruments": {}})


class WebullCalendarService(_CachedService):
    def __init__(self, *, fetcher: Callable[[str, str], Awaitable[dict[str, Any]]] = _fetch_calendar_from_webull, clock: Callable[[], float] = time.monotonic, ttl_seconds: float = CALENDAR_CACHE_TTL_SECONDS, error_cooldown_seconds: float = ERROR_COOLDOWN_SECONDS) -> None:
        super().__init__(clock=clock, ttl_seconds=ttl_seconds, error_cooldown_seconds=error_cooldown_seconds)
        self._fetcher = fetcher

    def peek_calendar(self) -> dict[str, Any]:
        return self.peek(empty={"status": "not_loaded", "source": "Webull US Macroeconomic Calendar", "events": [], "event_count": 0, "timezone": "UTC", "display_timezone": "America/New_York"})

    async def get_calendar(self, *, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
        start = date.fromisoformat(start_date) if start_date else datetime.now(UTC).date()
        end = date.fromisoformat(end_date) if end_date else start + timedelta(days=7)
        if end < start or (end - start).days > 31:
            raise ValueError("Calendar date range must be 0-31 days.")
        async def fetch() -> dict[str, Any]:
            return await self._fetcher(start.isoformat(), end.isoformat())
        return await self._get(fetch, unavailable={"source": "Webull US Macroeconomic Calendar", "events": [], "event_count": 0, "timezone": "UTC", "display_timezone": "America/New_York"})


webull_news_service = WebullNewsService()
webull_calendar_service = WebullCalendarService()
'''

NEWS_INTELLIGENCE_SOURCE = r'''"""Transparent per-instrument news sentiment derived only from source labels."""
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
'''

TEST_SOURCE = r'''from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from mission_control.webull_news import (
    READ_ONLY_NEWS_TOOLS,
    WebullCalendarService,
    WebullNewsService,
    normalize_calendar_events,
    normalize_webull_news,
)
from mission_control.news_intelligence import build_instrument_news_intelligence


def test_news_adapter_allowlist_contains_only_verified_read_only_tools():
    assert READ_ONLY_NEWS_TOOLS == {"get_stock_news", "get_macroeconomic_calendar"}


def test_normalize_webull_news_preserves_native_sentiment_and_proxy_mapping():
    now = datetime.now(UTC)
    payload = {"symbol": "QQQ", "data": [{
        "news_id": "abc", "title": "Nasdaq tech stocks move", "summary": "Market summary",
        "source": "Benzinga", "url": "https://example.com/story",
        "publish_time": now.isoformat(), "sentiment": "NEGATIVE", "related_symbols": ["QQQ"],
    }]}
    items = normalize_webull_news(payload, requested_symbol="QQQ", received_at=now)
    assert len(items) == 1
    assert items[0]["sentiment"] == "bearish"
    assert items[0]["provider_sentiment_raw"] == "NEGATIVE"
    assert items[0]["futures_instruments"] == ["MNQ"]
    assert items[0]["publisher"] == "Benzinga"


def test_normalize_webull_news_rejects_unsafe_or_incomplete_items():
    payload = {"data": [
        {"news_id": "1", "title": "No URL", "url": "", "sentiment": "POSITIVE"},
        {"news_id": "2", "title": "Unsafe", "url": "javascript:alert(1)", "sentiment": "POSITIVE"},
    ]}
    assert normalize_webull_news(payload, requested_symbol="SPY") == []


def test_calendar_normalizer_keeps_utc_time_and_pagination_cursor():
    events, cursor = normalize_calendar_events({"data": [{
        "event_name": "CPI", "publish_date": "2026-11-20",
        "publish_time": "2026-11-20T13:30:00.000+0000", "forecast_value": "3.1",
        "prior_value": "3.0", "is_fed_related": False,
    }], "pagination_key": "next-page"})
    assert len(events) == 1
    assert events[0]["publish_time"] == "2026-11-20T13:30:00+00:00"
    assert events[0]["timezone"] == "UTC"
    assert events[0]["forecast_value"] == "3.1"
    assert cursor == "next-page"


def test_webull_news_service_cache_and_peek_do_not_refetch():
    calls = []
    async def fetcher():
        calls.append(1)
        return {"source": "Webull Cloud MCP", "items": [], "item_count": 0, "status": "fresh", "provider_tool_requests": 3}
    service = WebullNewsService(fetcher=fetcher, clock=lambda: 100.0)
    assert service.peek_news()["status"] == "not_loaded"
    async def run():
        first = await service.get_news()
        second = await service.get_news()
        assert first["status"] == "fresh"
        assert first["provider_requests_triggered"] == 3
        assert second["status"] == "cached"
        assert second["provider_requests_triggered"] == 0
        assert service.peek_news()["provider_requests_triggered"] == 0
    asyncio.run(run())
    assert len(calls) == 1


def test_webull_calendar_service_rejects_bad_range_and_caches():
    calls = []
    async def fetcher(start, end):
        calls.append((start, end))
        return {"source": "Webull US Macroeconomic Calendar", "events": [], "event_count": 0, "status": "fresh"}
    service = WebullCalendarService(fetcher=fetcher, clock=lambda: 100.0)
    async def run():
        first = await service.get_calendar(start_date="2026-11-15", end_date="2026-11-20")
        second = await service.get_calendar(start_date="2026-11-15", end_date="2026-11-20")
        assert first["status"] == "fresh"
        assert second["status"] == "cached"
    asyncio.run(run())
    assert calls == [("2026-11-15", "2026-11-20")]


def test_news_intelligence_uses_webull_when_yahoo_has_no_native_sentiment():
    now = datetime.now(UTC)
    webull = {"status": "fresh", "items": [{
        "futures_instruments": ["MNQ"], "sentiment": "bearish",
        "published_at": now.isoformat(), "title": "Nasdaq falls",
    }]}
    result = build_instrument_news_intelligence(
        {"status": "cached", "items": [{"title": "Nasdaq futures update"}]}, webull
    )
    mnq = next(item for item in result["instruments"] if item["symbol"] == "MNQ")
    assert mnq["yahoo_bias"]["status"] == "not_provided"
    assert mnq["webull_bias"]["stance"] == "bearish"
    assert mnq["combined_bias"]["stance"] == "bearish"
    assert mnq["combined_bias"]["status"] == "single_source"
    assert mnq["combined_bias"]["confidence"] == "low"


def test_news_intelligence_does_not_use_stale_webull_sentiment():
    old = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    result = build_instrument_news_intelligence(None, {"items": [{
        "futures_instruments": ["MGC"], "sentiment": "bullish", "published_at": old,
    }]})
    mgc = next(item for item in result["instruments"] if item["symbol"] == "MGC")
    assert mgc["combined_bias"]["stance"] == "unavailable"
    assert mgc["webull_bias"]["article_count"] == 0


def test_news_intelligence_preserves_mixed_native_webull_sentiment():
    now = datetime.now(UTC).isoformat()
    items = [
        {"futures_instruments": ["MNQ"], "sentiment": "bullish", "published_at": now},
        {"futures_instruments": ["MNQ"], "sentiment": "bearish", "published_at": now},
    ]
    result = build_instrument_news_intelligence(None, {"items": items})
    mnq = next(item for item in result["instruments"] if item["symbol"] == "MNQ")
    assert mnq["webull_bias"]["stance"] == "mixed"
    assert mnq["combined_bias"]["stance"] == "mixed"
    assert mnq["combined_bias"]["status"] == "single_source"


def test_news_intelligence_includes_catalyst_tags_from_both_sources():
    now = datetime.now(UTC).isoformat()
    result = build_instrument_news_intelligence(
        {"status": "fresh", "items": [{"title": "CPI inflation report due", "publisher": "Yahoo", "url": "https://example.com/y"}]},
        {"status": "fresh", "items": [{"title": "Federal Reserve signals policy change", "publisher": "Benzinga", "url": "https://example.com/w", "published_at": now, "futures_instruments": ["MNQ"], "sentiment": "neutral"}]},
    )
    catalysts = result["catalyst_watch"]
    assert catalysts["summary"]["high_priority"] >= 1
    assert len(catalysts["headlines"]) == 2
    assert catalysts["provider_requests_triggered"] == 0
'''

API_TEST_SOURCE = r'''from fastapi.testclient import TestClient
from mission_control.main import app

client = TestClient(app)


def test_webull_news_endpoint_uses_service(monkeypatch):
    from mission_control import main
    async def fake():
        return {"status": "fresh", "source": "Webull Cloud MCP", "items": [], "item_count": 0}
    monkeypatch.setattr(main.webull_news_service, "get_news", fake)
    response = client.get("/api/news/webull")
    assert response.status_code == 200
    assert response.json()["source"] == "Webull Cloud MCP"


def test_news_intelligence_endpoint_is_cache_only(monkeypatch):
    from mission_control import main
    calls = {"yahoo": 0, "webull": 0}
    def yahoo():
        calls["yahoo"] += 1
        return {"status": "not_loaded", "items": []}
    def webull():
        calls["webull"] += 1
        return {"status": "not_loaded", "items": []}
    monkeypatch.setattr(main.yahoo_news_service, "peek_news", yahoo)
    monkeypatch.setattr(main.webull_news_service, "peek_news", webull)
    response = client.get("/api/news/intelligence")
    assert response.status_code == 200
    assert response.json()["provider_requests_triggered"] == 0
    assert calls == {"yahoo": 1, "webull": 1}


def test_webull_calendar_endpoint_uses_calendar_service(monkeypatch):
    from mission_control import main
    async def fake(**kwargs):
        return {"status": "fresh", "source": "Webull US Macroeconomic Calendar", "events": [], "event_count": 0}
    monkeypatch.setattr(main.webull_calendar_service, "get_calendar", fake)
    response = client.get("/api/calendar/events")
    assert response.status_code == 200
    assert response.json()["source"] == "Webull US Macroeconomic Calendar"


def test_gex_snapshot_endpoint_does_not_fetch_chain(monkeypatch):
    from mission_control import main
    monkeypatch.setattr(main.market_data_manager, "peek_snapshot", lambda _interval: None)
    response = client.get("/api/gex/snapshot")
    assert response.status_code == 200
    assert response.json()["status"] == "not_loaded"
'''

CSS_ADDITIONS = r'''
/* FAB787 intelligence workspace additions */
.instrument .bias-line{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin:8px 0 2px;color:var(--muted);font-size:11px}
.bias-chip,.source-chip{display:inline-flex;align-items:center;gap:5px;border:1px solid var(--line);border-radius:999px;padding:3px 8px;font-size:10px;font-weight:750;letter-spacing:.04em}
.bias-bullish{color:var(--green);border-color:#28684f;background:#10271f}.bias-bearish{color:var(--red);border-color:#713b48;background:#2a1720}.bias-neutral{color:var(--amber);border-color:#715b36;background:#2a2114}.bias-mixed{color:var(--blue);border-color:#435b9a;background:#18213c}.bias-unavailable{color:var(--muted);background:#101a29}
.home-intelligence{margin-top:12px}.home-intelligence .panel-title{color:var(--cyan)}.snapshot-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:12px}.snapshot-metric{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:10px;min-width:0}.snapshot-metric small{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.08em}.snapshot-metric b{display:block;margin-top:5px;font-size:18px;font-variant-numeric:tabular-nums}.news-columns{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.news-column{min-width:0}.news-column .panel-title{color:var(--cyan)}.news-feed{max-height:610px;overflow:auto}.news-item{padding:12px 0;border-bottom:1px solid var(--line)}.news-item:last-child{border-bottom:0}.news-item .tag{margin-bottom:6px}.news-item a{display:block;line-height:1.45}.news-item p{margin:6px 0;color:#bac9dc;font-size:12px}.news-bias-card{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:13px;min-width:0}.news-bias-card h4{margin:0 0 9px;color:var(--text);font-size:13px}.bias-row{display:flex;justify-content:space-between;align-items:center;gap:8px;margin:7px 0;font-size:11px}.bias-note{color:var(--muted);font-size:10px;line-height:1.45;margin-top:8px}.priority-high{color:var(--amber);border-color:#715b36;background:#2a2114}.priority-medium{color:var(--cyan);border-color:#28566a;background:#102433}.priority-unclassified{color:var(--muted)}.calendar-event{padding:10px 0;border-bottom:1px solid var(--line)}.calendar-event:last-child{border-bottom:0}.calendar-event strong{display:block;font-size:12px}.calendar-event .event-meta{color:var(--muted);font-size:10px;margin-top:4px}.fed-flag{color:var(--amber);font-weight:800}.gex-visual{background:#091321;border:1px solid var(--line);border-radius:10px;padding:10px;margin:12px 0}.gex-legend{display:flex;gap:12px;flex-wrap:wrap;color:var(--muted);font-size:10px;margin:6px 2px}.gex-legend span{display:inline-flex;align-items:center;gap:5px}.legend-dot{width:8px;height:8px;border-radius:50%;display:inline-block}.gex-table-wrap{overflow-x:auto}.gex-table-positive{color:var(--green)}.gex-table-negative{color:var(--red)}.context-meter{height:7px;background:#1b2a3e;border-radius:99px;overflow:hidden;margin:7px 0}.context-meter span{display:block;height:100%;background:linear-gradient(90deg,var(--blue),var(--cyan));border-radius:99px}.snapshot-note{font-size:10px;color:var(--muted);line-height:1.45;margin-top:8px}.news-refresh-row{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:10px}
@media(max-width:1100px){.news-columns{grid-template-columns:1fr 1fr}.news-columns>.news-column:last-child{grid-column:1/-1}}
@media(max-width:760px){.news-columns{grid-template-columns:1fr}.news-columns>.news-column:last-child{grid-column:auto}.snapshot-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
'''

HOME_MARKUP = r'''
<div class="grid2 home-intelligence">
 <div class="panel">
  <div class="panel-head"><div><div class="eyebrow">Source-aware news intelligence</div><div class="panel-title">Combined instrument sentiment</div></div><span class="pill off" id="home-sentiment-status">AWAITING SOURCES</span></div>
  <div class="grid3" id="home-sentiment-grid">
   <div class="news-bias-card"><h4>MNQ · QQQ</h4><span class="bias-chip bias-unavailable">Awaiting news</span></div>
   <div class="news-bias-card"><h4>MES · SPY</h4><span class="bias-chip bias-unavailable">Awaiting news</span></div>
   <div class="news-bias-card"><h4>MGC · GLD</h4><span class="bias-chip bias-unavailable">Awaiting news</span></div>
  </div>
  <p class="snapshot-note">Webull native article sentiment is used when Yahoo does not provide a sentiment field. ETF/index proxies are not futures-native news. Confidence is capped at medium without independent confirmation.</p>
 </div>
 <div class="panel">
  <div class="panel-head"><div><div class="eyebrow">Cached options positioning</div><div class="panel-title">GEX snapshot · NDX / MNQ proxy</div></div><span class="pill off" id="home-gex-status">NOT LOADED</span></div>
  <div class="snapshot-grid">
   <div class="snapshot-metric"><small>NDX underlying spot</small><b id="home-gex-spot">—</b></div>
   <div class="snapshot-metric"><small>MNQ mapped price</small><b id="home-gex-futures">—</b></div>
   <div class="snapshot-metric"><small>Call wall · MNQ level</small><b id="home-gex-call">—</b></div>
   <div class="snapshot-metric"><small>Put wall · MNQ level</small><b id="home-gex-put">—</b></div>
   <div class="snapshot-metric"><small>Gamma flip</small><b id="home-gex-flip">—</b></div>
   <div class="snapshot-metric"><small>Net GEX / 1% move</small><b id="home-gex-net">—</b></div>
  </div>
  <p class="snapshot-note" id="home-gex-note">Cache-only snapshot. Open GEX workspace and load data to populate it. No additional Cboe request is made from Home.</p>
 </div>
</div>
<div class="panel" style="margin-top:12px">
 <div class="panel-head"><div><div class="eyebrow">Deterministic cache-only market context</div><div class="panel-title">Futures bot assessment · Opportunity Scout</div></div><span class="pill off" id="home-scout-status">AWAITING CACHE</span></div>
 <div id="home-scout-content" class="muted">Waiting for verified market context. This is not an actionable trade recommendation.</div>
 <p class="snapshot-note">Context points are not a probability, forecast, trade entry, or approval. Any future proposal must pass the Master Risk Engine and your explicit approval.</p>
</div>
'''

GEX_CHART_MARKUP = r'''
<div class="gex-visual">
 <div class="panel-head"><div><div class="eyebrow">Strike distribution · mapped futures levels</div><div class="panel-title">GEX exposure map</div></div><span class="muted small" id="gex-chart-caption">Load data to plot strike exposure</span></div>
 <svg id="gex-chart" viewBox="0 0 1000 360" role="img" aria-label="Net gamma exposure by mapped futures strike with spot, call wall, put wall and gamma flip markers" style="width:100%;height:auto;display:block;min-height:210px"></svg>
 <div class="gex-legend">
  <span><i class="legend-dot" style="background:var(--green)"></i>Positive net GEX</span>
  <span><i class="legend-dot" style="background:var(--red)"></i>Negative net GEX</span>
  <span><i class="legend-dot" style="background:var(--cyan)"></i>Mapped futures spot</span>
  <span><i class="legend-dot" style="background:var(--green)"></i>Call wall</span>
  <span><i class="legend-dot" style="background:var(--red)"></i>Put wall</span>
  <span><i class="legend-dot" style="background:var(--amber)"></i>Gamma flip</span>
 </div>
</div>
'''

NEWS_MARKUP = r'''
<section class="section" id="news">
 <div class="news-refresh-row"><div><div class="eyebrow">Multi-source market intelligence</div><div class="panel-title">News, sentiment & macro catalysts</div><div class="muted small">Yahoo and Webull remain separate source feeds. All displayed publication times are converted from source timestamps to New York time.</div></div><button class="btn" id="refresh-news">Refresh news & calendar</button></div>
 <div class="news-columns">
  <div class="panel news-column">
   <div class="panel-head"><div><div class="eyebrow">Source 1 · RSS</div><div class="panel-title">Yahoo Finance</div></div><span class="pill off" id="news-status">CHECKING</span></div>
   <div class="muted small" id="news-meta">Existing combined Yahoo RSS feed · 10-minute cache.</div>
   <div class="news-feed" id="news-feed"><div class="placeholder">Loading Yahoo headlines…</div></div>
  </div>
  <div class="panel news-column">
   <div class="panel-head"><div><div class="eyebrow">Source 2 · Read-only MCP</div><div class="panel-title">Webull News</div></div><span class="pill off" id="webull-news-status">CHECKING</span></div>
   <div class="muted small" id="webull-news-meta">QQQ → MNQ · SPY → MES · GLD → MGC · independent 10-minute cache.</div>
   <div class="news-feed" id="webull-news-feed"><div class="placeholder">Loading Webull headlines…</div></div>
  </div>
  <div class="panel news-column">
   <div class="panel-head"><div><div class="eyebrow">Source 3 · Structured macro data</div><div class="panel-title">Economic Calendar</div></div><span class="pill off" id="calendar-status">CHECKING</span></div>
   <div class="muted small" id="calendar-meta">Webull US macro calendar · UTC source times displayed in America/New_York.</div>
   <div class="news-feed" id="calendar-feed"><div class="placeholder">Loading macro events…</div></div>
   <a class="tv-link" id="yahoo-calendar-link" href="https://finance.yahoo.com/calendar/economic/" target="_blank" rel="noopener noreferrer">Yahoo Finance calendar fallback ↗</a>
  </div>
 </div>
 <div class="panel" style="margin-top:12px">
  <div class="panel-head"><div><div class="eyebrow">Catalyst intelligence · both sources</div><div class="panel-title">Catalyst watch</div></div><span class="pill off" id="catalyst-status">AWAITING HEADLINES</span></div>
  <div id="catalyst-feed" class="news-feed" style="max-height:280px"><div class="placeholder">Catalyst tags will appear when headlines are cached.</div></div>
  <p class="snapshot-note">Catalyst tags identify themes and priority for review. They do not infer bullish/bearish market impact.</p>
 </div>
 <div class="panel" style="margin-top:12px">
  <div class="panel-head"><div><div class="eyebrow">Per-provider comparison</div><div class="panel-title">Yahoo bias · Webull bias · Combined instrument bias</div></div><span class="muted small">MNQ · MES · MGC</span></div>
  <div class="grid3" id="news-bias-grid">
   <div class="news-bias-card"><h4>MNQ · QQQ</h4><div class="bias-row"><span>Yahoo</span><span>Not supplied</span></div><div class="bias-row"><span>Webull</span><span>Awaiting feed</span></div><div class="bias-row"><strong>Combined</strong><span>Unavailable</span></div></div>
   <div class="news-bias-card"><h4>MES · SPY</h4><div class="bias-row"><span>Yahoo</span><span>Not supplied</span></div><div class="bias-row"><span>Webull</span><span>Awaiting feed</span></div><div class="bias-row"><strong>Combined</strong><span>Unavailable</span></div></div>
   <div class="news-bias-card"><h4>MGC · GLD</h4><div class="bias-row"><span>Yahoo</span><span>Not supplied</span></div><div class="bias-row"><span>Webull</span><span>Awaiting feed</span></div><div class="bias-row"><strong>Combined</strong><span>Unavailable</span></div></div>
  </div>
  <p class="snapshot-note">Yahoo RSS currently provides no native sentiment or reliable per-contract mapping. Webull sentiment is its own article label. A one-provider result is marked single-source; stale items older than 24 hours do not contribute to directional bias.</p>
 </div>
</section>
'''

JS_REPLACEMENT = r'''function formatNewsTime(value){
  if(!value)return "Time unavailable";
  const date=new Date(value);
  if(Number.isNaN(date.getTime()))return "Time unavailable";
  return new Intl.DateTimeFormat("en-US",{timeZone:"America/New_York",dateStyle:"medium",timeStyle:"short",timeZoneName:"short"}).format(date);
}
function biasClass(value){return ({bullish:"bias-bullish",bearish:"bias-bearish",neutral:"bias-neutral",mixed:"bias-mixed"})[value]||"bias-unavailable";}
function biasLabel(value){return ({bullish:"BULLISH",bearish:"BEARISH",neutral:"NEUTRAL",mixed:"MIXED",unavailable:"UNAVAILABLE",not_provided:"NOT PROVIDED"})[value]||String(value||"UNAVAILABLE").replaceAll("_"," ").toUpperCase();}
function setPill(id,status,label){const el=document.getElementById(id);if(!el)return;el.textContent=label||String(status||"unavailable").toUpperCase();el.className=["fresh","cached","partial","ready"].includes(status)?"pill ok":status==="stale"?"pill warn":"pill off";}
function renderArticleFeed(containerId,items,emptyText){
  const feed=document.getElementById(containerId);if(!feed)return;feed.replaceChildren();
  if(!items||!items.length){const empty=document.createElement("div");empty.className="placeholder";empty.textContent=emptyText;feed.appendChild(empty);return;}
  items.forEach(item=>{
    const article=document.createElement("article");article.className="news-item";
    const tag=document.createElement("div");tag.className="tag";tag.textContent=(item.publisher||item.source||"Provider")+" · "+formatNewsTime(item.published_at);
    const title=document.createElement("a");title.textContent=item.title||"Untitled headline";title.style.color="var(--text)";title.style.fontWeight="700";title.style.textDecoration="none";
    if(typeof item.url==="string"&&item.url.startsWith("https://")){title.href=item.url;title.target="_blank";title.rel="noopener noreferrer";}else title.removeAttribute("href");
    const summary=document.createElement("p");summary.textContent=item.summary||"No summary supplied by the publisher.";
    const meta=document.createElement("div");meta.className="muted small";
    const labels=[];if(item.proxy_symbol)labels.push("Proxy: "+item.proxy_symbol);if(item.sentiment&&item.sentiment!=="unavailable")labels.push("Webull label: "+biasLabel(item.sentiment));if(item.related_symbols?.length)labels.push("Related: "+item.related_symbols.slice(0,5).join(", "));
    meta.textContent=labels.join(" · ")||"Provider sentiment not supplied";
    article.append(tag,title,summary,meta);feed.appendChild(article);
  });
}
function renderYahooNews(data){
  setPill("news-status",data.status,data.status==="fresh"?"YAHOO FRESH":String(data.status||"YAHOO").toUpperCase());
  document.getElementById("news-meta").textContent=(data.item_count??(data.items||[]).length)+" headlines · cache age "+(data.cache_age_seconds??"—")+"s · refreshes "+(data.upstream_requests??data.upstream_refreshes??0)+" · Yahoo RSS";
  renderArticleFeed("news-feed",data.items||[],data.error||"No Yahoo Finance headlines returned.");
}
function renderWebullNews(data){
  setPill("webull-news-status",data.status,data.status==="fresh"?"WEBULL FRESH":String(data.status||"WEBULL").toUpperCase());
  const instruments=data.instruments||{};const byInstrument=["MNQ","MES","MGC"].map(s=>s+": "+(instruments[s]?.item_count??0)).join(" · ");
  document.getElementById("webull-news-meta").textContent=(data.item_count??(data.items||[]).length)+" headlines · "+byInstrument+" · cache age "+(data.cache_age_seconds??"—")+"s";
  renderArticleFeed("webull-news-feed",data.items||[],data.error||"No Webull headlines available for the configured proxy symbols.");
}
function renderCalendar(data){
  setPill("calendar-status",data.status,data.status==="fresh"?"WEBULL CALENDAR":String(data.status||"CALENDAR").toUpperCase());
  document.getElementById("calendar-meta").textContent=(data.event_count??(data.events||[]).length)+" events · "+(data.start_date||"")+" to "+(data.end_date||"")+" · source UTC / display New York · cache age "+(data.cache_age_seconds??"—")+"s";
  const feed=document.getElementById("calendar-feed");feed.replaceChildren();const events=data.events||[];
  if(!events.length){const empty=document.createElement("div");empty.className="placeholder";empty.textContent=data.error||"No structured macro events returned.";feed.appendChild(empty);return;}
  events.slice(0,35).forEach(event=>{const row=document.createElement("div");row.className="calendar-event";const title=document.createElement("strong");title.textContent=event.event_name;const when=document.createElement("div");when.className="event-meta";when.textContent=(event.publish_time?formatNewsTime(event.publish_time):event.publish_date+" · time unavailable")+(event.is_fed_related?" · FED-RELATED":"")+(event.indicator_type?" · "+event.indicator_type.replaceAll(/([A-Z])/g," $1"):"");const values=document.createElement("div");values.className="event-meta";const bits=[];if(event.forecast_value!=null)bits.push("Forecast "+event.forecast_value);if(event.prior_value!=null)bits.push("Prior "+event.prior_value);if(event.period)bits.push(event.period);values.textContent=bits.join(" · ");if(event.is_fed_related)title.className="fed-flag";row.append(title,when,values);feed.appendChild(row);});
  if(data.pagination_remaining){const note=document.createElement("div");note.className="snapshot-note";note.textContent="More events may be available; page limit reached for this refresh.";feed.appendChild(note);}
}
function renderCatalystWatch(data){
  setPill("catalyst-status",data.status,data.status==="ready"?((data.summary?.high_priority||0)+" HIGH PRIORITY") :String(data.status||"blocked").toUpperCase());
  const host=document.getElementById("catalyst-feed");if(!host)return;host.replaceChildren();const items=data.headlines||[];
  if(!items.length){const empty=document.createElement("div");empty.className="placeholder";empty.textContent="No catalyst headlines are currently classified.";host.appendChild(empty);return;}
  items.slice(0,24).forEach(item=>{const row=document.createElement("article");row.className="news-item";const meta=document.createElement("div");meta.className="tag";meta.textContent=(item.publisher||"Provider")+" · "+formatNewsTime(item.published_at);const title=document.createElement("div");title.style.fontWeight="700";title.textContent=item.title;const tag=document.createElement("span");tag.className="bias-chip "+(item.priority==="high"?"priority-high":item.priority==="medium"?"priority-medium":"priority-unclassified");tag.textContent=String(item.priority||"unclassified").toUpperCase();const themes=document.createElement("div");themes.className="muted small";themes.textContent=(item.themes||[]).map(x=>x.replaceAll("_"," ")).join(" · ")||"No theme keyword matched";row.append(meta,title,tag,themes);host.appendChild(row);});
}
function renderNewsBias(data){
  renderCatalystWatch(data.catalyst_watch||{});
  const grid=document.getElementById("news-bias-grid");if(!grid)return;grid.replaceChildren();
  (data.instruments||[]).forEach(item=>{
    const card=document.createElement("div");card.className="news-bias-card";const heading=document.createElement("h4");heading.textContent=item.symbol+" · "+item.proxy_symbol;card.appendChild(heading);
    [["Yahoo",item.yahoo_bias], ["Webull",item.webull_bias], ["Combined",item.combined_bias]].forEach(([label,bias])=>{
      const line=document.createElement("div");line.className="bias-row";const name=document.createElement("span");name.textContent=label;const value=document.createElement("span");value.textContent=biasLabel(bias?.stance)+(bias?.confidence&&bias.confidence!=="unavailable"?" · "+bias.confidence:" ");if(label==="Combined")value.className="bias-chip "+biasClass(bias?.stance);line.append(name,value);card.appendChild(line);
    });
    const note=document.createElement("p");note.className="bias-note";note.textContent=item.combined_bias?.disclosure||"No source disclosure available.";card.appendChild(note);grid.appendChild(card);
  });
  setPill("home-sentiment-status",data.status,data.status==="ready"?"NEWS BIAS READY":"AWAITING SOURCES");
  const home=document.getElementById("home-sentiment-grid");if(home){home.replaceChildren();(data.instruments||[]).forEach(item=>{const card=document.createElement("div");card.className="news-bias-card";const h=document.createElement("h4");h.textContent=item.symbol+" · "+item.proxy_symbol;const chip=document.createElement("span");chip.className="bias-chip "+biasClass(item.combined_bias?.stance);chip.textContent=biasLabel(item.combined_bias?.stance)+" · "+(item.combined_bias?.confidence||"no confidence");const note=document.createElement("p");note.className="bias-note";note.textContent=item.combined_bias?.status==="single_source"?"Single source: "+(item.combined_bias.sources||[]).join(", "):item.combined_bias?.status.replaceAll("_"," ")||"No fresh sentiment";card.append(h,chip,note);home.appendChild(card);const tile=document.querySelector('.instrument[data-symbol="'+item.symbol+'"]');if(tile){let biasLine=tile.querySelector(".bias-line");if(!biasLine){biasLine=document.createElement("div");biasLine.className="bias-line";const foot=tile.querySelector(".tile-foot");tile.insertBefore(biasLine,foot||null);}biasLine.replaceChildren();const small=document.createElement("span");small.textContent="News bias";const b=document.createElement("span");b.className="bias-chip "+biasClass(item.combined_bias?.stance);b.textContent=biasLabel(item.combined_bias?.stance);biasLine.append(small,b);}});}
}
async function loadHomeIntelligence(){
  const tasks=await Promise.allSettled([
    fetch("/api/news/intelligence").then(r=>{if(!r.ok)throw new Error("News intelligence unavailable");return r.json()}),
    fetch("/api/gex/snapshot").then(r=>{if(!r.ok)throw new Error("GEX snapshot unavailable");return r.json()}),
    fetch("/api/agents/opportunity-scout").then(r=>{if(!r.ok)throw new Error("Opportunity Scout unavailable");return r.json()})
  ]);
  if(tasks[0].status==="fulfilled")renderNewsBias(tasks[0].value);
  if(tasks[1].status==="fulfilled"){
    const d=tasks[1].value;if(d.status&&d.status!=="not_loaded"){
      setPill("home-gex-status",d.status,String(d.status).toUpperCase()+" · CBOE");
      document.getElementById("home-gex-spot").textContent=fmt(d.underlying_spot);document.getElementById("home-gex-futures").textContent=fmt(d.futures_price);document.getElementById("home-gex-call").textContent=fmt(d.call_wall_futures);document.getElementById("home-gex-put").textContent=fmt(d.put_wall_futures);document.getElementById("home-gex-flip").textContent=d.gamma_flip_futures==null?"Not detected":fmt(d.gamma_flip_futures);document.getElementById("home-gex-net").textContent=gexMoney(d.net_gex_per_1pct_move);
      document.getElementById("home-gex-note").textContent="Cboe delayed NDX options mapped to MNQ/NQ · chain age "+(d.cache_age_seconds??"—")+"s · futures price source: "+(d.futures_price_source||"cached")+". Cache-only; no new options request.";
    }else{setPill("home-gex-status","not_loaded","NOT LOADED");}
  }
  if(tasks[2].status==="fulfilled"){
    const d=tasks[2].value;setPill("home-scout-status",d.status,d.status==="ready"?"CONTEXT RANKED":String(d.status||"BLOCKED").toUpperCase());const host=document.getElementById("home-scout-content");host.replaceChildren();const ranked=d.ranked_instruments||[];if(!ranked.length){host.textContent=d.reason||"No ready market-context ranking. No trade recommendation is generated.";}else{ranked.slice(0,3).forEach(item=>{const row=document.createElement("div");row.style.margin="9px 0";const line=document.createElement("div");line.className="bias-row";const label=document.createElement("strong");label.textContent=(item.rank?"#"+item.rank+" ":"")+item.symbol+" · "+String(item.context||item.status).replaceAll("_"," ");const score=document.createElement("span");score.textContent=item.context_score==null?"Blocked":item.context_score+" / 80 pts";line.append(label,score);row.append(line);if(item.context_score!=null){const meter=document.createElement("div");meter.className="context-meter";const fill=document.createElement("span");fill.style.width=Math.max(0,Math.min(100,item.context_score/80*100))+"%";meter.appendChild(fill);row.appendChild(meter);}host.appendChild(row);});}
  }else{document.getElementById("home-scout-content").textContent="Opportunity Scout context unavailable. No recommendation has been generated.";}
}
async function loadNews(){
  setPill("news-status","checking","CHECKING YAHOO");setPill("webull-news-status","checking","CHECKING WEBULL");setPill("calendar-status","checking","CHECKING CALENDAR");
  const results=await Promise.allSettled([
    fetch("/api/news").then(r=>r.json()),
    fetch("/api/news/webull").then(r=>r.json()),
    fetch("/api/calendar/events").then(r=>r.json())
  ]);
  if(results[0].status==="fulfilled")renderYahooNews(results[0].value);else renderYahooNews({status:"unavailable",items:[],error:"Yahoo Finance RSS is temporarily unavailable."});
  if(results[1].status==="fulfilled")renderWebullNews(results[1].value);else renderWebullNews({status:"unavailable",items:[],error:"Webull news is temporarily unavailable."});
  if(results[2].status==="fulfilled")renderCalendar(results[2].value);else renderCalendar({status:"unavailable",events:[],error:"Webull macro calendar is temporarily unavailable. Yahoo fallback link remains available."});
  await loadHomeIntelligence();
}
document.getElementById("refresh-news").addEventListener("click",loadNews);
loadNews();
'''


GEX_CHART_JS = r"""function renderGexChart(data){
 const svg=document.getElementById("gex-chart");if(!svg)return;
 const rows=(data.all_strikes||[]).filter(x=>Number.isFinite(Number(x.futures_level))&&Number.isFinite(Number(x.net_gex))).sort((a,b)=>Number(a.futures_level)-Number(b.futures_level));
 if(!rows.length){svg.innerHTML='<text x="500" y="180" text-anchor="middle" fill="#91a7c2" font-size="16">No strike exposure rows available</text>';return;}
 const min=Math.min(...rows.map(x=>Number(x.futures_level))),max=Math.max(...rows.map(x=>Number(x.futures_level))),span=max-min||1;
 const maxAbs=Math.max(1,...rows.map(x=>Math.abs(Number(x.net_gex))));
 const left=72,right=960,zero=190,barMax=115,step=(right-left)/Math.max(1,rows.length),barW=Math.max(2,Math.min(13,step*.72));
 let parts=['<line x1="'+left+'" y1="'+zero+'" x2="'+right+'" y2="'+zero+'" stroke="#53657b" stroke-width="1"/>'];
 rows.forEach(item=>{const x=left+(Number(item.futures_level)-min)/span*(right-left)-barW/2;const value=Number(item.net_gex);const h=Math.max(1,Math.abs(value)/maxAbs*barMax);const y=value>=0?zero-h:zero;const color=value>=0?'#46d6a0':'#f07883';parts.push('<rect x="'+x.toFixed(1)+'" y="'+y.toFixed(1)+'" width="'+barW.toFixed(1)+'" height="'+h.toFixed(1)+'" rx="1.5" fill="'+color+'" opacity=".82"><title>MNQ '+Number(item.futures_level).toFixed(2)+' · Net GEX '+value.toLocaleString()+'</title></rect>');});
 const markers=[
  {key:'spot',value:Number(data.futures_price),label:'SPOT',color:'#62d9f5'},
  {key:'call',value:Number(data.call_wall_futures),label:'CALL WALL',color:'#46d6a0'},
  {key:'put',value:Number(data.put_wall_futures),label:'PUT WALL',color:'#f07883'},
  {key:'flip',value:data.gamma_flip_futures==null?NaN:Number(data.gamma_flip_futures),label:'GAMMA FLIP',color:'#edbd69'}
 ];
 markers.forEach((m,index)=>{if(!Number.isFinite(m.value)||m.value<min||m.value>max)return;const x=left+(m.value-min)/span*(right-left);parts.push('<line x1="'+x.toFixed(1)+'" y1="42" x2="'+x.toFixed(1)+'" y2="315" stroke="'+m.color+'" stroke-width="2" stroke-dasharray="'+(index===0?'0':'6 4')+'" opacity=".95"/><text x="'+Math.max(left,Math.min(right-8,x)).toFixed(1)+'" y="'+(index%2?34:20)+'" text-anchor="middle" fill="'+m.color+'" font-size="10" font-weight="700">'+m.label+'</text>');});
 parts.push('<text x="'+left+'" y="340" fill="#91a7c2" font-size="11">'+min.toLocaleString(undefined,{maximumFractionDigits:2})+'</text>');
 parts.push('<text x="'+right+'" y="340" text-anchor="end" fill="#91a7c2" font-size="11">'+max.toLocaleString(undefined,{maximumFractionDigits:2})+'</text>');
 parts.push('<text x="'+left+'" y="65" fill="#91a7c2" font-size="10">POSITIVE GEX</text><text x="'+left+'" y="312" fill="#91a7c2" font-size="10">NEGATIVE GEX</text>');
 svg.innerHTML=parts.join('');
 const caption=document.getElementById("gex-chart-caption");if(caption)caption.textContent=rows.length+" strikes · levels mapped to MNQ/NQ";
}
"""

GEX_PEEK_METHOD = r'''    def peek_levels(self, futures_price: float, *, price_source: str) -> dict[str, Any] | None:
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

'''

ROUTES = r'''
@app.get("/api/news/webull")
async def webull_news() -> dict[str, Any]:
    """Return independently cached Webull stock-news headlines for futures proxies."""
    return await webull_news_service.get_news()


@app.get("/api/news/intelligence")
def news_intelligence() -> dict[str, Any]:
    """Build combined per-instrument sentiment from existing provider caches only."""
    return build_instrument_news_intelligence(
        yahoo_news_service.peek_news(),
        webull_news_service.peek_news(),
    )


@app.get("/api/calendar/events")
async def macro_calendar_events() -> dict[str, Any]:
    """Return Webull's structured US macro calendar with bounded pagination."""
    try:
        return await webull_calendar_service.get_calendar()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

'''

GEX_SNAPSHOT_ROUTE = r'''
@app.get("/api/gex/snapshot")
def gex_snapshot() -> dict[str, Any]:
    """Return a cache-only GEX snapshot for Home; never fetch Cboe or market data."""
    cached = market_data_manager.peek_snapshot("1m")
    cache_meta = (cached.get("cache") or {}) if cached else {}
    if cached is None or cache_meta.get("status") != "hit":
        return {"status": "not_loaded", "source": "Cboe delayed options", "message": "No fresh cached market snapshot is available."}
    mnq = next((item for item in cached.get("instruments", []) if item.get("symbol") == "MNQ"), None)
    latest = mnq.get("latest") if isinstance(mnq, dict) else None
    price = latest.get("c") if isinstance(latest, dict) else None
    try:
        price = float(price)
    except (TypeError, ValueError):
        price = 0.0
    if price <= 0:
        return {"status": "not_loaded", "source": "Cboe delayed options", "message": "No usable cached MNQ price is available."}
    try:
        result = gex_service.peek_levels(price, price_source="cached MNQ market-data snapshot")
    except GexUnavailable as exc:
        return {"status": "unavailable", "source": "Cboe delayed options", "message": str(exc)}
    return result or {"status": "not_loaded", "source": "Cboe delayed options", "message": "No cached options chain is available. Open GEX and request an update."}

'''


def require_one(text: str, needle: str, label: str) -> None:
    count = text.count(needle)
    if count != 1:
        raise RuntimeError(f"Safety check failed: expected exactly one {label} anchor, found {count}.")


def main() -> int:
    if not (MC / "main.py").exists() or not (MC / "dashboard.html").exists() or not (MC / "gex.py").exists():
        print("Run this script from the futures-mission-control repository root.", file=sys.stderr)
        return 2
    if not (ROOT / ".venv" / "Scripts" / "python.exe").exists():
        print("Project .venv was not found. This patch expects the existing Windows project venv.", file=sys.stderr)
        return 2

    main_path = MC / "main.py"
    dash_path = MC / "dashboard.html"
    gex_path = MC / "gex.py"
    main_text = main_path.read_text(encoding="utf-8")
    dash_text = dash_path.read_text(encoding="utf-8")
    gex_text = gex_path.read_text(encoding="utf-8")

    import_anchor = "from mission_control.news import YAHOO_CALENDAR_URL, yahoo_news_service"
    news_route_anchor = (
        '@app.get("/api/news")\n'
        'async def news() -> dict[str, Any]:\n'
        '    """Return cached Yahoo Finance ticker headlines; never call TradingView."""\n'
        '    return await yahoo_news_service.get_news()\n'
    )
    integration_anchor = '@app.get("/api/integrations")'
    gex_method_anchor = '    async def get_levels(\n        self, futures_price: float, *, price_source: str\n    ) -> dict[str, Any]:\n'
    news_start = '<section class="section" id="news">'
    news_next = '<section class="section" id="agents">'
    js_start = 'function formatNewsTime(value){'
    js_end = 'const riskProfilesById={};'

    require_one(main_text, import_anchor, "existing Yahoo news import")
    require_one(main_text, news_route_anchor, "existing Yahoo news route")
    require_one(main_text, integration_anchor, "integration route")
    require_one(gex_text, gex_method_anchor, "GEX service get_levels")
    require_one(dash_text, news_start, "News section start")
    require_one(dash_text, news_next, "AI Desk section start")
    require_one(dash_text, js_start, "news JavaScript start")
    require_one(dash_text, js_end, "post-news JavaScript marker")
    if 'id="gex-chart"' in dash_text or 'id="webull-news-feed"' in dash_text or 'class="home-intelligence"' in dash_text:
        raise RuntimeError("Safety check: some intelligence UI additions already exist; refusing to apply the patch twice.")
    if (MC / "webull_news.py").exists() or (MC / "news_intelligence.py").exists():
        raise RuntimeError("Safety check: one of the new module files already exists. Nothing was changed.")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups = {}
    line_endings = {}
    for path in (main_path, dash_path, gex_path):
        raw = path.read_bytes()
        line_endings[path] = "\r\n" if b"\r\n" in raw else "\n"
    created: list[Path] = []
    try:
        for path in (main_path, dash_path, gex_path):
            backup = path.with_name(path.name + f".before_intelligence_{stamp}.bak")
            shutil.copy2(path, backup)
            backups[path] = backup

        # Add imports without touching the existing Webull OAuth or market-data imports.
        main_text = main_text.replace(
            import_anchor,
            import_anchor + "\nfrom mission_control.webull_news import webull_calendar_service, webull_news_service\nfrom mission_control.news_intelligence import build_instrument_news_intelligence",
            1,
        )
        main_text = main_text.replace(news_route_anchor, news_route_anchor + "\n\n" + ROUTES, 1)
        main_text = main_text.replace(integration_anchor, GEX_SNAPSHOT_ROUTE + "\n" + integration_anchor, 1)
        gex_text = gex_text.replace(gex_method_anchor, GEX_PEEK_METHOD + gex_method_anchor, 1)

        # Add Home snapshots inside the existing Overview section. Locate the
        # final section close before Charts so harmless nested wrapper markup is preserved.
        overview_start = dash_text.index('<section class="section active" id="overview">')
        charts_start = dash_text.index('<section class="section" id="charts">', overview_start)
        # Overview markup in this app has no nested <section> elements.
        overview_close = dash_text.find('</section>', overview_start, charts_start)
        if overview_close < 0:
            raise RuntimeError("Safety check: could not locate the Overview section close.")
        dash_text = dash_text[:overview_close] + HOME_MARKUP + "\n" + dash_text[overview_close:]

        # Add the GEX chart renderer and invoke it after a successful chain update.
        gex_money_anchor = 'function gexMoney(value){return (Number(value)/1000000).toFixed(2)+"M";}'
        require_one(dash_text, gex_money_anchor, "GEX money formatter")
        dash_text = dash_text.replace(gex_money_anchor, gex_money_anchor + "\n" + GEX_CHART_JS, 1)
        gex_render_anchor = 'document.getElementById("gex-assumptions").textContent=data.assumptions.join(" ");'
        require_one(dash_text, gex_render_anchor, "GEX assumptions renderer")
        dash_text = dash_text.replace(gex_render_anchor, 'renderGexChart(data);\n    ' + gex_render_anchor, 1)

        # Color the existing table's net/call/put GEX columns without changing its data contract.
        gex_cell_anchor = '[fmt(item.futures_level),gexMoney(item.net_gex),gexMoney(item.call_gex),gexMoney(item.put_gex),fmt(item.call_oi),fmt(item.put_oi)].forEach(value=>{\n        const td=document.createElement("td");td.style.padding="9px";td.textContent=value;tr.appendChild(td);\n      });'
        require_one(dash_text, gex_cell_anchor, "GEX table cell renderer")
        gex_cell_new = '[fmt(item.futures_level),gexMoney(item.net_gex),gexMoney(item.call_gex),gexMoney(item.put_gex),fmt(item.call_oi),fmt(item.put_oi)].forEach((value,index)=>{\n        const td=document.createElement("td");td.style.padding="9px";td.textContent=value;\n        if(index===0)td.style.fontWeight="750";\n        if(index===1)td.style.color=Number(item.net_gex)>=0?"var(--green)":"var(--red)";\n        if(index===2)td.style.color="var(--green)";\n        if(index===3)td.style.color="var(--red)";\n        tr.appendChild(td);\n      });'
        dash_text = dash_text.replace(gex_cell_anchor, gex_cell_new, 1)

        # Add GEX chart before the existing detailed strike table.
        gex_section_start = dash_text.index('<section class="section" id="gex">')
        gex_section_end = dash_text.index('</section>', gex_section_start)
        gex_html = dash_text[gex_section_start:gex_section_end]
        table_anchor = '<div class="panel" style="margin-top:12px">\n  <div class="panel-head"><div class="panel-title">Largest strike exposures</div>'
        require_one(gex_html, table_anchor, "GEX strike table")
        gex_html = gex_html.replace(table_anchor, GEX_CHART_MARKUP + "\n" + table_anchor, 1)
        dash_text = dash_text[:gex_section_start] + gex_html + dash_text[gex_section_end:]

        # Replace only the News section, keeping the following AI Desk section intact.
        start = dash_text.index(news_start)
        end = dash_text.index(news_next, start)
        old_news = dash_text[start:end]
        if old_news.count('</section>') != 1:
            raise RuntimeError("Safety check: unexpected nested section structure in News workspace.")
        dash_text = dash_text[:start] + NEWS_MARKUP + "\n" + dash_text[end:]

        # Replace only existing news rendering functions; all other dashboard JS remains.
        start = dash_text.index(js_start)
        end = dash_text.index(js_end, start)
        dash_text = dash_text[:start] + JS_REPLACEMENT + "\n" + dash_text[end:]

        style_anchor = '</style>'
        require_one(dash_text, style_anchor, "dashboard style close")
        dash_text = dash_text.replace(style_anchor, CSS_ADDITIONS + "\n" + style_anchor, 1)

        (MC / "webull_news.py").write_text(WEBULL_NEWS_SOURCE, encoding="utf-8", newline="\n")
        created.append(MC / "webull_news.py")
        (MC / "news_intelligence.py").write_text(NEWS_INTELLIGENCE_SOURCE, encoding="utf-8", newline="\n")
        created.append(MC / "news_intelligence.py")
        (TESTS / "test_webull_news.py").write_text(TEST_SOURCE, encoding="utf-8", newline="\n")
        created.append(TESTS / "test_webull_news.py")
        (TESTS / "test_intelligence_api.py").write_text(API_TEST_SOURCE, encoding="utf-8", newline="\n")
        created.append(TESTS / "test_intelligence_api.py")
        main_path.write_text(main_text, encoding="utf-8", newline=line_endings[main_path])
        dash_path.write_text(dash_text, encoding="utf-8", newline=line_endings[dash_path])
        gex_path.write_text(gex_text, encoding="utf-8", newline=line_endings[gex_path])

        # Syntax-check new Python modules before running project tests.
        py = ROOT / ".venv" / "Scripts" / "python.exe"
        syntax = subprocess.run(
            [str(py), "-m", "py_compile", str(MC / "webull_news.py"), str(MC / "news_intelligence.py"), str(main_path), str(gex_path)],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        if syntax.returncode != 0:
            raise RuntimeError("Python syntax check failed:\n" + syntax.stdout + syntax.stderr)

        tests = subprocess.run(
            [str(py), "-m", "pytest", "-q", "tests/test_webull_news.py", "tests/test_intelligence_api.py", "tests/test_news.py", "tests/test_news_catalyst.py", "tests/test_gex.py", "tests/test_api.py"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        print(tests.stdout)
        if tests.returncode != 0:
            print(tests.stderr, file=sys.stderr)
            raise RuntimeError("Targeted regression tests failed; restoring the three patched files and removing only newly created files.")

        full_tests = subprocess.run(
            [str(py), "-m", "pytest", "-q"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        print(full_tests.stdout)
        if full_tests.returncode != 0:
            print(full_tests.stderr, file=sys.stderr)
            raise RuntimeError("Full regression suite failed; restoring the three patched files and removing only newly created files.")

        print("\nPATCH APPLIED AND FULL REGRESSION SUITE PASSED.")
        print("Backups:")
        for original, backup in backups.items():
            print(f"  {backup.relative_to(ROOT)}")
        print("New files:")
        for path in created:
            print(f"  {path.relative_to(ROOT)}")
        print("No Git commands were run. No existing uncommitted files other than main.py, dashboard.html and gex.py were modified.")
        return 0
    except Exception as exc:
        # Best-effort transactional rollback: only this patch's three modified files and newly created files.
        for original, backup in backups.items():
            if backup.exists():
                shutil.copy2(backup, original)
        for path in created:
            if path.exists():
                path.unlink()
        print(f"PATCH NOT KEPT: {exc}", file=sys.stderr)
        print("The original main.py, dashboard.html and gex.py were restored from the timestamped backups.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
