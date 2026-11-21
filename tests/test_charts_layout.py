from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "mission_control" / "dashboard.html").read_text(encoding="utf-8")


def _block():
    start = HTML.index('<script id="fmc-charts-layout">')
    end = HTML.index('</script>', start)
    return HTML[start:end]


def test_charts_width_is_limited():
    assert "#charts{max-width:1180px}" in HTML


def test_status_note_styled_as_footer():
    assert "#charts .fmc-foot{" in HTML


def test_agent_moves_below_chart_and_status_to_bottom():
    block = _block()
    assert 'document.getElementById("fmc-agent-charts")' in block
    assert "tail.push(agent)" in block and "tail.push(target)" in block
    assert "tail.forEach(function (x) { sec.appendChild(x); })" in block


def test_script_is_ascii_and_has_no_network_calls():
    block = _block()
    assert all(ord(c) < 128 for c in block)
    assert "fetch(" not in block


def test_injector_markers_intact():
    assert '<button data-tab="risk">Risk & Approvals</button>' in HTML
    assert '<section class="section" id="help">' in HTML