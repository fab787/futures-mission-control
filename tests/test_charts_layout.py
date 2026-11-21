from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "mission_control" / "dashboard.html").read_text(encoding="utf-8")


def _block():
    start = HTML.index('<script id="fmc-charts-layout">')
    end = HTML.index('</script>', start)
    return HTML[start:end]


def test_charts_page_is_wider():
    assert "#charts{max-width:1640px}" in HTML


def test_chart_is_larger_than_analyst():
    assert "minmax(0,1.6fr) minmax(380px,1fr)" in HTML
    assert ".fmc-charts-main>.panel{min-height:860px" in HTML


def test_analyst_is_taller_but_fits_content():
    assert ".fmc-charts-side>#fmc-agent-charts{min-height:620px" in HTML
    assert "height:100%" not in HTML.split('<style id="fmc-charts-layout">')[1].split("</style>")[0]


def test_controls_wrap_on_fewer_lines():
    assert "#charts .panel-head{display:flex;flex-wrap:wrap" in HTML


def test_stacks_below_1100px():
    assert "@media(max-width:1100px){.fmc-charts-row{grid-template-columns:minmax(0,1fr)}" in HTML


def test_layout_script_still_moves_agent_to_side():
    block = _block()
    assert 'sideBox.appendChild(agent)' in block
    assert "fetch(" not in block
    assert all(ord(c) < 128 for c in block)


def test_injector_markers_intact():
    assert '<button data-tab="risk">Risk & Approvals</button>' in HTML
    assert '<section class="section" id="help">' in HTML