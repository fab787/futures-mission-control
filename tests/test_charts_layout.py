from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "mission_control" / "dashboard.html").read_text(encoding="utf-8")


def _block():
    start = HTML.index('<script id="fmc-charts-layout">')
    end = HTML.index('</script>', start)
    return HTML[start:end]


def test_chart_and_agent_sit_side_by_side():
    assert "grid-template-columns:minmax(0,1fr) minmax(300px,360px)" in HTML
    block = _block()
    assert '"fmc-charts-side"' in block
    assert 'sideBox.appendChild(agent)' in block


def test_duplicate_tradingview_link_removed_below_chart():
    block = _block()
    assert "links.slice(1)" in block
    assert '/Launch/i.test(t) && /TradingView/i.test(t)' in block


def test_status_note_moves_to_bottom():
    block = _block()
    assert 'sec.appendChild(foot)' in block
    assert "fmc-foot" in HTML


def test_script_is_ascii_and_has_no_network_calls():
    block = _block()
    assert all(ord(c) < 128 for c in block)
    assert "fetch(" not in block


def test_injector_markers_intact():
    assert '<button data-tab="risk">Risk & Approvals</button>' in HTML
    assert '<section class="section" id="help">' in HTML