import re
from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "mission_control" / "dashboard.html").read_text(encoding="utf-8")


def _block(tag):
    start = HTML.index('<script id="' + tag + '">')
    end = HTML.index('</script>', start)
    return HTML[start:end]


def test_health_tab_removed_from_nav():
    nav = re.findall(r'<nav class="nav"[^>]*>(.*?)</nav>', HTML, re.S)[0]
    assert 'data-tab="health"' not in nav
    assert 'id="health"' not in HTML


def test_health_panel_lives_inside_help():
    assert HTML.index('id="fmc-health-root"') > HTML.index('<section class="section" id="help">')


def test_page_titles_no_longer_inserted():
    assert "return; // page titles removed" in HTML


def test_system_pill_is_created_by_script_and_is_ascii():
    block = _block("fmc-syscheck")
    assert 'pill.id = "fmc-sys";' in block
    assert "America/New_York" in block
    assert '"/api/market/status"' in block
    assert all(ord(c) < 128 for c in block)


def test_state_colors_css_and_script():
    assert "#fmc-market-light[data-state=\"live\"]" in HTML
    assert "#fmc-arm-btn.fmc-arm-on" in HTML
    block = _block("fmc-state-colors")
    assert '"live"' in block and '"halt"' in block and '"off"' in block
    assert "fmc-arm-on" in block and "fmc-arm-off" in block
    assert all(ord(c) < 128 for c in block)
    assert "setInterval" not in block


def test_safety_badge_styled():
    assert ".safety{color:#58c9e9!important" in HTML


def test_injector_markers_intact():
    assert '<button data-tab="risk">Risk & Approvals</button>' in HTML
    assert '<section class="section" id="help">' in HTML