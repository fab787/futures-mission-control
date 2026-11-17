"""Webull option chain for GEX (Stage B): paged contracts, batched snapshots, keyed cache.

Only this module calls Webull. call_tool is injected, so tests use mocks.
Contract paging returns every expiry, so the expiry filter is applied here.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime
from typing import Any, Awaitable, Callable

from mission_control.gex import GexUnavailable, calculate_gex
from mission_control.gex_sources import (
    ET,
    FIELD_ALIASES,
    OCC_RE,
    RATIO_BANDS,
    TICKER_ROOTS,
    active_session,
    build_payload,
    is_trading_day,
)
from mission_control.webull import _records, call_readonly_tool

OPTION_CATEGORY = "US_OPTION"
PAGE_SIZE = 1000
SNAPSHOT_BATCH = 20
CHAIN_TTL_SECONDS = 60.0
MAX_PAGES = 50

ToolCaller = Callable[[str, dict[str, Any]], Awaitable[Any]]


def resolve_expiry(value: str | None, now: datetime | None = None) -> date:
    """Empty means the active 0DTE session. Otherwise a trading-day YYYY-MM-DD."""
    if not value:
        return active_session(now or datetime.now(ET))
    try:
        day = date.fromisoformat(value)
    except ValueError as exc:
        raise GexUnavailable(f"Expiry must be YYYY-MM-DD, got {value!r}") from exc
    if not is_trading_day(day):
        raise GexUnavailable(f"{value} is not a trading day")
    return day


def _first(row: dict[str, Any], field: str) -> str:
    for key in FIELD_ALIASES[field]:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip().upper()
    return ""


def _instrument_id(row: dict[str, Any]) -> str | None:
    for key in ("instrument_id", "instrumentId", "id"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _root_and_expiry(symbol: str) -> tuple[str | None, date | None]:
    match = OCC_RE.match(symbol)
    if not match:
        return None, None
    root, ymd, _cp, _strike = match.groups()
    try:
        return root, datetime.strptime(ymd, "%y%m%d").date()
    except ValueError:
        return None, None


async def list_contracts(root: str, call_tool: ToolCaller) -> list[dict[str, Any]]:
    """Every contract for one root across all expiries, read until an empty page."""
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    seen: set[str] = set()
    for _ in range(MAX_PAGES):
        args: dict[str, Any] = {"category": OPTION_CATEGORY, "root_symbol": root, "page_size": PAGE_SIZE}
        if cursor is not None:
            args["last_instrument_id"] = cursor
        page = _records(await call_tool("get_option_contracts", args))
        if not page:
            return out
        out.extend(page)
        cursor = _instrument_id(page[-1])
        if cursor is None:
            raise GexUnavailable(f"Webull contract page for {root} has no instrument id to continue paging")
        if cursor in seen:
            raise GexUnavailable(f"Webull contract paging repeated a cursor for {root}")
        seen.add(cursor)
    raise GexUnavailable(f"Webull contract listing for {root} exceeded {MAX_PAGES} pages")


async def fetch_snapshots(symbols: list[str], call_tool: ToolCaller) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for start in range(0, len(symbols), SNAPSHOT_BATCH):
        batch = symbols[start:start + SNAPSHOT_BATCH]
        page = _records(await call_tool("get_option_snapshot", {"category": OPTION_CATEGORY, "symbols": ",".join(batch)}))
        for row in page:
            symbol = _first(row, "symbol")
            if symbol:
                out[symbol] = row
    return out


async def fetch_chain(ticker: str, expiry: date, call_tool: ToolCaller) -> list[dict[str, Any]]:
    """Contracts for one ticker and one expiry, merged with their snapshots."""
    if ticker not in TICKER_ROOTS:
        raise GexUnavailable(f"Unsupported GEX ticker: {ticker}")
    allowed = set(TICKER_ROOTS[ticker])
    matched: list[dict[str, Any]] = []
    for root in TICKER_ROOTS[ticker]:
        for row in await list_contracts(root, call_tool):
            symbol = _first(row, "symbol")
            sym_root, sym_expiry = _root_and_expiry(symbol)
            if sym_root in allowed and sym_expiry == expiry:
                matched.append({**row, "symbol": symbol})
    if not matched:
        raise GexUnavailable(f"Webull returned no {ticker} contracts expiring {expiry.isoformat()}")
    snapshots = await fetch_snapshots([row["symbol"] for row in matched], call_tool)
    return [{**row, **snapshots.get(row["symbol"], {})} for row in matched]


class WebullGexService:
    """Chain cache keyed by (ticker, expiry). Stale data is served only after a failed refresh."""

    def __init__(
        self,
        *,
        call_tool: ToolCaller | None = None,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = CHAIN_TTL_SECONDS,
    ) -> None:
        self._call_tool = call_tool or call_readonly_tool
        self._clock = clock
        self.ttl_seconds = ttl_seconds
        self._cache: dict[tuple[str, date], tuple[float, list[dict[str, Any]], str]] = {}
        self._lock = asyncio.Lock()
        self.fetch_count = 0

    async def _chain(self, key: tuple[str, date], force_refresh: bool) -> tuple[list[dict[str, Any]], float, str, str]:
        entry = self._cache.get(key)
        if entry and not force_refresh and self._clock() - entry[0] < self.ttl_seconds:
            return entry[1], entry[0], entry[2], "cached"
        async with self._lock:
            entry = self._cache.get(key)
            if entry and not force_refresh and self._clock() - entry[0] < self.ttl_seconds:
                return entry[1], entry[0], entry[2], "cached"
            self.fetch_count += 1
            try:
                rows = await fetch_chain(key[0], key[1], self._call_tool)
            except GexUnavailable:
                raise
            except Exception as exc:
                if entry:
                    return entry[1], entry[0], entry[2], "stale"
                raise GexUnavailable(f"Webull option request failed: {type(exc).__name__}") from exc
            retrieved_at = datetime.now(ET).isoformat(timespec="seconds")
            stored_at = self._clock()
            self._cache[key] = (stored_at, rows, retrieved_at)
            return rows, stored_at, retrieved_at, "fresh"

    async def get_levels(
        self,
        *,
        ticker: str,
        expiry: date,
        spot: float,
        futures_price: float,
        price_source: str,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        if ticker not in TICKER_ROOTS or ticker not in RATIO_BANDS:
            raise GexUnavailable(f"Unsupported GEX ticker: {ticker}")
        rows, stored_at, retrieved_at, status = await self._chain((ticker, expiry), force_refresh)
        payload = build_payload(rows, spot, ticker)
        return calculate_gex(
            payload,
            futures_price,
            price_source=price_source,
            cache_status=status,
            cache_age_seconds=max(0.0, self._clock() - stored_at),
            retrieved_at=retrieved_at,
            ratio_band=RATIO_BANDS[ticker],
        )


webull_gex_service = WebullGexService()
