from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "mission_control" / "dashboard.html").read_text(encoding="utf-8")


def test_mount_no_longer_removes_news_analyst_box():
    assert '["fmc-desk5", "fmc-cal5", "fmc-news-analyst-info"]' not in HTML
    assert '["fmc-desk5", "fmc-cal5"]' in HTML


def test_news_analyst_is_placed_before_agent_desk():
    assert "agents.insertBefore(info, agents.firstElementChild)" in HTML
    assert 'info.insertAdjacentElement("afterend", agentDesk)' in HTML