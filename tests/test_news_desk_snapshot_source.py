from mission_control.news_analyst_desk import choose_market_snapshot, price_check


def snap(symbol, closes):
    return {"instruments": [{"symbol": symbol, "bars": [{"c": c} for c in closes]}]}


def test_primary_used_when_it_has_bars():
    primary = snap("MNQ", [100.0] * 130)
    assert choose_market_snapshot(primary, snap("MNQ", [100.0] * 130)) is primary


def test_fallback_used_when_primary_has_no_bars():
    fallback = snap("MNQ", [100.0] * 130)
    assert choose_market_snapshot(None, fallback) is fallback
    assert choose_market_snapshot({"instruments": []}, fallback) is fallback


def test_strong_drift_is_up():
    closes = [100.0 + 0.5 * i for i in range(130)]
    direction, detail = price_check(snap("MNQ", closes), "MNQ")
    assert direction == "up"
    assert "five-minute" in detail


def test_pure_noise_is_flat():
    closes = [100.0 + (0.01 if i % 2 else -0.01) for i in range(130)]
    direction, _ = price_check(snap("MNQ", closes), "MNQ")
    assert direction == "flat"


def test_short_history_reports_not_enough():
    direction, detail = price_check(snap("MNQ", [100.0 + i for i in range(60)]), "MNQ")
    assert direction is None
    assert "Not enough" in detail