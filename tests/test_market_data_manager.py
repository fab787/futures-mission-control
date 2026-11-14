from __future__ import annotations

import asyncio

from mission_control.market_data_manager import MarketDataRequestManager


def snapshot(close: int = 100) -> dict:
    return {
        "source": "test",
        "instruments": [
            {"symbol": "MNQ", "bars": [{"c": close}]},
            {"symbol": "MES", "bars": [{"c": close}]},
        ],
    }


def test_snapshot_is_cached_for_ttl() -> None:
    now = [10.0]
    manager = MarketDataRequestManager(ttl_seconds=300, clock=lambda: now[0])
    calls = 0

    async def loader(_interval: str) -> dict:
        nonlocal calls
        calls += 1
        return snapshot()

    async def run() -> None:
        first = await manager.get_snapshot("1m", loader)
        now[0] += 30
        second = await manager.get_snapshot("1m", loader)
        assert first["cache"]["status"] == "miss"
        assert second["cache"]["status"] == "hit"
        assert second["instruments"][0]["bars"][0]["c"] == 100

    asyncio.run(run())
    assert calls == 1
    assert manager.upstream_refreshes == 1
    assert manager.cache_hits == 1


def test_expired_snapshot_refreshes_once() -> None:
    now = [10.0]
    manager = MarketDataRequestManager(ttl_seconds=5, clock=lambda: now[0])
    calls = 0

    async def loader(_interval: str) -> dict:
        nonlocal calls
        calls += 1
        return snapshot(100 + calls)

    async def run() -> None:
        first = await manager.get_snapshot("5m", loader)
        now[0] += 6
        second = await manager.get_snapshot("5m", loader)
        assert first["instruments"][0]["bars"][0]["c"] == 101
        assert second["instruments"][0]["bars"][0]["c"] == 102
        assert second["cache"]["status"] == "miss"

    asyncio.run(run())
    assert calls == 2


def test_concurrent_requests_are_coalesced() -> None:
    manager = MarketDataRequestManager()
    calls = 0

    async def loader(_interval: str) -> dict:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return snapshot()

    async def run() -> None:
        results = await asyncio.gather(
            manager.get_snapshot("15m", loader),
            manager.get_snapshot("15m", loader),
            manager.get_snapshot("15m", loader),
        )
        assert all(item["instruments"][0]["bars"][0]["c"] == 100 for item in results)

    asyncio.run(run())
    assert calls == 1
    assert manager.upstream_refreshes == 1
    assert manager.coalesced_requests >= 1


def test_failed_refresh_serves_last_good_data_as_stale() -> None:
    now = [10.0]
    manager = MarketDataRequestManager(ttl_seconds=5, clock=lambda: now[0])

    async def good_loader(_interval: str) -> dict:
        return snapshot(123)

    async def bad_loader(_interval: str) -> dict:
        raise RuntimeError("provider unavailable")

    async def run() -> None:
        await manager.get_snapshot("1m", good_loader)
        now[0] += 6
        stale = await manager.get_snapshot("1m", bad_loader)
        assert stale["cache"]["status"] == "stale"
        assert stale["instruments"][0]["bars"][0]["c"] == 123

    asyncio.run(run())
