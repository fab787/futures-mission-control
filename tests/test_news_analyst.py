from datetime import datetime, timedelta, timezone

import pytest

from mission_control.news_analyst import (
    analyze_instrument,
    bucket_of,
    catalyst_state,
    headline_stance,
    is_duplicate,
    is_relevant,
)

NOW = datetime(2026, 11, 20, 2, 0, tzinfo=timezone.utc)


def item(title, source="webull", label=None, confidence="medium", minutes_ago=10, url=None, summary=""):
    return {
        "title": title, "summary": summary, "source": source, "label": label,
        "confidence": confidence, "url": url,
        "published_at": NOW - timedelta(minutes=minutes_ago),
    }


def test_single_medium_webull_bearish_stays_within_band():
    result = analyze_instrument("MNQ", [item("Fed signals hawkish stance", label="bearish")], [], NOW)
    assert -25 < result["score"] < -15


def test_single_low_yahoo_item_is_small():
    result = analyze_instrument("MNQ", [item("Fed officials want to hike", source="yahoo", confidence="low")], [], NOW)
    assert -10 < result["score"] < -5


def test_twenty_strong_agreeing_items_approach_but_do_not_reach_minus_100():
    items = [item(f"Fed hawkish update {i}", label="bearish", confidence="high") for i in range(20)]
    result = analyze_instrument("MNQ", items, [], NOW)
    assert -90 < result["score"] < -85


def test_score_never_reaches_plus_or_minus_100():
    items = [item(f"Fed hawkish update {i}", label="bearish", confidence="high") for i in range(200)]
    assert -100 < analyze_instrument("MNQ", items, [], NOW)["score"] < 0


def test_neutral_items_dampen_the_score():
    alone = analyze_instrument("MNQ", [item("Fed hawkish move", label="bearish")], [], NOW)["score"]
    diluted = analyze_instrument(
        "MNQ",
        [item("Fed hawkish move", label="bearish")] + [item(f"Fed holds steady {i}", source="yahoo") for i in range(10)],
        [], NOW,
    )["score"]
    assert abs(diluted) < abs(alone)


def test_weak_bearish_against_neutral_is_not_a_conflict():
    items = [item(f"Fed hawkish {i}", label="bearish") for i in range(3)]
    items.append(item("Fed holds steady", source="yahoo", label="neutral"))
    result = analyze_instrument("MNQ", items, [], NOW)
    assert result["stance"] == "bearish"


def test_equal_strong_bull_and_bear_is_a_conflict():
    items = [item(f"Fed bullish {i}", label="bullish") for i in range(3)]
    items += [item(f"Fed hawkish {i}", label="bearish") for i in range(3)]
    assert analyze_instrument("MNQ", items, [], NOW)["stance"] == "conflict"


def test_single_company_mover_is_dropped():
    title = "VivoSim Labs (VIVS) Stock Jumps 21% After Hours: What's Driving the Move"
    assert is_relevant(title) is False
    result = analyze_instrument("MNQ", [item(title)], [], NOW)
    assert result["counts"]["dropped_irrelevant"] == 1


def test_fed_hawkish_is_bearish_for_all_three_futures():
    title = "Some Fed Officials Wanted to Hike in October"
    for instrument in ("MNQ", "MES", "MGC"):
        assert headline_stance({"title": title}, instrument) == -1


def test_gold_drop_is_bearish_for_gold_only():
    title = "Gold drops below key support"
    assert headline_stance({"title": title}, "MGC") == -1
    assert headline_stance({"title": title}, "MNQ") == 0


def test_same_url_is_counted_once():
    a = item("Fed hawkish move", url="https://x/1")
    b = item("Different wording about the Fed", url="https://x/1")
    assert is_duplicate(a, b) is True
    assert analyze_instrument("MNQ", [a, b], [], NOW)["counts"]["dropped_duplicate"] == 1


def test_future_item_is_excluded_for_point_in_time():
    future = item("Fed hawkish move", minutes_ago=-30)
    assert analyze_instrument("MNQ", [future], [], NOW)["counts"]["dropped_future"] == 1


def test_naive_timestamp_is_excluded():
    bad = {"title": "Fed hawkish move", "source": "webull", "published_at": datetime(2026, 11, 20, 1, 50)}
    assert analyze_instrument("MNQ", [bad], [], NOW)["counts"]["dropped_no_time"] == 1


def test_catalyst_states_by_minutes_to_event():
    assert catalyst_state(-20) == "ACTIVE"
    assert catalyst_state(10) == "ACTIVE"
    assert catalyst_state(40) == "IMMINENT"
    assert catalyst_state(120) == "UPCOMING"
    assert catalyst_state(-40) == "RECENT"
    assert catalyst_state(300) == "BACKGROUND"


def test_high_importance_event_soon_sets_event_window_and_warns():
    fomc = {"name": "FOMC decision", "time": NOW + timedelta(minutes=10), "importance": "high"}
    result = analyze_instrument("MNQ", [item("Fed hawkish move", label="bearish")], [fomc], NOW)
    assert result["state"] == "event_window"
    assert result["action"] == "warn"


def test_two_recent_directional_items_set_elevated():
    items = [item("Fed hawkish move", label="bearish"), item("Fed hike talk", label="bearish")]
    assert analyze_instrument("MNQ", items, [], NOW)["state"] == "elevated"


def test_buckets_recent_and_background():
    assert bucket_of(NOW - timedelta(hours=3), NOW, True) == "last_6h"
    assert bucket_of(NOW - timedelta(hours=60), NOW, False) == "background"


def test_same_input_gives_same_output():
    items = [item("Fed hawkish move", label="bearish")]
    assert analyze_instrument("MNQ", items, [], NOW) == analyze_instrument("MNQ", items, [], NOW)
