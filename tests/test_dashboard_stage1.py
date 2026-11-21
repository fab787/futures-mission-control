from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "mission_control" / "dashboard.html").read_text(encoding="utf-8")


def test_no_garbled_characters_remain():
    for bad in ["\u00e2\u2014", "\u00c2\u00b7", "\u00e2\u2020", "\u00e2\u20ac"]:
        assert bad not in HTML


def test_status_and_link_text_are_correct():
    assert "\u25cf READ-ONLY" in HTML
    assert "\u2197" in HTML


def test_brand_still_present():
    assert 'class="fmc-logo"' in HTML
    assert 'class="fmc-word">QUANTUM</span>' in HTML