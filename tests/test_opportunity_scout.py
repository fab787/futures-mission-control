from mission_control.opportunity_scout import scout_opportunities


def base_report(symbol, *, direction="upward", relative_volume=1.8):
    return {
        "symbol": symbol,
        "instrument_name": symbol,
        "status": "ready",
        "freshness": {"status": "fresh", "age_seconds": 30},
        "market_structure": {
            "direction_20_bars": direction,
            "last_close": 100.0,
            "atr_14": 1.0,
            "relative_volume": relative_volume,
        },
    }


def structure_report(symbol, *, alignment="bullish_alignment", swing="higher_highs_higher_lows"):
    return {
        "symbol": symbol,
        "status": "ready",
        "market_structure": {
            "ema_alignment": alignment,
            "confirmed_swing_structure": swing,
            "nearest_support": {"price": 99.5, "distance_points": 0.5},
            "nearest_resistance": {"price": 101.0, "distance_points": -1.0},
        },
    }


def test_opportunity_scout_ranks_confluence_without_creating_trades():
    reports = {
        "MNQ": base_report("MNQ"),
        "MES": base_report("MES", direction="downward", relative_volume=0.9),
        "MGC": base_report("MGC", direction="sideways", relative_volume=1.1),
    }
    structure = {
        "instruments": [
            structure_report("MNQ"),
            structure_report("MES", alignment="bearish_alignment", swing="lower_highs_lower_lows"),
            structure_report("MGC", alignment="neutral", swing="mixed_or_range"),
        ]
    }
    result = scout_opportunities(reports, structure, {"status": "ready", "summary": {"high_priority": 1}})
    assert result["status"] == "ready"
    assert result["ranked_instruments"][0]["symbol"] == "MNQ"
    assert result["ranked_instruments"][0]["context_score"] == 80
    assert result["ranked_instruments"][0]["score_is_probability"] is False
    assert result["trade_proposals_created"] == 0
    assert result["provider_requests_triggered"] == 0
    assert result["trade_authority"] == "none"
    assert result["catalyst_context"]["directional_bias"] == "not_assessed"


def test_opportunity_scout_blocks_instruments_with_unready_inputs():
    reports = {"MNQ": base_report("MNQ"), "MES": {**base_report("MES"), "status": "blocked"}}
    structure = {"instruments": [structure_report("MNQ"), {"symbol": "MES", "status": "blocked"}]}
    result = scout_opportunities(reports, structure)
    assert result["status"] == "partial"
    assert result["ranked_instruments"][0]["symbol"] == "MNQ"
    assert result["ranked_instruments"][1]["status"] == "blocked"
    assert result["ranked_instruments"][1]["context_score"] is None


def test_opportunity_scout_blocks_when_structure_is_missing():
    result = scout_opportunities({"MNQ": base_report("MNQ")}, None)
    assert result["status"] == "blocked"
    assert result["ranked_instruments"] == []
    assert result["provider_requests_triggered"] == 0


def test_opportunity_scout_handles_no_ready_instruments():
    result = scout_opportunities({"MNQ": {**base_report("MNQ"), "status": "blocked"}}, {"instruments": []})
    assert result["status"] == "blocked"
    assert result["summary"]["blocked"] == 1


def test_opportunity_scout_marks_stale_news_as_context_only():
    result = scout_opportunities(
        {"MNQ": base_report("MNQ")},
        {"instruments": [structure_report("MNQ")]},
        {"status": "stale_context", "summary": {"high_priority": 1}},
    )
    assert "stale" in result["catalyst_context"]["note"]
    assert result["catalyst_context"]["directional_bias"] == "not_assessed"
