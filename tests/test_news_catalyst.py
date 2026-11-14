from mission_control.news_catalyst import analyze_news_catalysts


def test_news_catalyst_classifies_cached_headlines_without_provider_calls():
    payload = {
        "status": "cached",
        "source": "Yahoo Finance RSS",
        "fetched_at": "2026-11-14T17:00:00+00:00",
        "cache_age_seconds": 120,
        "symbols": ["MNQ=F", "MES=F", "MGC=F", "QQQ", "SPY", "GLD"],
        "items": [
            {
                "id": "1",
                "title": "Federal Reserve signals interest rate decision",
                "summary": "Markets await policy update.",
                "publisher": "Yahoo Finance",
                "url": "https://finance.yahoo.com/news/example",
                "published_at": "2026-11-14T16:00:00+00:00",
                "received_at": "2026-11-14T17:00:00+00:00",
            },
            {
                "id": "2",
                "title": "Gold prices climb",
                "summary": "Bullion advances in afternoon trading.",
                "publisher": "Yahoo Finance",
                "url": "https://finance.yahoo.com/news/gold",
            },
        ],
    }
    result = analyze_news_catalysts(payload)
    assert result["status"] == "ready"
    assert result["summary"]["headline_count"] == 2
    assert result["summary"]["high_priority"] == 1
    assert "monetary_policy" in result["headlines"][0]["themes"]
    assert result["headlines"][0]["directional_bias"] == "not_assessed"
    assert result["headlines"][0]["instrument_mapping"] == "unavailable_from_combined_feed"
    assert result["calendar"]["events"] == []
    assert result["provider_requests_triggered"] == 0
    assert result["trade_authority"] == "none"


def test_news_catalyst_marks_old_headlines_as_stale_context():
    result = analyze_news_catalysts({
        "status": "stale",
        "items": [{"title": "CPI inflation report", "url": "https://example.com/a"}],
    })
    assert result["status"] == "stale_context"
    assert "stale" in result["reason"]


def test_news_catalyst_blocks_when_cache_has_not_been_loaded():
    result = analyze_news_catalysts({
        "status": "not_loaded",
        "items": [],
    })
    assert result["status"] == "blocked"
    assert result["headlines"] == []
    assert result["provider_requests_triggered"] == 0


def test_news_catalyst_does_not_invent_calendar_events_or_symbol_mapping():
    result = analyze_news_catalysts({
        "status": "fresh",
        "items": [{"title": "Nasdaq stocks gain", "url": "https://example.com/a"}],
    })
    assert result["calendar"]["status"] == "page_link_only"
    assert result["calendar"]["events"] == []
    assert "not reliably mapped" in result["headline_scope"]
