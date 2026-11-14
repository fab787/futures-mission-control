from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from mission_control.gex import GexService, calculate_gex
from mission_control.main import app

ET = ZoneInfo("America/New_York")


def options_payload() -> dict:
    expiry = (datetime.now(ET) + timedelta(days=30)).strftime("%y%m%d")
    options = []
    for strike, call_oi, put_oi in (
        (19800, 700, 1200),
        (20000, 1800, 1600),
        (20200, 1300, 600),
    ):
        options.append(
            {
                "option": f"NDX{expiry}C{strike * 1000:08d}",
                "open_interest": call_oi,
                "iv": 0.22,
            }
        )
        options.append(
            {
                "option": f"NDX{expiry}P{strike * 1000:08d}",
                "open_interest": put_oi,
                "iv": 0.24,
            }
        )
    return {"data": {"current_price": 20000, "options": options}}


def test_calculate_gex_maps_ndx_levels_to_futures() -> None:
    result = calculate_gex(
        options_payload(),
        21000,
        price_source="manual input",
        cache_status="fresh",
        cache_age_seconds=0,
        retrieved_at="2026-11-14T10:00:00-05:00",
    )
    assert result["source"] == "Cboe delayed options"
    assert result["futures_price"] == 21000
    assert result["underlying_spot"] == 20000
    assert result["futures_spot_scale"] == 1.05
    assert result["call_wall_futures"] == result["call_wall_ndx"] * 1.05
    assert result["put_wall_futures"] == result["put_wall_ndx"] * 1.05
    assert result["contracts_used"] == 6
    assert result["all_strikes"]
    assert "dealer-positioning assumption" in result["assumptions"][0]


def test_gex_service_caches_chain_and_recalculates_for_new_futures_price() -> None:
    now = [100.0]
    calls = []

    def fetcher(symbol: str) -> dict:
        calls.append(symbol)
        return options_payload()

    service = GexService(fetcher=fetcher, clock=lambda: now[0])

    async def run() -> None:
        first = await service.get_levels(21000, price_source="manual input")
        second = await service.get_levels(21100, price_source="manual input")
        assert first["status"] == "fresh"
        assert second["status"] == "cached"
        assert second["futures_price"] == 21100
        assert second["futures_spot_scale"] == 1.055
        assert service.fetch_count == 1

    asyncio.run(run())
    assert calls == ["_NDX"]


def test_gex_service_serves_stale_chain_after_fetch_failure() -> None:
    now = [100.0]
    calls = 0

    def fetcher(_symbol: str) -> dict:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise OSError("simulated Cboe outage")
        return options_payload()

    service = GexService(
        fetcher=fetcher,
        clock=lambda: now[0],
        ttl_seconds=5,
        error_cooldown_seconds=300,
    )

    async def run() -> None:
        await service.get_levels(21000, price_source="manual input")
        now[0] += 6
        stale = await service.get_levels(21100, price_source="manual input")
        assert stale["status"] == "stale"
        assert stale["futures_price"] == 21100
        assert stale["cache_age_seconds"] == 6
        assert service.fetch_count == 2

    asyncio.run(run())


def test_gex_endpoint_uses_manual_price_without_tradingview_request(monkeypatch) -> None:
    from mission_control import main

    calls = []

    async def fake_get_levels(price: float, *, price_source: str) -> dict:
        calls.append((price, price_source))
        return {"futures_price": price, "futures_price_source": price_source}

    monkeypatch.setattr(main.gex_service, "get_levels", fake_get_levels)
    response = TestClient(app).get("/api/gex?futures_price=21000")
    assert response.status_code == 200
    assert response.json()["futures_price"] == 21000
    assert calls == [(21000.0, "manual input")]


def test_gex_endpoint_reuses_fresh_cached_mnq_price(monkeypatch) -> None:
    from mission_control import main

    calls = []

    async def fake_get_levels(price: float, *, price_source: str) -> dict:
        calls.append((price, price_source))
        return {"futures_price": price, "futures_price_source": price_source}

    monkeypatch.setattr(main.gex_service, "get_levels", fake_get_levels)
    monkeypatch.setattr(
        main.market_data_manager,
        "peek_snapshot",
        lambda interval: {
            "cache": {"status": "hit", "age_seconds": 12},
            "instruments": [{"symbol": "MNQ", "latest": {"c": 20987.5}}],
        },
    )
    response = TestClient(app).get("/api/gex")
    assert response.status_code == 200
    assert response.json()["futures_price"] == 20987.5
    assert calls[0][0] == 20987.5
    assert calls[0][1].startswith("cached MNQ TradingView snapshot")


def test_gex_endpoint_requires_manual_price_when_mnq_cache_is_stale(monkeypatch) -> None:
    from mission_control import main

    monkeypatch.setattr(
        main.market_data_manager,
        "peek_snapshot",
        lambda _interval: {
            "cache": {"status": "stale", "age_seconds": 700},
            "instruments": [{"symbol": "MNQ", "latest": {"c": 20987.5}}],
        },
    )
    response = TestClient(app).get("/api/gex")
    assert response.status_code == 400
    assert "will not trigger a TradingView request" in response.json()["detail"]
