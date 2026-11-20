from datetime import datetime, timedelta, timezone

from mission_control.sentiment_fusion import decide, fuse

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def ev(source, title, stance, conf=1.0, hours_ago=0.0):
    return {"source": source, "title": title, "stance": stance, "confidence": conf,
            "published": (NOW - timedelta(hours=hours_ago)).isoformat()}


def test_same_story_from_two_sources_counts_once():
    items = [ev("webull", "Fed hawkish", -1), ev("yahoo", "Fed hawkish", -1),
             ev("webull", "Dow falls", -1), ev("webull", "Gold drops", -1)]
    result = fuse(items, NOW)
    assert result["clusters"] == 3


def test_single_headline_cannot_show_direction():
    result = fuse([ev("webull", "Fed hawkish", -1)], NOW)
    assert result["stance"] == "neutral"
    assert result["clusters"] == 1


def test_three_fresh_bearish_stories_are_bearish():
    items = [ev("webull", f"story {i}", -1) for i in range(3)]
    result = fuse(items, NOW)
    assert result["stance"] == "bearish"
    assert result["score"] <= -15


def test_old_stories_decay_toward_zero():
    fresh = fuse([ev("webull", f"s{i}", -1) for i in range(3)], NOW)
    old = fuse([ev("webull", f"s{i}", -1, hours_ago=24) for i in range(3)], NOW)
    assert abs(old["score"]) < abs(fresh["score"])
    assert abs(old["score"]) < 10


def test_missing_source_lowers_coverage_not_score_to_zero():
    items = [ev("webull", f"s{i}", -1) for i in range(3)]
    result = fuse(items, NOW)
    assert result["coverage"]["ratio"] == 0.5
    assert result["coverage"]["missing"] == ["yahoo"]
    assert result["stance"] == "bearish"


def test_opposing_evidence_is_conflict():
    items = [ev("webull", f"bull{i}", 1) for i in range(3)]
    items += [ev("news", f"bear{i}", -1) for i in range(3)]
    result = fuse(items, NOW)
    assert result["stance"] == "conflict"


def test_naive_timestamp_is_dropped():
    bad = {"source": "webull", "title": "x", "stance": -1, "confidence": 1,
           "published": "2026-11-20T11:00:00"}
    result = fuse([bad], NOW)
    assert result["dropped"]["no_time"] == 1
    assert result["clusters"] == 0
    assert result["score"] == 0.0


def test_empty_evidence_is_neutral():
    result = fuse([], NOW)
    assert result["score"] == 0.0
    assert result["stance"] == "neutral"
    assert result["confidence"] == "low"


def test_decide_block_overrides_everything():
    fused = {"stance": "bearish"}
    assert decide(fused, "agrees", block="CPI")[0] == "NO NEW ENTRIES"


def test_decide_requires_price_confirmation():
    fused = {"stance": "bearish"}
    assert decide(fused, None)[0] == "WAIT"
    assert decide(fused, "diverges")[0] == "WAIT"
    assert decide(fused, "agrees")[0] == "SHORT BIAS"


def test_decide_neutral_and_conflict():
    assert decide({"stance": "neutral"}, "agrees")[0] == "NEUTRAL"
    assert decide({"stance": "conflict"}, "agrees")[0] == "WAIT"