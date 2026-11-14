"""Centralized, process-local cache and single-flight gate for market-data polling.

All dashboard consumers should call this manager instead of invoking the
TradingView adapter directly. The cache is keyed by interval and intentionally
does not expose a force-refresh path that can bypass provider cooldowns.
"""
from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Awaitable, Callable
from typing import Any

SnapshotLoader = Callable[[str], Awaitable[dict[str, Any]]]


class MarketDataRequestManager:
    """Cache market snapshots and coalesce concurrent requests per interval."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 300.0,
        error_ttl_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or error_ttl_seconds <= 0:
            raise ValueError("Cache TTL values must be positive")
        self.ttl_seconds = ttl_seconds
        self.error_ttl_seconds = error_ttl_seconds
        self._clock = clock
        self._cache: dict[str, tuple[float, dict[str, Any], float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.upstream_refreshes = 0
        self.cache_hits = 0
        self.coalesced_requests = 0

    @staticmethod
    def _has_data(snapshot: dict[str, Any]) -> bool:
        instruments = snapshot.get("instruments", [])
        return any(
            isinstance(item, dict) and bool(item.get("bars"))
            for item in instruments
        )

    def _decorate(
        self,
        snapshot: dict[str, Any],
        *,
        status: str,
        age_seconds: float,
        ttl_seconds: float,
    ) -> dict[str, Any]:
        result = copy.deepcopy(snapshot)
        result["cache"] = {
            "status": status,
            "age_seconds": round(max(0.0, age_seconds), 1),
            "ttl_seconds": ttl_seconds,
            "next_refresh_seconds": round(max(0.0, ttl_seconds - age_seconds), 1),
            "upstream_refreshes": self.upstream_refreshes,
            "cache_hits": self.cache_hits,
            "coalesced_requests": self.coalesced_requests,
        }
        return result

    async def get_snapshot(
        self,
        interval: str,
        loader: SnapshotLoader,
    ) -> dict[str, Any]:
        """Return cached data or perform one serialized upstream refresh."""
        now = self._clock()
        cached = self._cache.get(interval)
        if cached is not None:
            stored_at, snapshot, ttl = cached
            age = now - stored_at
            if age < ttl:
                self.cache_hits += 1
                return self._decorate(
                    snapshot, status="hit", age_seconds=age, ttl_seconds=ttl
                )

        lock = self._locks.setdefault(interval, asyncio.Lock())
        was_waiting = lock.locked()
        async with lock:
            if was_waiting:
                self.coalesced_requests += 1
            now = self._clock()
            cached = self._cache.get(interval)
            if cached is not None:
                stored_at, snapshot, ttl = cached
                age = now - stored_at
                if age < ttl:
                    self.cache_hits += 1
                    return self._decorate(
                        snapshot, status="coalesced" if was_waiting else "hit",
                        age_seconds=age, ttl_seconds=ttl,
                    )

            previous = cached
            self.upstream_refreshes += 1
            try:
                fresh = await loader(interval)
            except Exception:
                if previous is not None and self._has_data(previous[1]):
                    stored_at, snapshot, ttl = previous
                    return self._decorate(
                        snapshot, status="stale", age_seconds=now - stored_at,
                        ttl_seconds=ttl,
                    )
                raise

            has_data = self._has_data(fresh)
            if not has_data and previous is not None and self._has_data(previous[1]):
                stored_at, snapshot, ttl = previous
                return self._decorate(
                    snapshot, status="stale", age_seconds=self._clock() - stored_at,
                    ttl_seconds=ttl,
                )

            ttl = self.ttl_seconds if has_data else self.error_ttl_seconds
            stored_at = self._clock()
            self._cache[interval] = (stored_at, copy.deepcopy(fresh), ttl)
            return self._decorate(
                fresh, status="miss", age_seconds=0.0, ttl_seconds=ttl
            )


market_data_manager = MarketDataRequestManager()
