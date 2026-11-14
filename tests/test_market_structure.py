from mission_control.market_structure import analyze_market_structure


def make_bars(count=80):
    bars = []
    for index in range(count):
        close = 100 + index * 0.2
        bars.append({
            "o": close - 0.1,
            "h": close + (1.0 if index % 7 == 3 else 0.3),
            "l": close - (1.0 if index % 9 == 4 else 0.3),
            "c": close,
            "t": 1794680000 + index * 60,
        })
    return bars


def make_inputs():
    bars = make_bars()
    snapshot = {
        "interval": "1m",
        "instruments": [{"symbol": symbol, "bars": bars} for symbol in ("MNQ", "MES", "MGC")],
    }
    reports = {
        symbol: {"symbol": symbol, "status": "ready"}
        for symbol in ("MNQ", "MES", "MGC")
    }
    return reports, snapshot


def test_market_structure_analyzes_all_ready_instruments_without_provider_calls():
    reports, snapshot = make_inputs()
    result = analyze_market_structure(reports, snapshot)
    assert result["status"] == "ready"
    assert [item["symbol"] for item in result["instruments"]] == ["MNQ", "MES", "MGC"]
    for item in result["instruments"]:
        assert item["status"] == "ready"
        structure = item["market_structure"]
        assert structure["ema_20"] is not None
        assert structure["ema_50"] is not None
        assert structure["bars_analyzed"] == 80
    assert result["provider_requests_triggered"] == 0
    assert result["trade_authority"] == "none"


def test_market_structure_blocks_when_base_instrument_report_is_blocked():
    reports, snapshot = make_inputs()
    reports["MNQ"]["status"] = "blocked"
    result = analyze_market_structure(reports, snapshot)
    mnq = next(item for item in result["instruments"] if item["symbol"] == "MNQ")
    assert result["status"] == "partial"
    assert mnq["status"] == "blocked"
    assert mnq["market_structure"] is None


def test_market_structure_blocks_insufficient_history():
    reports, snapshot = make_inputs()
    snapshot["instruments"][0]["bars"] = make_bars(30)
    result = analyze_market_structure(reports, snapshot)
    mnq = next(item for item in result["instruments"] if item["symbol"] == "MNQ")
    assert mnq["status"] == "blocked"
    assert "50 valid bars" in mnq["reason"]


def test_market_structure_blocks_missing_snapshot():
    result = analyze_market_structure({}, None)
    assert result["status"] == "blocked"
    assert result["provider_requests_triggered"] == 0
