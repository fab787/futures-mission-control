from datetime import datetime, timedelta, timezone

from mission_control.news_fusion_adapter import attach_fused_scores

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def webull_item(title, sentiment, instruments, hours_ago=1):
    when = NOW - timedelta(hours=hours_ago)
    return {"title": title, "sentiment": sentiment, "futures_instruments": instruments,
            "published_at": when, "published": when}


def base_rows():
    return {"instruments": [{"symbol": "MNQ"}, {"symbol": "MES"}, {"symbol": "MGC"}]}


def test_each_instrument_gets_one_fused_block():
    result = attach_fused_scores(base_rows(), None, {"items": []}, now=NOW)
    for row in result["instruments"]:
        assert set(row["fused"]) >= {"score", "stance", "confidence", "bias"}
    assert "fused_overall" in result


def test_three_negative_stories_are_bearish_and_wait_without_price():
    items = [webull_item(f"Nasdaq story {i}", "NEGATIVE", ["MNQ"]) for i in range(3)]
    result = attach_fused_scores(base_rows(), None, {"items": items}, now=NOW)
    mnq = result["instruments"][0]["fused"]
    assert mnq["stance"] == "bearish"
    assert mnq["bias"] == "WAIT"


def test_instrument_without_items_is_neutral_zero():
    items = [webull_item(f"s{i}", "NEGATIVE", ["MNQ"]) for i in range(3)]
    result = attach_fused_scores(base_rows(), None, {"items": items}, now=NOW)
    mes = result["instruments"][1]["fused"]
    assert mes["score"] == 0.0
    assert mes["stance"] == "neutral"


def test_overall_is_mean_of_reporting_instruments_only():
    items = [webull_item(f"s{i}", "NEGATIVE", ["MNQ"]) for i in range(3)]
    result = attach_fused_scores(base_rows(), None, {"items": items}, now=NOW)
    overall = result["fused_overall"]
    assert overall["instruments_reporting"] == 1
    assert overall["score"] == result["instruments"][0]["fused"]["score"]


def test_no_evidence_gives_no_overall_score():
    result = attach_fused_scores(base_rows(), None, None, now=NOW)
    assert result["fused_overall"]["score"] is None
    assert result["fused_overall"]["instruments_reporting"] == 0


def test_yahoo_ai_estimate_is_its_own_source():
    items = [webull_item(f"s{i}", "NEGATIVE", ["MNQ"]) for i in range(2)]
    yahoo = [{"title": "Yahoo story", "symbol": "QQQ", "sentiment": "POSITIVE",
              "sentiment_source": "ai_estimate", "published_at": NOW - timedelta(hours=1)}]
    result = attach_fused_scores(base_rows(), {"items": yahoo}, {"items": items}, now=NOW)
    mnq = result["instruments"][0]["fused"]
    assert mnq["coverage"]["sources"] == ["webull", "yahoo"]
    assert mnq["clusters"] == 3