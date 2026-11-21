from datetime import datetime, timedelta, timezone

from mission_control.news_analyst_desk import price_relation, recommendation
from mission_control.news_fusion_adapter import fused_for_instrument

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def wb(title, sentiment, instrument="MNQ", hours_ago=1):
    when = NOW - timedelta(hours=hours_ago)
    return {"title": title, "sentiment": sentiment, "futures_instruments": [instrument],
            "published_at": when, "published": when}


def test_three_negative_webull_stories_are_bearish_for_mnq():
    items = [wb(f"Nasdaq story {i}", "NEGATIVE") for i in range(3)]
    assert fused_for_instrument("MNQ", items, [], NOW)["stance"] == "bearish"


def test_no_items_is_neutral_zero():
    result = fused_for_instrument("MES", [], [], NOW)
    assert result["stance"] == "neutral"
    assert result["score"] == 0.0


def test_other_instruments_items_do_not_leak():
    items = [wb(f"Gold story {i}", "NEGATIVE", instrument="MGC") for i in range(3)]
    assert fused_for_instrument("MNQ", items, [], NOW)["clusters"] == 0


def test_bearish_news_with_down_price_is_short():
    assert recommendation("bearish", price_relation("bearish", "down"), "OPEN", None, 5)[0] == "SHORT BIAS"


def test_bearish_news_with_up_price_is_wait():
    assert recommendation("bearish", price_relation("bearish", "up"), "OPEN", None, 5)[0] == "WAIT"


def test_neutral_news_stays_neutral_even_with_down_price():
    assert recommendation("neutral", price_relation("neutral", "down"), "OPEN", None, 5)[0] == "NEUTRAL"


def test_conflict_is_wait():
    assert recommendation("conflict", None, "OPEN", None, 5)[0] == "WAIT"