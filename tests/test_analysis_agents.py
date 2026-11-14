from datetime import datetime, timezone

from mission_control.analysis_agents import analyze_instrument, analyze_instruments


NOW = datetime(2026, 11, 14, 18, 0, tzinfo=timezone.utc)


def make_snapshot(symbols=("MNQ", "MES", "MGC"), *, age_seconds=60, cache_status="hit"):
    timestamp = (NOW.timestamp() - age_seconds)
    bars = []
    for index in range(25):
        close = 20000 + index * 2
        bars.append({
            "t": timestamp - (24 - index) * 60,
            "o": close - 1,
            "h": close + 3,
            "l": close - 2,
            "c": close,
            "v": 100 + index,
        })
    return {
        "source": "TradingView MCP",
        "interval": "1m",
        "cache": {"status": cache_status},
        "instruments": [
            {"symbol": symbol, "source": "TradingView MCP", "bars": bars}
            for symbol in symbols
        ],
    }


CONFIG = {
    "name": "Micro E-mini Nasdaq-100",
    "analysis_profile": "mnq",
}


def test_instrument_agent_analyzes_cached_bars_without_provider_calls():
    report = analyze_instrument("MNQ", CONFIG, make_snapshot(), now=NOW)
    assert report["status"] == "ready"
    assert report["symbol"] == "MNQ"
    assert report["market_structure"]["direction_20_bars"] == "upward"
    assert report["market_structure"]["last_close"] > 0
    assert report["provider_requests_triggered"] == 0
    assert report["trade_authority"] == "none"
    assert report["risk_authority"] == "none"


def test_instrument_agent_blocks_missing_snapshot():
    report = analyze_instrument("MNQ", CONFIG, None, now=NOW)
    assert report["status"] == "blocked"
    assert "No cached" in report["reason"]


def test_instrument_agent_blocks_missing_timestamp():
    snapshot = make_snapshot()
    for bar in snapshot["instruments"][0]["bars"]:
        bar.pop("t")
    report = analyze_instrument("MNQ", CONFIG, snapshot, now=NOW)
    assert report["status"] == "blocked"
    assert "timestamp" in report["reason"]


def test_instrument_agent_blocks_stale_market_data():
    report = analyze_instrument(
        "MNQ", CONFIG, make_snapshot(age_seconds=1200), now=NOW
    )
    assert report["status"] == "blocked"
    assert report["freshness"]["status"] == "stale"


def test_instrument_agent_blocks_stale_cache_even_if_bar_is_recent():
    report = analyze_instrument(
        "MNQ", CONFIG, make_snapshot(cache_status="stale"), now=NOW
    )
    assert report["status"] == "blocked"
    assert "marked this snapshot stale" in report["reason"]


def test_instrument_agent_requires_minimum_valid_history():
    snapshot = make_snapshot()
    snapshot["instruments"][0]["bars"] = snapshot["instruments"][0]["bars"][:10]
    report = analyze_instrument("MNQ", CONFIG, snapshot, now=NOW)
    assert report["status"] == "blocked"
    assert "10 valid bars" in report["reason"]


def test_instrument_agents_cover_configured_watchlist_without_refreshing():
    configs = {
        "MNQ": CONFIG,
        "MES": {"name": "Micro E-mini S&P 500", "analysis_profile": "mes"},
        "MGC": {"name": "Micro Gold", "analysis_profile": "mgc"},
    }
    result = analyze_instruments(configs, make_snapshot(), now=NOW)
    assert result["status"] == "ready"
    assert [item["symbol"] for item in result["agents"]] == ["MNQ", "MES", "MGC"]
    assert result["summary"]["provider_requests_triggered"] == 0
    assert result["summary"]["trade_proposals_created"] == 0
    assert result["safety"]["order_execution"] == "disabled"
