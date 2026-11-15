from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from mission_control.webull_news import (
    READ_ONLY_NEWS_TOOLS,
    WebullCalendarService,
    WebullNewsService,
    _decode_tool_result,
    _unwrap_payload,
    normalize_calendar_events,
    normalize_webull_news,
)
from mission_control.webull import AuthorizationRequired as WebullAuthorizationRequired
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


def test_webull_news_service_preserves_sanitized_provider_error_details():
    async def failed_fetch():
        raise RuntimeError("HTTP 429 rate limit access_token=do-not-leak Authorization: Bearer bearer-secret")

    service = WebullNewsService(fetcher=failed_fetch, clock=lambda: 100.0)

    async def run():
        result = await service.get_news()
        assert result["status"] == "unavailable"
        assert "429" in result["error_message"]
        assert "rate limit" in result["error_message"]
        assert "do-not-leak" not in result["error_message"]
        assert "bearer-secret" not in result["error_message"]
        assert result["provider_requests_triggered"] == 0

    asyncio.run(run())


def test_webull_mcp_tool_error_retains_provider_message():
    class TextPart:
        text = "HTTP 429 Too Many Requests"

    class ToolResult:
        is_error = True
        structured_content = None
        content = [TextPart()]

    try:
        _decode_tool_result(ToolResult())
    except RuntimeError as exc:
        assert "429" in str(exc)
        assert "Too Many Requests" in str(exc)
    else:
        raise AssertionError("Expected Webull MCP tool error to be surfaced")



def test_news_normalizer_preserves_all_rows_from_top_level_array_response():
    now = datetime.now(UTC)
    payload = [
        {"news_id": "one", "title": "Nasdaq update one", "url": "https://example.com/1",
         "publish_time": now.isoformat()},
        {"news_id": "two", "title": "Nasdaq update two", "url": "https://example.com/2",
         "publish_time": now.isoformat()},
    ]
    unwrapped = _unwrap_payload(payload)
    assert len(unwrapped["data"]) == 2
    items = normalize_webull_news(payload, requested_symbol="QQQ", received_at=now)
    assert [item["news_id"] for item in items] == ["one", "two"]


def test_news_normalizer_supports_common_mcp_result_wrappers():
    now = datetime.now(UTC)
    row = {"news_id": "wrapped", "title": "Wrapped news", "url": "https://example.com/w",
           "publish_time": now.isoformat()}
    for payload in (
        {"result": {"data": [row]}},
        {"data": {"items": [row]}},
        {"articles": [row]},
    ):
        assert len(normalize_webull_news(payload, requested_symbol="SPY", received_at=now)) == 1


def test_webull_news_manual_retry_clears_only_authorization_cooldown():
    calls = []

    async def fetcher():
        calls.append(1)
        if len(calls) == 1:
            raise WebullAuthorizationRequired("Saved Webull token was rejected.")
        return {"source": "Webull Cloud MCP", "items": [], "item_count": 0,
                "status": "fresh", "provider_tool_requests": 1}

    service = WebullNewsService(fetcher=fetcher, clock=lambda: 100.0)

    async def run():
        failed = await service.get_news()
        assert failed["status"] == "unavailable"
        cooling = await service.get_news()
        assert cooling["status"] == "cooldown"
        recovered = await service.get_news(force_refresh=True)
        assert recovered["status"] == "fresh"

    asyncio.run(run())
    assert len(calls) == 2


def test_webull_news_manual_retry_does_not_bypass_quota_cooldown():
    calls = []

    async def fetcher():
        calls.append(1)
        raise RuntimeError("HTTP 429 Too Many Requests")

    service = WebullNewsService(fetcher=fetcher, clock=lambda: 100.0)

    async def run():
        failed = await service.get_news()
        assert failed["status"] == "unavailable"
        retry = await service.get_news(force_refresh=True)
        assert retry["status"] == "cooldown"

    asyncio.run(run())
    assert len(calls) == 1
