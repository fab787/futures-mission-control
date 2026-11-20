from mission_control.news_analyst_desk import choose_market_snapshot, price_check


def snap(symbol, n):
    return {"instruments": [{"symbol": symbol, "bars": [{"c": 100.0 + i} for i in range(n)]}]}


def test_primary_used_when_it_has_bars():
    primary = snap("MNQ", 30)
    assert choose_market_snapshot(primary, snap("MNQ", 30)) is primary


def test_fallback_used_when_primary_has_no_bars():
    fallback = snap("MNQ", 30)
    assert choose_market_snapshot(None, fallback) is fallback
    assert choose_market_snapshot({"instruments": []}, fallback) is fallback
    assert choose_market_snapshot({"instruments": [{"symbol": "MNQ", "bars": []}]}, fallback) is fallback


def test_price_check_reads_snapshot_bars():
    direction, detail = price_check(snap("MNQ", 30), "MNQ")
    assert direction == "up"
    assert "one-minute" in detail


def test_price_check_still_reports_short_history():
    direction, detail = price_check(snap("MNQ", 10), "MNQ")
    assert direction is None
    assert "Not enough" in detail