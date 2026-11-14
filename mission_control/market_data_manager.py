"""Centralized cache, single-flight gate, and provider cooldown for market polling.

All dashboard consumers call this manager rather than invoking the TradingView
adapter directly. A provider cooldown cannot be bypassed by manual refreshes.
"""
from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Awaitable, Callable
from typing import Any

from mission_control.provider_errors import (
    ProviderCooldownActive,
    TradingViewQuotaExceeded,
)

SnapshotLoader = Callable[[str], Awaitable[dict[str, Any]]]


class MarketDataRequestManager:
    """Cache snapshots and coalesce concurrent requests per interval."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 300.0,
        error_ttl_seconds: float = 30.0,
        cooldown_seconds: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if min(ttl_seconds, error_ttl_seconds, cooldown_seconds) <= 0:
            raise ValueError("Cache TTL and cooldown values must be positive")
        self.ttl_seconds = ttl_seconds
        self.error_ttl_seconds = error_ttl_seconds
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._cache: dict[str, tuple[float, dict[str, Any], float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._cooldown_until = 0.0
        self.upstream_refreshes = 0
        self.cache_hits = 0
        self.coalesced_requests = 0
        self.quota_events = 0

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
        now = self._clock()
        result["cache"] = {
            "status": status,
            "age_seconds": round(max(0.0, age_seconds), 1),
            "ttl_seconds": ttl_seconds,
            "next_refresh_seconds": round(max(0.0, ttl_seconds - age_seconds), 1),
            "cooldown_seconds": round(max(0.0, self._cooldown_until - now), 1),
            "upstream_refreshes": self.upstream_refreshes,
            "cache_hits": self.cache_hits,
            "coalesced_requests": self.coalesced_requests,
            "quota_events": self.quota_events,
        }
        return result

    def _serve_stale(self, cached: tuple[float, dict[str, Any], float]) -> dict[str, Any]:
        stored_at, snapshot, ttl = cached
        return self._decorate(
            snapshot, status="stale", age_seconds=self._clock() - stored_at,
            ttl_seconds=ttl,
        )

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

        if now < self._cooldown_until:
            if cached is not None and self._has_data(cached[1]):
                return self._serve_stale(cached)
            raise ProviderCooldownActive(
                f"TradingView is in cooldown for {self._cooldown_until - now:.0f}s"
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

            if now < self._cooldown_until:
                if cached is not None and self._has_data(cached[1]):
                    return self._serve_stale(cached)
                raise ProviderCooldownActive(
                    f"TradingView is in cooldown for {self._cooldown_until - now:.0f}s"
                )

            previous = cached
            self.upstream_refreshes += 1
            try:
                fresh = await loader(interval)
            except TradingViewQuotaExceeded as exc:
                self.quota_events += 1
                self._cooldown_until = self._clock() + self.cooldown_seconds
                if previous is not None and self._has_data(previous[1]):
                    return self._serve_stale(previous)
                raise ProviderCooldownActive(
                    f"TradingView quota reached; retry blocked for {self.cooldown_seconds:.0f}s"
                ) from exc
            except Exception:
                if previous is not None and self._has_data(previous[1]):
                    return self._serve_stale(previous)
                raise

            has_data = self._has_data(fresh)
            if not has_data and previous is not None and self._has_data(previous[1]):
                return self._serve_stale(previous)

            ttl = self.ttl_seconds if has_data else self.error_ttl_seconds
            stored_at = self._clock()
            self._cache[interval] = (stored_at, copy.deepcopy(fresh), ttl)
            return self._decorate(
                fresh, status="miss", age_seconds=0.0, ttl_seconds=ttl
            )


market_data_manager = MarketDataRequestManager()
