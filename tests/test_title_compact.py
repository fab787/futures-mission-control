from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "mission_control" / "dashboard.html").read_text(encoding="utf-8")


def _script():
    start = HTML.index('<script id="fmc-title-compact">')
    end = HTML.index('</script>', start)
    return HTML[start:end]


def test_title_compact_present():
    assert 'id="fmc-title-compact"' in HTML
    assert ".fmc-pop{" in HTML


def test_script_covers_every_panel_header():
    block = _script()
    assert '".panel-head, .page-intro, .fmc-intro"' in block


def test_script_is_ascii_and_has_no_polling():
    block = _script()
    assert all(ord(c) < 128 for c in block)
    assert "setInterval" not in block


def test_script_keeps_titles_and_moves_detail_to_tooltip():
    block = _script()
    assert 'title.setAttribute("title", info)' in block
    assert '".eyebrow,.fmc-small,.page-intro-desc,.fmc-intro-desc"' in block


def test_injector_markers_still_present():
    assert '<button data-tab="risk">Risk & Approvals</button>' in HTML
    assert '<section class="section" id="help">' in HTML