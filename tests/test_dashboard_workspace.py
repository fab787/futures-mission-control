import re
from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "mission_control" / "dashboard.html").read_text(encoding="utf-8")


def test_health_tab_is_last_in_nav():
    nav = re.findall(r'<nav class="nav"[^>]*>(.*?)</nav>', HTML, re.S)[0]
    tabs = re.findall(r'data-tab="([^"]+)"', nav)
    assert tabs[-2:] == ["help", "health"]


def test_health_section_and_root_exist():
    assert 'id="health"' in HTML
    assert 'id="fmc-health-root"' in HTML


def test_injector_markers_still_present():
    assert '<button data-tab="risk">Risk & Approvals</button>' in HTML
    assert '<section class="section" id="help">' in HTML


def _block():
    start = HTML.index('<script id="fmc-workspace-script">')
    end = HTML.index('</script>', start)
    return HTML[start:end]


def test_workspace_script_has_no_polling_timers():
    assert "setInterval" not in _block()


def test_workspace_uses_cache_endpoints_and_new_york_time():
    block = _block()
    for url in ["/api/agents/desk", "/api/agents/news-analyst", "/api/market/status"]:
        assert url in block
    assert "America/New_York" in block


def test_workspace_block_is_ascii_only():
    assert all(ord(c) < 128 for c in _block())