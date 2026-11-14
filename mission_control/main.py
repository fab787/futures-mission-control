from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from mission_control.analysis_agents import analyze_instruments
from mission_control.gex import GexUnavailable, gex_service
from mission_control.market_data_manager import market_data_manager
from mission_control.news import YAHOO_CALENDAR_URL, yahoo_news_service
from mission_control.provider_errors import ProviderCooldownActive
from mission_control.tradingview import AuthorizationRequired, market_snapshot
from mission_control.tradingview_auth import KeyringTokenStorage

ROOT = Path(__file__).resolve().parent.parent
INSTRUMENTS_FILE = ROOT / "config" / "instruments.yaml"

app = FastAPI(
    title="FAB787 QUANTUM",
    version="0.3.0",
    description="Read-only futures intelligence dashboard with optional TradingView MCP data.",
)


def load_instruments() -> dict[str, Any]:
    """Load enabled instrument definitions from the project YAML config."""
    with INSTRUMENTS_FILE.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    instruments = raw.get("instruments", {})
    return {
        symbol: {"symbol": symbol, **settings}
        for symbol, settings in instruments.items()
        if settings.get("enabled", False)
    }


async def tradingview_authorized() -> bool:
    try:
        return await KeyringTokenStorage().get_tokens() is not None
    except Exception:
        return False


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "read-only", "execution": "disabled"}


@app.get("/api/instruments")
def instruments() -> dict[str, list[dict[str, Any]]]:
    return {"instruments": list(load_instruments().values())}


@app.get("/api/tradingview/status")
async def tradingview_status() -> dict[str, Any]:
    authorized = await tradingview_authorized()
    return {
        "id": "tradingview",
        "name": "TradingView MCP",
        "connected": authorized,
        "status": "authorized" if authorized else "authorization_required",
        "endpoint": "https://mcp.tradingview.com/mcp",
        "transport": "Streamable HTTP",
        "authentication": "OAuth 2.1",
        "mode": "read-only",
        "authorization_command": "python -m mission_control.tradingview_auth",
    }


@app.get("/api/market-data")
async def market_data(interval: str = Query(default="1m", pattern="^(1m|5m|15m|1h|4h|1D)$")) -> dict[str, Any]:
    if not await tradingview_authorized():
        raise HTTPException(
            status_code=503,
            detail={
                "status": "authorization_required",
                "message": "Authorize TradingView first: python -m mission_control.tradingview_auth",
            },
        )
    try:
        return await market_data_manager.get_snapshot(interval, market_snapshot)
    except AuthorizationRequired as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ProviderCooldownActive as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@app.get("/api/gex")
async def gex(futures_price: float | None = Query(default=None, gt=0)) -> dict[str, Any]:
    """Return cached Cboe NDX GEX estimates mapped to MNQ/NQ futures levels."""
    price_source = "manual input"
    resolved_price = futures_price

    if resolved_price is None:
        cached = market_data_manager.peek_snapshot("1m")
        cache_meta = cached.get("cache", {}) if cached else {}
        if cached is not None and cache_meta.get("status") == "hit":
            mnq = next(
                (
                    item for item in cached.get("instruments", [])
                    if item.get("symbol") == "MNQ"
                ),
                None,
            )
            latest = mnq.get("latest") if mnq else None
            close = latest.get("c") if isinstance(latest, dict) else None
            try:
                resolved_price = float(close)
            except (TypeError, ValueError):
                resolved_price = None
            if resolved_price and resolved_price > 0:
                price_source = (
                    "cached MNQ TradingView snapshot "
                    f"({cache_meta.get('age_seconds', 'unknown')}s old)"
                )

    if resolved_price is None or resolved_price <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "No fresh cached MNQ price is available. Enter the current MNQ/NQ "
                "futures price manually; this endpoint will not trigger a TradingView request."
            ),
        )

    try:
        return await gex_service.get_levels(
            resolved_price, price_source=price_source
        )
    except GexUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/integrations")
async def integrations() -> dict[str, Any]:
    """Report connection state from the local OS keyring, not Claude Desktop."""
    authorized = await tradingview_authorized()
    return {
        "integrations": [
            {
                "id": "tradingview",
                "name": "TradingView MCP",
                "role": "Read-only market data and history only",
                "status": "authorized" if authorized else "authorization_required",
                "connected": authorized,
                "capabilities": ["get_ohlcv", "get_symbol_data_batch"],
                "limitations": [
                    "This is a separate OAuth connection; Claude Desktop authorization is not reused.",
                    "Market history is polled on demand; no continuous streaming feed is promised.",
                    "TradingView symbol coverage and exchange permissions apply.",
                    "TradingView MCP is beta and rate-limited.",
                ],
            },
            {
                "id": "yahoo_finance",
                "name": "Yahoo Finance / News",
                "role": "Ticker headlines and economic-calendar page link",
                "status": "configured_unverified",
                "connected": False,
                "capabilities": ["ticker_headlines", "economic_calendar_page_link"],
                "limitations": [
                    "Ticker-specific RSS only; this adapter does not claim a complete general-market news feed.",
                    "Economic calendar is linked to Yahoo Finance; structured event extraction is not yet verified.",
                    "Yahoo data must not be treated as a guaranteed real-time futures feed.",
                ],
            },
        ]
    }


@app.get("/api/agents/instruments")
def instrument_agents() -> dict[str, Any]:
    """Analyze the existing 1-minute cache only; never trigger provider refreshes."""
    cached_snapshot = market_data_manager.peek_snapshot("1m")
    return analyze_instruments(load_instruments(), cached_snapshot)


@app.get("/api/news")
async def news() -> dict[str, Any]:
    """Return cached Yahoo Finance ticker headlines; never call TradingView."""
    return await yahoo_news_service.get_news()


@app.get("/api/calendar")
def economic_calendar() -> dict[str, Any]:
    """Link to Yahoo Finance calendar without scraping an unverified schema."""
    return {
        "status": "page_link_only",
        "source": "Yahoo Finance Economic Calendar",
        "url": YAHOO_CALENDAR_URL,
        "timezone_note": (
            "Yahoo Finance currently displays event times in UTC. "
            "FAB787 QUANTUM has not yet verified a structured Yahoo event feed "
            "for conversion to America/New_York."
        ),
        "items": [],
        "message": (
            "Open Yahoo Finance for the current economic-event schedule. "
            "No events or timestamps are synthesized by this dashboard."
        ),
    }


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return (ROOT / "mission_control" / "dashboard.html").read_text(encoding="utf-8")
