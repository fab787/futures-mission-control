from datetime import datetime, timezone

from mission_control.agent_desk import _news, build_agent_desk

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def fused(stance, score, clusters=3, confidence="medium", sources=("webull",)):
    return {"score": score, "stance": stance, "confidence": confidence, "clusters": clusters,
            "coverage": {"sources": list(sources), "missing": [], "ratio": 0.5}}


def test_fused_bearish_is_bearish():
    assert _news({"fused": fused("bearish", -30)})["stance"] == "bearish"


def test_fused_conflict_is_conflict():
    assert _news({"fused": fused("conflict", 0)})["stance"] == "conflict"


def test_fused_neutral_thin_evidence_is_neutral():
    assert _news({"fused": fused("neutral", -33, clusters=1, confidence="low")})["stance"] == "neutral"


def test_no_clusters_is_no_data():
    assert _news({"fused": fused("neutral", 0.0, clusters=0)})["stance"] == "no_data"


def test_falls_back_to_combined_when_no_fused_block():
    inst = {"combined_bias": {"stance": "bearish", "confidence": "low", "sources": ["webull"], "status": "single_source"}}
    assert _news(inst)["stance"] == "bearish"


def test_agent_row_uses_fused_news_for_mnq():
    intel = {"instruments": [{"symbol": "MNQ", "fused": fused("bearish", -30)}]}
    rows = build_agent_desk(intel, {}, now=NOW)["instruments"]
    mnq = next(r for r in rows if r["symbol"] == "MNQ")
    assert mnq["specialists"]["news"]["stance"] == "bearish"
    assert mnq["manager"]["signal"] == "WAIT"