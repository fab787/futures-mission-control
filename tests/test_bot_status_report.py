from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from mission_control import bot_status_report
from mission_control.bot_status_report import build_bot_status
from mission_control.mes_bot import MES_BOT_PROFILE

NOW = datetime(2026, 11, 13, 15, 0, tzinfo=timezone.utc)
BASES = {"MNQ": 20000, "MES": 5000, "MGC": 2000}


def _bars(count: int, end: datetime, base: float) -> list[dict]:
    start = end - timedelta(minutes=count - 1)
    bars = []
    for index in range(count):
        stamp = start + timedelta(minutes=index)
        price = base + index
        bars.append(
            {
                "t": stamp.isoformat(),
                "o": price,
                "h": price + 5,
                "l": price - 5,
                "c": price + 2,
                "v": 100 + index,
            }
        )
    return bars


def _snapshot(
    *,
    symbols: tuple[str, ...] = ("MNQ", "MES", "MGC"),
    end: datetime = NOW - timedelta(seconds=60),
    cache_status: str = "fresh",
) -> dict:
    instruments = [
        {"symbol": symbol, "bars": _bars(30, end, BASES[symbol]), "source": "tradingview_cache"}
        for symbol in symbols
    ]
    return {
        "interval": "1",
        "source": "tradingview_cache",
        "cache": {"status": cache_status},
        "instruments": instruments,
    }


def _bot(result: dict, symbol: str) -> dict:
    return next(bot for bot in result["bots"] if bot["symbol"] == symbol)


def test_all_bots_ready_with_fresh_snapshot():
    result = build_bot_status(_snapshot(), now=NOW)

    assert result["overall_status"] == "ready"
    assert result["summary"] == {"total": 3, "ready": 3, "blocked": 0, "disabled": 0}
    assert [bot["symbol"] for bot in result["bots"]] == ["MNQ", "MES", "MGC"]


def test_missing_symbol_blocks_only_that_bot():
    result = build_bot_status(_snapshot(symbols=("MES", "MGC")), now=NOW)

    assert result["overall_status"] == "partial"
    assert _bot(result, "MNQ")["status"] == "blocked"
    assert "absent from the cached snapshot" in _bot(result, "MNQ")["reason"]
    assert _bot(result, "MES")["status"] == "ready"
    assert _bot(result, "MGC")["status"] == "ready"


def test_no_snapshot_blocks_every_bot():
    result = build_bot_status(None, now=NOW)

    assert result["overall_status"] == "blocked"
    assert result["summary"]["blocked"] == 3
    for bot in result["bots"]:
        assert "No cached market snapshot" in bot["reason"]


def test_stale_cache_blocks_every_bot():
    result = build_bot_status(_snapshot(cache_status="stale"), now=NOW)

    assert result["overall_status"] == "blocked"
    assert result["summary"]["ready"] == 0


def test_disabled_bot_is_reported_not_analyzed(monkeypatch):
    disabled_mes = {**MES_BOT_PROFILE, "enabled": False}
    patched = tuple(
        (symbol, runner, disabled_mes if symbol == "MES" else profile)
        for symbol, runner, profile in bot_status_report.BOTS
    )
    monkeypatch.setattr(bot_status_report, "BOTS", patched)

    result = build_bot_status(_snapshot(), now=NOW)

    mes = _bot(result, "MES")
    assert mes["status"] == "disabled"
    assert mes["enabled"] is False
    assert result["summary"]["disabled"] == 1
    assert result["overall_status"] == "partial"


def test_every_bot_and_the_report_carry_safety_flags():
    result = build_bot_status(_snapshot(), now=NOW)

    expected = {
        "mode": "read_only_status_report",
        "trade_authority": "none",
        "trade_proposals_created": 0,
        "order_execution": "disabled",
        "human_approval_required": True,
        "provider_requests_triggered": 0,
    }
    assert result["safety"] == expected
    for bot in result["bots"]:
        assert bot["safety"] == expected


def test_every_bot_reports_its_contract_verification_label():
    result = build_bot_status(_snapshot(), now=NOW)

    assert "Verified against CME/broker" in _bot(result, "MNQ")["verification"]
    assert "CME re-check pending" in _bot(result, "MES")["verification"]
    assert "CME notice SER-9247" in _bot(result, "MGC")["verification"]


def test_summary_counts_match_bot_statuses():
    result = build_bot_status(_snapshot(symbols=("MNQ", "MES")), now=NOW)

    statuses = [bot["status"] for bot in result["bots"]]
    assert result["summary"]["total"] == len(statuses)
    assert result["summary"]["ready"] == statuses.count("ready")
    assert result["summary"]["blocked"] == statuses.count("blocked")
    assert result["summary"]["disabled"] == statuses.count("disabled")


def test_does_not_mutate_snapshot():
    snapshot = _snapshot()
    before = copy.deepcopy(snapshot)

    build_bot_status(snapshot, now=NOW)

    assert snapshot == before
