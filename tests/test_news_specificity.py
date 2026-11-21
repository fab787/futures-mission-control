from datetime import datetime, timedelta, timezone

from mission_control.news_fusion_adapter import fused_for_instrument

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def wb(title, instrument, label="NEGATIVE", hours_ago=1):
    when = NOW - timedelta(hours=hours_ago)
    return {"title": title, "sentiment": label, "futures_instruments": [instrument],
            "published_at": when, "published": when}


def test_generic_headlines_alone_give_no_direction():
    items = [wb(f"Stock Market: Will S&P 500 Open Up or Down {i}", "MNQ") for i in range(3)]
    result = fused_for_instrument("MNQ", items, [], NOW)
    assert result["stance"] == "neutral"
    assert result["score"] == 0.0
    assert "instrument-specific" in result["reason"]


def test_named_instrument_stories_still_give_direction():
    items = [wb(f"Nasdaq futures fall on chip selloff {i}", "MNQ") for i in range(3)]
    assert fused_for_instrument("MNQ", items, [], NOW)["stance"] == "bearish"


def test_mixed_specific_and_generic_keeps_direction():
    items = [wb("Nasdaq futures fall", "MNQ"), wb("QQQ slides", "MNQ"),
             wb("Stock market: Will S&P 500 open up", "MNQ")]
    assert fused_for_instrument("MNQ", items, [], NOW)["stance"] == "bearish"


def test_gold_stories_name_mgc():
    items = [wb(f"Gold stocks fall {i}", "MGC") for i in range(3)]
    assert fused_for_instrument("MGC", items, [], NOW)["stance"] == "bearish"