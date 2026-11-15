from __future__ import annotations

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
