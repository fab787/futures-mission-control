from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "mission_control" / "dashboard.html").read_text(encoding="utf-8")


def _block():
    start = HTML.index('<script id="fmc-chart-trim">')
    end = HTML.index('</script>', start)
    return HTML[start:end]


def test_trim_script_present_and_ascii():
    block = _block()
    assert 'id="fmc-chart-trim"' in HTML
    assert all(ord(c) < 128 for c in block)


def test_removes_blue_chart_tag_and_keeps_large_title():
    assert "keepLargest(leaves(chart" in _block()


def test_removes_bottom_tradingview_link_and_note():
    block = _block()
    assert "/^Launch .*TradingView/i" in block
    assert "/^Local chart uses historical/i" in block


def test_removes_large_analyst_title_and_keeps_blue_tag():
    block = _block()
    assert "keepSmallest(leaves(agent" in block
    assert '"MARKET STRUCTURE ANALYST"' in block


def test_script_makes_no_requests_and_no_timers_on_its_own():
    block = _block()
    assert "fetch(" not in block
    assert "setInterval" not in block


def test_injector_markers_intact():
    assert '<button data-tab="risk">Risk & Approvals</button>' in HTML
    assert '<section class="section" id="help">' in HTML