from datetime import datetime, timedelta, timezone

from mission_control.sentiment_analyst import (
    compare_sentiment_assessments,
    sentiment_interface_status,
)


NOW = datetime(2026, 11, 14, 20, 0, tzinfo=timezone.utc)


def assessment(provider, stance="bullish", *, age_minutes=2, symbol="MNQ"):
    return {
        "provider": provider,
        "model": "test-model",
        "analyzed_at": (NOW - timedelta(minutes=age_minutes)).isoformat(),
        "sources": [{
            "title": "Market source",
            "url": "https://example.com/market",
            "published_at": (NOW - timedelta(minutes=3)).isoformat(),
        }],
        "findings": [{
            "symbol": symbol,
            "stance": stance,
            "summary": f"Source-grounded {stance} context.",
            "confidence": "medium",
        }],
    }


def test_sentiment_interface_does_not_claim_provider_connections():
    result = sentiment_interface_status()
    assert result["status"] == "awaiting_reviews"
    assert all(not item["connected"] for item in result["providers"])
    assert result["provider_requests_triggered"] == 0
    assert result["market_data_requests_triggered"] == 0
    assert result["provider_manager"]["api_calls_enabled"] is False
    assert result["provider_manager"]["network_requests_triggered"] == 0
    assert all(not item["api_calls_enabled"] for item in result["provider_manager"]["providers"])


def test_sentiment_analyst_reports_agreement_between_fresh_reviews():
    result = compare_sentiment_assessments(
        [assessment("claude"), assessment("chatgpt")], now=NOW
    )
    mnq = result["instruments"][0]
    assert result["status"] == "ready"
    assert mnq["status"] == "agreement"
    assert mnq["stance"] == "bullish"
    assert len(mnq["reviews"]) == 2
    assert all(review["sources"][0]["url"].startswith("https://") for review in mnq["reviews"])
    assert result["provider_requests_triggered"] == 0
    assert result["trade_authority"] == "none"


def test_sentiment_analyst_preserves_disagreement_without_averaging():
    result = compare_sentiment_assessments(
        [assessment("claude", "bullish"), assessment("chatgpt", "bearish")],
        now=NOW,
    )
    mnq = result["instruments"][0]
    assert mnq["status"] == "disagreement"
    assert mnq["stance"] == "not_resolved"
    assert {review["stance"] for review in mnq["reviews"]} == {"bullish", "bearish"}


def test_sentiment_analyst_marks_single_provider_without_claiming_consensus():
    result = compare_sentiment_assessments([assessment("claude")], now=NOW)
    assert result["instruments"][0]["status"] == "single_provider"
    assert result["instruments"][0]["stance"] == "bullish"


def test_sentiment_analyst_rejects_stale_and_naive_timestamps():
    stale = assessment("claude", age_minutes=30)
    naive = assessment("chatgpt")
    naive["analyzed_at"] = "2026-11-14T19:59:00"
    result = compare_sentiment_assessments([stale, naive], now=NOW)
    assert result["status"] == "awaiting_reviews"
    assert result["instruments"] == []
    assert len(result["rejected_assessments"]) == 2


def test_sentiment_analyst_rejects_unattributed_or_unsafe_sources():
    invalid = assessment("claude")
    invalid["sources"] = [{"title": "No secure source", "url": "http://example.com"}]
    result = compare_sentiment_assessments([invalid], now=NOW)
    assert result["instruments"] == []
    assert "HTTPS URL" in result["rejected_assessments"][0]


def test_sentiment_analyst_rejects_duplicate_provider_assessment():
    result = compare_sentiment_assessments(
        [assessment("claude"), assessment("claude", "bearish")], now=NOW
    )
    assert len(result["instruments"][0]["reviews"]) == 1
    assert any("duplicate" in item for item in result["rejected_assessments"])


def test_sentiment_analyst_rejects_unknown_symbols_and_stances():
    invalid = assessment("claude")
    invalid["findings"][0]["symbol"] = "ES"
    invalid["findings"][0]["stance"] = "guaranteed_up"
    result = compare_sentiment_assessments([invalid], now=NOW)
    assert result["instruments"] == []
