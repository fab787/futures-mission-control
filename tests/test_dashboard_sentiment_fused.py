from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "mission_control" / "dashboard.html").read_text(encoding="utf-8")


def test_client_side_scorer_is_removed():
    assert "function score(sources)" not in HTML
    assert "function overallCard(scores)" not in HTML
    assert "WEIGHT[s.key]" not in HTML


def test_sentiment_desk_reads_server_fused_values():
    assert "inst.fused" in HTML
    assert "fused_overall" in HTML