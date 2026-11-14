from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from mission_control.news import (
    YahooFinanceNewsService,
    YahooNewsUnavailable,
    parse_yahoo_rss,
)


FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Yahoo Finance</title>
<item>
  <title>Nasdaq futures rise before inflation data</title>
  <link>https://finance.yahoo.com/news/sample-story</link>
  <guid>sample-1</guid>
  <pubDate>Sat, 14 Nov 2026 14:30:00 +0000</pubDate>
  <description><![CDATA[<p>Markets <b>move</b> ahead of CPI.</p>]]></description>
  <source url="https://finance.yahoo.com/">Yahoo Finance</source>
  <category>Futures</category>
</item>
<item>
  <title>Nasdaq futures rise before inflation data</title>
  <link>https://finance.yahoo.com/news/sample-story</link>
  <guid>sample-1</guid>
</item>
<item><title>Missing link item</title><guid>sample-2</guid></item>
</channel></rss>"""


def test_parse_yahoo_rss_normalizes_metadata_and_deduplicates() -> None:
    received = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)
    items = parse_yahoo_rss(FIXTURE, received_at=received)
    assert len(items) == 1
    item = items[0]
    assert item["title"] == "Nasdaq futures rise before inflation data"
    assert item["summary"] == "Markets move ahead of CPI."
    assert item["publisher"] == "Yahoo Finance"
    assert item["published_at"] == "2026-11-14T14:30:00+00:00"
    assert item["received_at"] == "2026-11-14T15:00:00+00:00"
    assert "symbols" not in item


def test_parse_yahoo_rss_rejects_invalid_xml() -> None:
    with pytest.raises(YahooNewsUnavailable):
        parse_yahoo_rss("<rss><channel>")


def test_news_service_uses_one_fetch_for_ten_minute_cache() -> None:
    now = [100.0]
    calls = []

    def fetcher() -> str:
        calls.append(1)
        return FIXTURE

    service = YahooFinanceNewsService(fetcher=fetcher, clock=lambda: now[0])

    async def run() -> None:
        first = await service.get_news()
        second = await service.get_news()
        assert first["status"] == "fresh"
        assert first["upstream_requests"] == 1
        assert second["status"] == "cached"
        assert second["upstream_requests"] == 1
        assert first["items"][0]["title"] == second["items"][0]["title"]
        assert service.fetch_count == 1

    asyncio.run(run())
    assert len(calls) == 1


def test_news_service_serves_stale_items_after_refresh_failure() -> None:
    now = [100.0]
    calls = 0

    def fetcher() -> str:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise OSError("simulated Yahoo outage")
        return FIXTURE

    service = YahooFinanceNewsService(
        fetcher=fetcher,
        clock=lambda: now[0],
        ttl_seconds=5,
        error_cooldown_seconds=300,
    )

    async def run() -> None:
        await service.get_news()
        now[0] += 6
        stale = await service.get_news()
        assert stale["status"] == "stale"
        assert stale["items"][0]["id"] == "sample-1"
        assert stale["cache_age_seconds"] == 6
        assert stale["retry_after_seconds"] == 300
        cooling = await service.get_news()
        assert cooling["status"] == "stale"
        assert service.fetch_count == 2

    asyncio.run(run())


def test_news_service_does_not_retry_during_error_cooldown() -> None:
    calls = []

    def fetcher() -> str:
        calls.append(1)
        raise OSError("simulated Yahoo outage")

    service = YahooFinanceNewsService(fetcher=fetcher, clock=lambda: 100.0)

    async def run() -> None:
        first = await service.get_news()
        second = await service.get_news()
        assert first["status"] == "unavailable"
        assert second["status"] == "cooldown"
        assert second["upstream_requests"] == 1
        assert service.fetch_count == 1

    asyncio.run(run())
    assert len(calls) == 1


def test_parse_yahoo_rss_discards_non_https_links() -> None:
    unsafe = FIXTURE.replace(
        "https://finance.yahoo.com/news/sample-story",
        "javascript:alert(1)",
    )
    assert parse_yahoo_rss(unsafe) == []




def test_news_service_peek_reads_cache_without_fetching() -> None:
    now = [100.0]
    calls = []

    def fetcher() -> str:
        calls.append(1)
        return FIXTURE

    service = YahooFinanceNewsService(
        fetcher=fetcher,
        clock=lambda: now[0],
        ttl_seconds=5,
    )
    empty = service.peek_news()
    assert empty["status"] == "not_loaded"
    assert empty["provider_requests_triggered"] == 0

    async def run() -> None:
        await service.get_news()
        cached = service.peek_news()
        assert cached["status"] == "cached"
        assert cached["provider_requests_triggered"] == 0
        now[0] += 6
        stale = service.peek_news()
        assert stale["status"] == "stale"
        assert stale["provider_requests_triggered"] == 0
        assert service.fetch_count == 1

    asyncio.run(run())
    assert len(calls) == 1
