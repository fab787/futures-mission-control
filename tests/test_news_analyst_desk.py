from datetime import UTC, datetime, timedelta

from mission_control.news_analyst_desk import build_news_analyst_desk

NOW = datetime(2026, 11, 20, 2, 0, tzinfo=UTC)
DOWN = [100 - i * 0.5 for i in range(130)]
UP = [90 + i * 0.5 for i in range(130)]


def wb(title, label, minutes_ago=10):
    return {
        "title": title, "summary": "", "url": "https://example.com/" + title.replace(" ", "-"),
        "publisher": "Webull", "sentiment": label, "futures_instruments": ["MNQ"],
        "published_at": NOW - timedelta(minutes=minutes_ago),
    }


def bearish_three():
    return [wb(f"Fed hawkish {i}", "bearish") for i in range(3)]


def market(closes):
    return {"instruments": [{"symbol": "MNQ", "bars": [{"c": c} for c in closes]}]}


def run(webull=(), yahoo=(), events=(), closes=None):
    return build_news_analyst_desk(
        yahoo_items=list(yahoo), webull_items=list(webull), calendar_events=list(events),
        market_snapshot=market(closes) if closes else None, now=NOW,
    )


def mnq(result):
    return next(item for item in result["instruments"] if item["instrument"] == "MNQ")


def test_no_items_is_insufficient_data():
    assert mnq(run())["bias"] == "INSUFFICIENT DATA"


def test_bearish_news_and_falling_price_agree():
    d = mnq(run(webull=bearish_three(), closes=DOWN))
    assert d["stance"] == "bearish"
    assert d["price"]["relation"] == "agrees"
    assert d["bias"] == "SHORT BIAS"


def test_bearish_news_against_rising_price_waits():
    d = mnq(run(webull=bearish_three(), closes=UP))
    assert d["price"]["relation"] == "diverges"
    assert d["bias"] == "WAIT"


def test_block_overrides_direction():
    events = [{"name": "CPI", "time_utc": (NOW + timedelta(minutes=5)).isoformat(), "importance": "high"}]
    d = mnq(run(webull=bearish_three(), closes=DOWN, events=events))
    assert d["event_gate"] == "block"
    assert d["bias"] == "NO NEW ENTRIES"


def test_fomc_press_conference_warns_before_release():
    events = [{"name": "FOMC Press Conference", "time_utc": (NOW + timedelta(minutes=20)).isoformat(),
               "importance": "fomc_presser"}]
    d = mnq(run(events=events))
    assert d["event_gate"] == "warn"
    assert d["event_window"] is True


def test_recent_event_asks_for_closed_bar_confirmation():
    events = [{"name": "CPI", "time_utc": (NOW - timedelta(minutes=10)).isoformat(), "importance": "high"}]
    d = mnq(run(webull=bearish_three(), closes=DOWN, events=events))
    assert d["event_gate"] == "confirm"
    assert "closed-bar confirmation" in d["recommendation"]


def test_unconfirmed_events_never_appear_as_catalysts():
    events = [{"name": "CPI", "time_utc": None, "importance": "high"}]
    assert run(events=events)["catalysts"] == []


def test_events_beyond_72_hours_are_excluded():
    events = [{"name": "Far", "time_utc": (NOW + timedelta(hours=100)).isoformat(), "importance": "high"}]
    assert run(events=events)["catalysts"] == []


def test_catalysts_sorted_by_urgency():
    events = [
        {"name": "Later", "time_utc": (NOW + timedelta(hours=30)).isoformat(), "importance": "high"},
        {"name": "Soon", "time_utc": (NOW + timedelta(minutes=5)).isoformat(), "importance": "high"},
    ]
    assert [c["name"] for c in run(events=events)["catalysts"]] == ["Soon", "Later"]


def test_endpoint_returns_three_instruments():
    from fastapi.testclient import TestClient

    from mission_control.main import app

    body = TestClient(app).get("/api/agents/news-analyst").json()
    assert [i["instrument"] for i in body["instruments"]] == ["MNQ", "MES", "MGC"]
    assert body["trade_authority"] == "none"

def test_directional_bias_without_price_is_wait():
    assert mnq(run(webull=bearish_three()))["bias"] == "WAIT"
