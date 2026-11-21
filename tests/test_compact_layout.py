from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "mission_control" / "dashboard.html").read_text(encoding="utf-8")


def _block(tag_id):
    start = HTML.index('<' + tag_id)
    end = HTML.index('</' + tag_id.split(" ")[0].replace("<", "") + '>', start)
    return HTML[start:end]


def test_compact_css_and_script_present():
    assert 'id="fmc-compact-css"' in HTML
    assert 'id="fmc-compact-script"' in HTML


def test_header_is_compacted():
    assert "header.top{padding:8px 12px!important" in HTML
    assert "width:36px!important;height:36px!important" in HTML


def test_page_titles_are_smaller():
    assert ".page-intro-title,.fmc-intro-title{font-size:14px!important" in HTML


def test_compact_block_is_ascii_and_has_no_polling():
    start = HTML.index('<script id="fmc-compact-script">')
    end = HTML.index('</script>', start)
    block = HTML[start:end]
    assert all(ord(c) < 128 for c in block)
    assert "setInterval" not in block
    assert "fmc-agent-charts" in block


def test_injector_markers_still_present():
    assert '<button data-tab="risk">Risk & Approvals</button>' in HTML
    assert '<section class="section" id="help">' in HTML