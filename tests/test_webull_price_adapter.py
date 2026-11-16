from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from mission_control.analysis_agents import analyze_instrument
from mission_control.webull_price_adapter import convert_webull_futures_bars

NOW = datetime(2026, 11, 16, 4, 0, tzinfo=timezone.utc)


def _stamp(minutes_before_now: int) -> str:
    moment = NOW - timedelta(minutes=minutes_before_now)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def _candle(minutes_before_now: int, price: float, volume: float = 1000.0) -> dict:
    # Webull field names: open, high, low, close, time, volume (as strings).
    return {
        "time": _stamp(minutes_before_now),
        "open": f"{price + 2:.2f}",
        "high": f"{price + 5:.2f}",
        "low": f"{price - 5:.2f}",
        "close": f"{price:.2f}",
        "volume": f"{volume:.0f}",
    }


def _payload(candles: list[dict], symbol: str = "MNQZ6", delay: int = 0) -> list[dict]:
    # Webull returns newest-first, as the first candle here is the most recent.
    return [{"symbol": symbol, "result": candles, "delay_minutes": delay}]


def _newest_first(count: int, base: float = 27800.0) -> list[dict]:
    # minute 1 is the most recent, minute count is the oldest.
    return [_candle(minute, base + minute) for minute in range(1, count + 1)]


def test_reverses_newest_first_candles_to_oldest_first():
    result = convert_webull_futures_bars(_payload(_newest_first(5)), "MNQ")

    assert result["status"] == "ready"
    stamps = [bar["t"] for bar in result["bars"]]
    assert stamps == sorted(stamps)
    assert result["order"] == "oldest_first"


def test_renames_fields_to_analysis_format():
    result = convert_webull_futures_bars(_payload(_newest_first(3)), "MNQ")

    for bar in result["bars"]:
        assert set(bar) == {"t", "o", "h", "l", "c", "v"}
        assert isinstance(bar["o"], float)


def test_parses_webull_timestamp_with_plus0000_offset_to_utc():
    result = convert_webull_futures_bars(_payload(_newest_first(3)), "MNQ")

    first = result["bars"][0]
    assert first["t"] == (NOW - timedelta(minutes=3)).isoformat()
    parsed = datetime.fromisoformat(first["t"])
    assert parsed.utcoffset() == timedelta(0)


def test_matches_only_the_requested_root_contract():
    mes_only = _payload(_newest_first(5), symbol="MESZ6")

    result = convert_webull_futures_bars(mes_only, "MNQ")

    assert result["status"] == "unavailable"
    assert result["contract"] is None
    assert result["bars"] == []


def test_selects_mnq_contract_when_mes_is_also_present():
    payload = _payload(_newest_first(5), symbol="MESZ6", delay=0) + _payload(
        _newest_first(5, base=27800.0), symbol="MNQZ6", delay=0
    )

    result = convert_webull_futures_bars(payload, "MNQ")

    assert result["status"] == "ready"
    assert result["contract"] == "MNQZ6"


def test_reports_delay_minutes_from_webull_response():
    result = convert_webull_futures_bars(_payload(_newest_first(3), delay=0), "MNQ")

    assert result["delay_minutes"] == 0.0


def test_drops_invalid_candles_and_duplicates():
    good = _newest_first(4)
    bad_range = {**_candle(10, 27800.0), "high": "27700.00", "low": "27900.00"}
    bad_number = {**_candle(11, 27800.0), "close": "not-a-number"}
    missing_time = {**_candle(12, 27800.0), "time": ""}
    duplicate = dict(good[0])
    candles = good + [bad_range, bad_number, missing_time, duplicate]

    result = convert_webull_futures_bars(_payload(candles), "MNQ")

    assert result["status"] == "ready"
    assert result["dropped_candles"] == 3
    stamps = [bar["t"] for bar in result["bars"]]
    assert len(stamps) == len(set(stamps)) == 4


def test_no_candles_is_unavailable():
    result = convert_webull_futures_bars(_payload([]), "MNQ")

    assert result["status"] == "unavailable"
    assert result["bars"] == []


def test_missing_result_list_is_unavailable():
    payload = [{"symbol": "MNQZ6", "delay_minutes": 0}]

    result = convert_webull_futures_bars(payload, "MNQ")

    assert result["status"] == "unavailable"


def test_rejects_unsupported_root():
    with pytest.raises(ValueError):
        convert_webull_futures_bars(_payload(_newest_first(3)), "ES")


def test_does_not_mutate_input_payload():
    payload = _payload(_newest_first(5))
    before = copy.deepcopy(payload)

    convert_webull_futures_bars(payload, "MNQ")

    assert payload == before


def test_output_feeds_existing_instrument_analysis_when_fresh():
    # 30 one-minute candles are the minimum the analysis agent needs.
    result = convert_webull_futures_bars(_payload(_newest_first(30)), "MNQ")
    snapshot = {
        "interval": "1",
        "source": "webull",
        "cache": {"status": "fresh"},
        "instruments": [
            {"symbol": "MNQ", "bars": result["bars"], "source": "Webull Cloud MCP"}
        ],
    }
    config = {"name": "Micro E-mini Nasdaq-100", "analysis_profile": "mnq"}

    report = analyze_instrument("MNQ", config, snapshot, now=NOW)

    assert report["status"] == "ready"
    assert report["freshness"]["status"] == "fresh"
    assert report["trade_authority"] == "none"


def test_stale_webull_bars_are_blocked_by_existing_freshness_rule():
    stale = convert_webull_futures_bars(_payload(_newest_first(30)), "MNQ")
    snapshot = {
        "interval": "1",
        "source": "webull",
        "cache": {"status": "fresh"},
        "instruments": [
            {"symbol": "MNQ", "bars": stale["bars"], "source": "Webull Cloud MCP"}
        ],
    }
    config = {"name": "Micro E-mini Nasdaq-100", "analysis_profile": "mnq"}

    report = analyze_instrument("MNQ", config, snapshot, now=NOW + timedelta(hours=2))

    assert report["status"] == "blocked"
    assert report["freshness"]["status"] == "stale"
