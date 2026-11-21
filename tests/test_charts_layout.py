from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "mission_control" / "dashboard.html").read_text(encoding="utf-8")


def _block():
    start = HTML.index('<script id="fmc-charts-layout">')
    end = HTML.index('</script>', start)
    return HTML[start:end]


def test_charts_page_is_wider():
    assert "#charts{max-width:1400px}" in HTML


def test_analyst_sits_beside_chart_at_equal_visual_weight():
    assert "grid-template-columns:minmax(0,1.35fr) minmax(360px,1fr)" in HTML
    assert "align-items:stretch" in HTML


def test_analyst_stretches_to_chart_height():
    assert ".fmc-charts-side>#fmc-agent-charts{flex:1 1 auto;height:100%" in HTML


def test_stacks_below_1100px():
    assert "@media(max-width:1100px){.fmc-charts-row{grid-template-columns:minmax(0,1fr)}}" in HTML


def test_layout_script_still_moves_agent_to_side():
    block = _block()
    assert 'sideBox.appendChild(agent)' in block
    assert "fetch(" not in block
    assert all(ord(c) < 128 for c in block)


def test_injector_markers_intact():
    assert '<button data-tab="risk">Risk & Approvals</button>' in HTML
    assert '<section class="section" id="help">' in HTML