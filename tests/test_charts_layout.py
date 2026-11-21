from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "mission_control" / "dashboard.html").read_text(encoding="utf-8")


def _block():
    start = HTML.index('<script id="fmc-charts-layout">')
    end = HTML.index('</script>', start)
    return HTML[start:end]


def _style():
    start = HTML.index('<style id="fmc-charts-layout">')
    end = HTML.index('</style>', start)
    return HTML[start:end]


def test_charts_page_width_set():
    assert "#charts{max-width:1640px}" in _style()


def test_no_fixed_heights_leave_empty_space():
    style = _style()
    assert "min-height:860px" not in style
    assert "min-height:620px" not in style
    assert "height:100%" not in style


def test_panels_size_to_content_and_align_top():
    assert "align-items:start" in _style()


def test_chart_and_analyst_side_by_side_above_1100px():
    style = _style()
    assert "grid-template-columns:minmax(0,1.6fr) minmax(380px,1fr)" in style
    assert "@media(max-width:1100px){.fmc-charts-row{grid-template-columns:minmax(0,1fr)}}" in style


def test_layout_script_still_moves_agent_to_side():
    block = _block()
    assert "sideBox.appendChild(agent)" in block
    assert "fetch(" not in block
    assert all(ord(c) < 128 for c in block)


def test_injector_markers_intact():
    assert '<button data-tab="risk">Risk & Approvals</button>' in HTML
    assert '<section class="section" id="help">' in HTML