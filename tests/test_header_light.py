from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "mission_control" / "dashboard.html").read_text(encoding="utf-8")


def _script():
    start = HTML.index('<script id="fmc-header-light-script">')
    end = HTML.index('</script>', start)
    return HTML[start:end]


def test_header_light_css_present():
    assert 'id="fmc-header-light"' in HTML
    assert "header.top #fmc-market-light{flex:0 0 auto!important;order:0!important;margin:0 auto!important" in HTML


def test_placement_script_moves_light_before_top_right():
    block = _script()
    assert 'h.insertBefore(l, tr)' in block
    assert 'l.nextElementSibling !== tr' in block


def test_script_is_ascii_and_has_no_polling():
    block = _script()
    assert all(ord(c) < 128 for c in block)
    assert "setInterval" not in block


def test_gate_and_injector_markers_intact():
    assert 'id="fmc-market-gate"' in HTML
    assert '<button data-tab="risk">Risk & Approvals</button>' in HTML
    assert '<section class="section" id="help">' in HTML