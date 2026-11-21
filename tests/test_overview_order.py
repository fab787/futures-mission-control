from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "mission_control" / "dashboard.html").read_text(encoding="utf-8")


def _block():
    start = HTML.index('<script id="fmc-overview-order">')
    end = HTML.index('</script>', start)
    return HTML[start:end]


def test_order_script_present_and_ascii():
    block = _block()
    assert all(ord(c) < 128 for c in block)


def test_order_is_watchlist_then_positions_then_gex_then_news():
    block = _block()
    assert "[intro, watchHead, cards, picker, pos, selGrid, gex, brief, news]" in block


def test_positions_panel_is_placeholder_only():
    block = _block()
    assert "Positions & profit / loss" in block
    assert "NOT CONNECTED" in block
    assert "Appears after an account is connected" in block


def test_order_script_has_no_polling():
    assert "setInterval" not in _block()


def test_injector_markers_still_present():
    assert '<button data-tab="risk">Risk & Approvals</button>' in HTML
    assert '<section class="section" id="help">' in HTML