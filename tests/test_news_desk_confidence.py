from datetime import datetime, timezone

from mission_control.news_analyst_desk import build_news_analyst_desk

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def test_confidence_is_label_and_value_object():
    result = build_news_analyst_desk(
        yahoo_items=[], webull_items=[], calendar_events=[],
        market_snapshot=None, now=NOW, calendar_status=None,
    )
    for row in result["instruments"]:
        conf = row["confidence"]
        assert isinstance(conf, dict)
        assert conf["label"] in {"low", "medium", "high"}
        assert isinstance(conf["value"], float)