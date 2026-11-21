from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "mission_control" / "dashboard.html").read_text(encoding="utf-8")


def test_header_is_sticky():
    assert 'id="fmc-sticky"' in HTML
    assert "header.top{position:sticky!important;top:0;" in HTML


def test_nav_offsets_below_header():
    assert "nav.nav{top:var(--fmc-head-h,120px)!important}" in HTML
    assert 'setProperty("--fmc-head-h"' in HTML


def test_header_and_nav_kept():
    assert '<header class="top">' in HTML
    assert '<nav class="nav"' in HTML
    assert 'class="fmc-logo"' in HTML


def test_injector_markers_untouched():
    assert '<button data-tab="risk">Risk & Approvals</button>' in HTML
    assert '<section class="section" id="help">' in HTML