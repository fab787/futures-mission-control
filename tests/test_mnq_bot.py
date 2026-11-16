from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from mission_control.mnq_bot import MNQ_BOT_PROFILE, run_mnq_bot

NOW = datetime(2026, 11, 13, 15, 0, tzinfo=timezone.utc)


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
    instruments: list[dict] | None = None,
    end: datetime = NOW - timedelta(seconds=60),
    cache_status: str = "fresh",
) -> dict:
    if instruments is None:
        instruments = [
            {"symbol": "MNQ", "bars": _bars(30, end, 20000), "source": "tradingview_cache"}
        ]
    return {
        "interval": "1",
        "source": "tradingview_cache",
        "cache": {"status": cache_status},
        "instruments": instruments,
    }


def test_fresh_mnq_snapshot_produces_ready_analysis_only_report():
    report = run_mnq_bot(_snapshot(), now=NOW)

    assert report["status"] == "ready"
    assert report["symbol"] == "MNQ"
    assert report["analysis"]["symbol"] == "MNQ"
    assert report["analysis"]["status"] == "ready"
    assert report["mode"] == "paper_analysis_only"
    assert report["trade_authority"] == "none"
    assert report["trade_proposals_created"] == 0
    assert report["order_execution"] == "disabled"
    assert report["human_approval_required"] is True
    assert report["provider_requests_triggered"] == 0


def test_disabled_profile_returns_disabled_without_analysis():
    profile = {**MNQ_BOT_PROFILE, "enabled": False}

    report = run_mnq_bot(_snapshot(), profile, now=NOW)

    assert report["status"] == "disabled"
    assert report["enabled"] is False
    assert report["analysis"] is None


def test_missing_snapshot_is_blocked():
    report = run_mnq_bot(None, now=NOW)

    assert report["status"] == "blocked"
    assert report["analysis"]["status"] == "blocked"
    assert "No cached market snapshot" in report["reason"]


def test_mnq_bot_ignores_other_instrument_data():
    # MNQ rows are absent; MES rows are present with enough bars.
    # The MNQ bot must not borrow MES history.
    snapshot = _snapshot(
        instruments=[
            {"symbol": "MES", "bars": _bars(30, NOW - timedelta(seconds=60), 5000), "source": "tradingview_cache"}
        ]
    )

    report = run_mnq_bot(snapshot, now=NOW)

    assert report["status"] == "blocked"
    assert "absent from the cached snapshot" in report["reason"]


def test_mnq_analysis_uses_only_mnq_rows_when_mixed_snapshot_is_supplied():
    mnq = {"symbol": "MNQ", "bars": _bars(30, NOW - timedelta(seconds=60), 20000), "source": "tradingview_cache"}
    mes = {"symbol": "MES", "bars": _bars(30, NOW - timedelta(seconds=60), 5000), "source": "tradingview_cache"}
    snapshot = _snapshot(instruments=[mes, mnq])

    report = run_mnq_bot(snapshot, now=NOW)

    assert report["status"] == "ready"
    # MNQ closes are around 20000; a leak from MES would show ~5000.
    assert report["analysis"]["market_structure"]["last_close"] > 19000


def test_stale_latest_bar_blocks_analysis():
    stale_end = NOW - timedelta(hours=1)

    report = run_mnq_bot(_snapshot(end=stale_end), now=NOW)

    assert report["status"] == "blocked"
    assert report["analysis"]["freshness"]["status"] == "stale"


def test_stale_cache_status_blocks_analysis():
    report = run_mnq_bot(_snapshot(cache_status="stale"), now=NOW)

    assert report["status"] == "blocked"
    assert "marked this snapshot stale" in report["reason"]


def test_rejects_non_mnq_profile():
    profile = {**MNQ_BOT_PROFILE, "symbol": "MES"}

    with pytest.raises(ValueError):
        run_mnq_bot(_snapshot(), profile, now=NOW)


def test_does_not_mutate_inputs():
    snapshot = _snapshot()
    profile = copy.deepcopy(MNQ_BOT_PROFILE)
    snapshot_before = copy.deepcopy(snapshot)
    profile_before = copy.deepcopy(profile)

    run_mnq_bot(snapshot, profile, now=NOW)

    assert snapshot == snapshot_before
    assert profile == profile_before
