"""Read-only Webull news and US macro calendar adapters.

This module is deliberately separate from mission_control.webull: the existing
futures-market tool allowlist and OAuth scopes are not modified. Only the
verified get_stock_news and get_macroeconomic_calendar tools are called.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from mission_control.webull import (
    AuthorizationRequired as WebullAuthorizationRequired,
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


def _safe_error_message(error: BaseException | Any) -> str:
    """Expose useful provider diagnostics while redacting credential-like values."""
    message = str(error)
    message = re.sub(
        r"(?i)\bbearer\s+[^\s,;]+",
        "Bearer [REDACTED]",
        message,
    )
    message = re.sub(
        r"(?i)\b(authorization|access[_-]?token|refresh[_-]?token|api[_-]?key|password)\b"
        r"(\s*[:=]\s*)[^\s,;]+",
        r"\1=[REDACTED]",
        message,
    )
    return message[:600]


def _decode_tool_result(result: Any) -> Any:
    structured = getattr(result, "structured_content", None)
    if getattr(result, "is_error", False):
        details = structured
        if details is None:
            details = [
                getattr(part, "text", str(part))
                for part in (getattr(result, "content", []) or [])
            ]
        raise RuntimeError(
            "Webull MCP tool returned an error: "
            + _safe_error_message(json.dumps(details, default=str))
        )
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
    """Normalize MCP structured content without discarding top-level result arrays."""
    if isinstance(value, str):
        try:
            return _unwrap_payload(json.loads(value))
        except (TypeError, json.JSONDecodeError):
            return {}
    if isinstance(value, list):
        return {"data": [item for item in value if isinstance(item, dict)]}
    if not isinstance(value, dict):
        return {}

    for key in ("result", "payload", "response"):
        nested_value = value.get(key)
        if isinstance(nested_value, (dict, list, str)):
            nested = _unwrap_payload(nested_value)
            if _extract_news_rows(nested) is not None or "pagination_key" in nested:
                merged = dict(nested)
                if "pagination_key" not in merged and value.get("pagination_key") is not None:
                    merged["pagination_key"] = value["pagination_key"]
                return merged

    data_value = value.get("data")
    if isinstance(data_value, (dict, list, str)):
        nested = _unwrap_payload(data_value)
        if _extract_news_rows(nested) is not None or "pagination_key" in nested:
            merged = dict(nested)
            if "pagination_key" not in merged and value.get("pagination_key") is not None:
                merged["pagination_key"] = value["pagination_key"]
            return merged

    for key in ("items", "articles", "news", "results", "records"):
        rows = value.get(key)
        if isinstance(rows, list):
            merged = dict(value)
            merged["data"] = rows
            return merged
    return value


def _extract_news_rows(data: dict[str, Any]) -> list[dict[str, Any]] | None:
    for key in ("data", "items", "articles", "news", "results", "records"):
        rows = data.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return None


def _is_authorization_failure(error: BaseException) -> bool:
    if isinstance(error, WebullAuthorizationRequired):
        return True
    message = str(error).casefold()
    return "http 417" in message or "invalid_token" in message or "token refresh failed: 417" in message


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
    rows = _extract_news_rows(data) or []
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
                    if _extract_news_rows(data) is None:
                        raise ValueError(
                            "Webull news response did not contain a recognized article array"
                        )
                    items = normalize_webull_news(
                        data, requested_symbol=proxy_symbol, received_at=received
                    )
                    instruments[instrument] = {
                        "status": "available" if items else "empty",
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
                        "error_message": _safe_error_message(exc),
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
    available = sum(value["status"] in {"available", "empty"} for value in instruments.values())
    if available == 0:
        failures = "; ".join(
            f"{symbol}: {details.get('error_message') or details.get('error') or 'no data'}"
            for symbol, details in instruments.items()
        )
        raise WebullNewsUnavailable(
            "Webull did not return news for any configured proxy symbol. " + failures
        )
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
        self._last_error_kind: str | None = None
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

    async def _get(
        self,
        fetcher: Callable[[], Awaitable[dict[str, Any]]],
        *,
        unavailable: dict[str, Any],
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        now = self._clock()
        if self._cache and now - self._cache[0] < self.ttl_seconds and not force_refresh:
            return self._response("cached", now)

        # Only an auth-related cooldown may be cleared by an explicit user retry.
        # Quota/rate-limit and other provider cooldowns remain enforced.
        auth_retry = force_refresh and self._last_error_kind == "authorization"
        if now < self._cooldown_until and not auth_retry:
            if self._cache:
                return self._response("stale", now)
            return {
                **unavailable,
                "status": "cooldown",
                "upstream_refreshes": self.fetch_count,
                "retry_after_seconds": round(self._cooldown_until - now, 1),
                "provider_requests_triggered": 0,
            }

        async with self._lock:
            now = self._clock()
            if self._cache and now - self._cache[0] < self.ttl_seconds and not force_refresh:
                return self._response("cached", now)
            auth_retry = force_refresh and self._last_error_kind == "authorization"
            if now < self._cooldown_until and not auth_retry:
                if self._cache:
                    return self._response("stale", now)
                return {
                    **unavailable,
                    "status": "cooldown",
                    "upstream_refreshes": self.fetch_count,
                    "retry_after_seconds": round(self._cooldown_until - now, 1),
                    "provider_requests_triggered": 0,
                }
            if auth_retry:
                self._cooldown_until = 0.0
                self._last_error_kind = None

            self.fetch_count += 1
            try:
                payload = await fetcher()
            except Exception as exc:
                self._last_error_kind = (
                    "authorization" if _is_authorization_failure(exc) else type(exc).__name__
                )
                self._cooldown_until = self._clock() + self.error_cooldown_seconds
                if self._cache:
                    response = self._response("stale", self._clock())
                    response["last_error"] = type(exc).__name__
                    response["last_error_message"] = _safe_error_message(exc)
                    return response
                return {
                    **unavailable,
                    "status": "unavailable",
                    "upstream_refreshes": self.fetch_count,
                    "error": type(exc).__name__,
                    "error_message": _safe_error_message(exc),
                    "retry_after_seconds": self.error_cooldown_seconds,
                    "provider_requests_triggered": 0,
                }
            self._last_error_kind = None
            self._cooldown_until = 0.0
            self._cache = (self._clock(), payload)
            return self._response(str(payload.get("status") or "fresh"), self._clock())


class WebullNewsService(_CachedService):
    def __init__(self, *, fetcher: Callable[[], Awaitable[dict[str, Any]]] = _fetch_news_from_webull, clock: Callable[[], float] = time.monotonic, ttl_seconds: float = NEWS_CACHE_TTL_SECONDS, error_cooldown_seconds: float = ERROR_COOLDOWN_SECONDS) -> None:
        super().__init__(clock=clock, ttl_seconds=ttl_seconds, error_cooldown_seconds=error_cooldown_seconds)
        self._fetcher = fetcher

    def peek_news(self) -> dict[str, Any]:
        return self.peek(empty={"status": "not_loaded", "source": "Webull Cloud MCP", "items": [], "item_count": 0, "instruments": {}})

    async def get_news(self, *, force_refresh: bool = False) -> dict[str, Any]:
        return await self._get(
            self._fetcher,
            unavailable={"source": "Webull Cloud MCP", "items": [], "item_count": 0, "instruments": {}},
            force_refresh=force_refresh,
        )


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
