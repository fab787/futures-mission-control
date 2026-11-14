from __future__ import annotations

import asyncio

from mission_control.market_data_manager import MarketDataRequestManager


def snapshot(close: int = 100) -> dict:
    return {
        "source": "test",
        "provider_requests": 3,
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
        assert second["cache"]["provider_requests_per_snapshot"] == 3
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


def test_quota_error_activates_cooldown_and_serves_stale_data() -> None:
    from mission_control.provider_errors import (
        ProviderCooldownActive,
        TradingViewQuotaExceeded,
    )

    now = [10.0]
    manager = MarketDataRequestManager(
        ttl_seconds=5, cooldown_seconds=900, clock=lambda: now[0]
    )
    calls = 0

    async def good_loader(_interval: str) -> dict:
        return snapshot(456)

    async def quota_loader(_interval: str) -> dict:
        nonlocal calls
        calls += 1
        raise TradingViewQuotaExceeded("quota exceeded")

    async def run() -> None:
        await manager.get_snapshot("1m", good_loader)
        now[0] += 6
        stale = await manager.get_snapshot("1m", quota_loader)
        assert stale["cache"]["status"] == "stale"
        assert stale["cache"]["cooldown_seconds"] == 900
        now[0] += 1
        still_stale = await manager.get_snapshot("1m", quota_loader)
        assert still_stale["cache"]["status"] == "stale"
        assert calls == 1
        assert manager.quota_events == 1

        empty_manager = MarketDataRequestManager(
            cooldown_seconds=900, clock=lambda: now[0]
        )
        with_error = False
        try:
            await empty_manager.get_snapshot("1m", quota_loader)
        except ProviderCooldownActive:
            with_error = True
        assert with_error

    asyncio.run(run())


def test_different_timeframes_share_one_upstream_gate() -> None:
    manager = MarketDataRequestManager()
    active_loaders = 0
    peak_active_loaders = 0
    calls = 0

    async def loader(_interval: str) -> dict:
        nonlocal active_loaders, peak_active_loaders, calls
        calls += 1
        active_loaders += 1
        peak_active_loaders = max(peak_active_loaders, active_loaders)
        await asyncio.sleep(0.02)
        active_loaders -= 1
        return snapshot()

    async def run() -> None:
        await asyncio.gather(
            manager.get_snapshot("1m", loader),
            manager.get_snapshot("5m", loader),
            manager.get_snapshot("15m", loader),
        )

    asyncio.run(run())
    assert calls == 3
    assert peak_active_loaders == 1
    assert manager.upstream_refreshes == 3


def test_quota_detector_recognizes_transport_429_and_payloads() -> None:
    from mission_control.provider_errors import is_quota_error

    class Http429Error(RuntimeError):
        status_code = 429

    class Response:
        status_code = 429

    class WrappedHttpError(RuntimeError):
        response = Response()

    assert is_quota_error(Http429Error("upstream rejected request"))
    assert is_quota_error(WrappedHttpError("request failed"))
    assert is_quota_error({"error": "Too Many Requests"})
    assert is_quota_error("HTTP 429 from upstream")
    assert not is_quota_error("symbol not found")


def test_peek_snapshot_only_reads_existing_cache() -> None:
    manager = MarketDataRequestManager()
    calls = 0

    async def loader(_interval: str) -> dict:
        nonlocal calls
        calls += 1
        return snapshot(789)

    assert manager.peek_snapshot("1m") is None

    async def run() -> None:
        await manager.get_snapshot("1m", loader)

    asyncio.run(run())
    cached = manager.peek_snapshot("1m")
    assert cached is not None
    assert cached["cache"]["status"] == "hit"
    assert calls == 1

