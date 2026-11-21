from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "mission_control" / "dashboard.html").read_text(encoding="utf-8")


def _block():
    start = HTML.index('<script id="fmc-price-rows">')
    end = HTML.index('</script>', start)
    return HTML[start:end]


def test_price_rows_script_present():
    assert 'id="fmc-price-rows"' in HTML


def test_fetches_cache_endpoint_only_and_no_timers():
    block = _block()
    assert '"/api/agents/price-checks"' in block
    assert "setInterval" not in block
    assert all(ord(c) < 128 for c in block)


def test_rows_go_after_technical_and_five_minute():
    block = _block()
    assert "tech.nextSibling" in block
    assert "five.nextSibling" in block


def test_injector_markers_intact():
    assert '<button data-tab="risk">Risk & Approvals</button>' in HTML
    assert '<section class="section" id="help">' in HTML