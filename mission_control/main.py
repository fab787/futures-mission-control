from __future__ import annotations

import ipaddress
import math
import time
from datetime import UTC
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from mission_control import local_auth
from mission_control.account_history import (
    get_current_history,
    list_history_versions,
    save_history_snapshot,
)
from mission_control.account_history_csv import preview_csv
from mission_control.account_protection import assess_account_protection
from mission_control.account_registry import (
    create_account,
    list_account_events,
    list_accounts,
    update_account,
)
from mission_control.account_registry_ui import build_dashboard
from mission_control.analysis_agents import analyze_instruments
from mission_control.audit_ledger import (
    create_proposal,
    decide_proposal,
    list_audit_events,
    list_proposals,
)
from mission_control.broker_capabilities import list_broker_capabilities, upsert_broker_capability
from mission_control.broker_capabilities_ui import build_broker_capabilities_dashboard
from mission_control.chief_decision_agent import compose_chief_decision
from mission_control.gex import GexUnavailable, gex_service
from mission_control.gex_webull import resolve_expiry, webull_gex_service
from mission_control.market_data_manager import MarketDataRequestManager, market_data_manager
from mission_control.market_structure import analyze_market_structure
from mission_control.master_risk_engine import evaluate_master_risk
from mission_control.news import YAHOO_CALENDAR_URL, yahoo_news_service
from mission_control.webull_news import webull_calendar_service, webull_news_service
from mission_control.news_intelligence import build_instrument_news_intelligence
from mission_control.news_catalyst import analyze_news_catalysts
from mission_control.opportunity_scout import scout_opportunities
from mission_control.prop_firm_rules import evaluate_prop_firm_rules
from mission_control.provider_errors import ProviderCooldownActive
from mission_control.risk_config import (
    create_profile,
    get_profile,
    list_profile_versions,
    list_profiles,
)
from mission_control.sentiment_analyst import (
    compare_sentiment_assessments,
    sentiment_interface_status,
)
from mission_control.tradingview import (
    AuthorizationRequired as TradingViewAuthorizationRequired,
)
from mission_control.tradingview import (
    diagnostics_snapshot as tradingview_diagnostics_snapshot,
)
from mission_control.tradingview import (
    market_snapshot as tradingview_market_snapshot,
)
from mission_control.tradingview_auth import KeyringTokenStorage
from mission_control.tradingview_webhook import (
    claim_signal_for_review,
    set_signal_status,
)
from mission_control.tradingview_webhook import (
    get_signal as get_tradingview_signal,
)
from mission_control.tradingview_webhook import (
    list_signals as list_tradingview_webhook_signals,
)
from mission_control.webhook_inbox_ui import build_webhook_dashboard
from mission_control.webull import (
    AuthorizationRequired as WebullAuthorizationRequired,
)
from mission_control.webull import (
    WebullTokenStorage,
)
from mission_control.webull import (
    market_snapshot as webull_market_snapshot,
)

ROOT = Path(__file__).resolve().parent.parent
INSTRUMENTS_FILE = ROOT / "config" / "instruments.yaml"
# Separate provider caches prevent one provider's limits or failures from
# affecting the other provider's market-data path.
webull_market_data_manager = MarketDataRequestManager(provider_name="Webull", ttl_seconds=15.0)

app = FastAPI(
    title="FAB787 QUANTUM",
    version="0.3.0",
    description="Read-only futures intelligence dashboard with TradingView and Webull Cloud MCP data.",
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


async def webull_authorized() -> bool:
    try:
        storage = WebullTokenStorage()
        return (
            await storage.get_tokens() is not None
            and not storage.needs_reauthorization()
        )
    except Exception:
        return False



AUTH_COOKIE = "fq_session"
AUTH_EXEMPT_PATHS = {
    "/login",
    "/api/health",
    "/api/auth/status",
    "/api/auth/setup",
    "/api/auth/login",
    "/api/auth/logout",
}
_auth_failures: dict[str, list[float]] = {}


def _request_is_local(request: Request) -> bool:
    host = request.client.host if request.client else ""
    if host == "testclient":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    try:
        return urlsplit(origin).netloc == request.headers.get("host", "")
    except ValueError:
        return False


@app.middleware("http")
async def local_authentication_gate(request: Request, call_next):
    """Require both loopback access and a valid local administrator session."""
    if not _request_is_local(request):
        return JSONResponse(
            {"detail": "FAB787 QUANTUM is restricted to this computer."},
            status_code=403,
        )

    if request.url.path not in AUTH_EXEMPT_PATHS:
        username = local_auth.get_session_username(
            request.cookies.get(AUTH_COOKIE)
        )
        if username is None:
            if request.url.path.startswith("/api/"):
                return JSONResponse(
                    {"detail": "Authentication required. Sign in to continue."},
                    status_code=401,
                )
            return RedirectResponse(url="/login", status_code=303)

    return await call_next(request)


@app.get("/login", response_class=HTMLResponse)
def login_page() -> str:
    return (
        ROOT / "mission_control" / "login.html"
    ).read_text(encoding="utf-8")


@app.get("/api/auth/status")
def authentication_status(request: Request) -> dict[str, Any]:
    username = local_auth.get_session_username(
        request.cookies.get(AUTH_COOKIE)
    )
    return {
        "configured": local_auth.is_configured(),
        "authenticated": username is not None,
        "username": username,
        "setup_required": not local_auth.is_configured(),
    }


@app.post("/api/auth/setup")
async def authentication_setup(request: Request) -> JSONResponse:
    if not _same_origin(request):
        raise HTTPException(status_code=403, detail="Cross-origin setup denied.")
    try:
        payload = await request.json()
        username = str(payload.get("username", ""))
        password = str(payload.get("password", ""))
        local_auth.create_user(username, password)
        token = local_auth.create_session(username)
    except local_auth.AuthenticationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid setup request.") from exc

    response = JSONResponse({"ok": True, "username": username.strip()})
    response.set_cookie(
        AUTH_COOKIE,
        token,
        max_age=local_auth.SESSION_TTL_SECONDS,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/",
    )
    return response


@app.post("/api/auth/login")
async def authentication_login(request: Request) -> JSONResponse:
    if not _same_origin(request):
        raise HTTPException(status_code=403, detail="Cross-origin login denied.")
    if not local_auth.is_configured():
        raise HTTPException(
            status_code=409,
            detail="Complete first-run administrator setup first.",
        )

    host = request.client.host if request.client else "local"
    now = time.time()
    recent = [
        stamp for stamp in _auth_failures.get(host, [])
        if now - stamp < 600
    ]
    _auth_failures[host] = recent
    if len(recent) >= 8:
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Try again in ten minutes.",
        )

    try:
        payload = await request.json()
        username = str(payload.get("username", ""))
        password = str(payload.get("password", ""))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid login request.") from exc

    if not local_auth.verify_credentials(username, password):
        _auth_failures[host] = recent + [now]
        raise HTTPException(
            status_code=401,
            detail="Username or password is incorrect.",
        )

    _auth_failures.pop(host, None)
    token = local_auth.create_session(username.strip())
    response = JSONResponse({"ok": True, "username": username.strip()})
    response.set_cookie(
        AUTH_COOKIE,
        token,
        max_age=local_auth.SESSION_TTL_SECONDS,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/",
    )
    return response


@app.post("/api/auth/logout")
def authentication_logout(request: Request) -> JSONResponse:
    if not _same_origin(request):
        raise HTTPException(status_code=403, detail="Cross-origin logout denied.")
    response = JSONResponse({"ok": True})
    response.delete_cookie(
        AUTH_COOKIE,
        path="/",
        httponly=True,
        samesite="strict",
    )
    return response


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


@app.get("/api/tradingview/diagnostics")
def tradingview_diagnostics() -> dict[str, Any]:
    """Expose sanitized in-memory HTTP status and rate-limit headers."""
    return {
        "provider": "TradingView MCP",
        **tradingview_diagnostics_snapshot(),
    }


@app.get("/api/webull/status")
async def webull_status() -> dict[str, Any]:
    authorized = await webull_authorized()
    return {
        "id": "webull",
        "name": "Webull Cloud MCP",
        "connected": authorized,
        "status": "authorized" if authorized else "authorization_required",
        "endpoint": "https://api.webull.com/mcp",
        "transport": "Streamable HTTP",
        "authentication": "OAuth 2.1",
        "mode": "read-only",
        "scopes": ["market:read", "instrument:read"],
        "capabilities": [
            "get_futures_bars",
            "get_futures_snapshot",
            "get_futures_depth",
            "get_futures_tick",
            "get_futures_instruments",
            "get_futures_products",
            "get_futures_product_class",
        ],
        "authorization_command": "python -m mission_control.webull_auth",
    }


@app.get("/api/market-data")
async def market_data(
    interval: str = Query(default="1m", pattern="^(1m|5m|15m|1h|4h|1D)$"),
    provider: str = Query(default="webull", pattern="^(tradingview|webull)$"),
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    """Return cached market data from the selected, independently managed provider."""
    if provider == "webull":
        if not await webull_authorized():
            reauthorization = WebullTokenStorage().needs_reauthorization()
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "reauthorization_required" if reauthorization else "authorization_required",
                    "provider": "webull",
                    "message": (
                        "Webull rejected the saved OAuth token. Reauthorize with: "
                        "python -m mission_control.webull_auth"
                        if reauthorization
                        else "Authorize Webull: python -m mission_control.webull_auth"
                    ),
                },
            )
        try:
            return await webull_market_data_manager.get_snapshot(
                interval, webull_market_snapshot, force_refresh=refresh
            )
        except WebullAuthorizationRequired as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ProviderCooldownActive as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc

    if not await tradingview_authorized():
        raise HTTPException(
            status_code=503,
            detail={
                "status": "authorization_required",
                "provider": "tradingview",
                "message": "Authorize TradingView first: python -m mission_control.tradingview_auth",
            },
        )
    try:
        return await market_data_manager.get_snapshot(
            interval, tradingview_market_snapshot, force_refresh=refresh
        )
    except TradingViewAuthorizationRequired as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ProviderCooldownActive as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


def _gex_provider_manager(provider: str) -> MarketDataRequestManager:
    if provider == "tradingview":
        return market_data_manager
    if provider == "webull":
        return webull_market_data_manager
    raise HTTPException(status_code=400, detail="Provider must be tradingview or webull")


def _cached_gex_mnq_price(provider: str) -> tuple[float | None, str]:
    """Read only the selected provider's fresh MNQ 1-minute cache."""
    manager = _gex_provider_manager(provider)
    cached = manager.peek_snapshot("1m")
    cache_meta = (cached.get("cache") or {}) if cached else {}
    if cached is None or cache_meta.get("status") != "hit":
        return None, ""
    mnq = next(
        (item for item in cached.get("instruments", []) if item.get("symbol") == "MNQ"),
        None,
    )
    latest = mnq.get("latest") if isinstance(mnq, dict) else None
    raw = latest.get("c") if isinstance(latest, dict) else None
    try:
        price = float(raw)
    except (TypeError, ValueError):
        price = 0.0
    if not math.isfinite(price) or price <= 0:
        return None, ""
    source = f"cached MNQ {'TradingView' if provider == 'tradingview' else 'Webull'} snapshot ({cache_meta.get('age_seconds', 'unknown')}s old)"
    return price, source


def _attach_cached_realized_volatility(result: dict[str, Any], provider: str) -> dict[str, Any]:
    """Calculate annualized RV only from a fresh, already-cached daily MNQ series."""
    manager = _gex_provider_manager(provider)
    for interval in ("1d", "D", "1D"):
        snapshot = manager.peek_snapshot(interval)
        cache_meta = (snapshot.get("cache") or {}) if snapshot else {}
        if snapshot is None or cache_meta.get("status") != "hit":
            continue
        mnq = next(
            (item for item in snapshot.get("instruments", []) if item.get("symbol") == "MNQ"),
            None,
        )
        bars = mnq.get("bars", []) if isinstance(mnq, dict) else []
        closes: list[float] = []
        for bar in bars if isinstance(bars, list) else []:
            if not isinstance(bar, dict):
                continue
            try:
                close = float(bar.get("c"))
            except (TypeError, ValueError):
                continue
            if math.isfinite(close) and close > 0:
                closes.append(close)
        if len(closes) < 21:
            continue
        returns = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
        if len(returns) < 20:
            continue
        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
        annualized = math.sqrt(max(0.0, variance) * 252.0) * 100.0
        result["realized_volatility"] = {
            "status": "ready",
            "annualized_pct": round(annualized, 2),
            "source": f"{provider.upper()} cached MNQ daily bars",
            "observations": len(returns),
            "interval": interval,
            "cache_age_seconds": cache_meta.get("age_seconds"),
            "method": "Sample standard deviation of daily close-to-close returns × sqrt(252)",
        }
        return result
    result["realized_volatility"] = {
        "status": "unavailable",
        "annualized_pct": None,
        "source": None,
        "observations": 0,
        "reason": f"No fresh cached daily MNQ history with at least 21 closes is available from {provider.upper()}; no extra market-data request was made.",
    }
    return result


@app.get("/api/gex")
async def gex(
    futures_price: float | None = Query(default=None, gt=0),
    provider: str = Query(default="webull", pattern="^(tradingview|webull)$"),
    ticker: str = Query(default="NDX", pattern="^(NDX|QQQ|SPX|SPY|GLD)$"),
    expiry: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    source: str = Query(default="cboe", pattern="^(cboe|webull)$"),
    spot: float | None = Query(default=None, gt=0),
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    """GEX for the selected ticker. The Cboe NDX path stays the default until Stage C."""
    price_source = "manual input"
    resolved_price = futures_price

    if resolved_price is None:
        resolved_price, price_source = _cached_gex_mnq_price(provider)

    if resolved_price is None or not math.isfinite(resolved_price) or resolved_price <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No fresh cached MNQ price is available from {'TradingView' if provider == 'tradingview' else 'Webull'}. "
                "Select an authorized provider with a fresh MNQ 1-minute cache or enter "
                "the current MNQ/NQ price manually. GEX will not trigger a TradingView request."
            ),
        )

    if source == "cboe":
        if ticker != "NDX":
            raise HTTPException(status_code=400, detail="The Cboe source supports NDX only. Use source=webull for QQQ.")
        try:
            result = await gex_service.get_levels(resolved_price, price_source=price_source)
            return _attach_cached_realized_volatility(result, provider)
        except GexUnavailable as exc:
            message = str(exc)
            status_code = 400 if "proxy ratio" in message.lower() else 503
            raise HTTPException(status_code=status_code, detail=message) from exc

    if ticker not in ("NDX", "QQQ"):
        raise HTTPException(status_code=400, detail=f"{ticker} is not mapped yet. Stage C adds SPX, SPY and GLD.")
    if spot is None:
        raise HTTPException(
            status_code=400,
            detail="Webull GEX needs the calculation spot (spot=...). Stage C reads it automatically.",
        )
    try:
        target = resolve_expiry(expiry)
    except GexUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return await webull_gex_service.get_levels(
            ticker=ticker,
            expiry=target,
            spot=spot,
            futures_price=resolved_price,
            price_source=price_source,
            force_refresh=refresh,
        )
    except GexUnavailable as exc:
        message = str(exc)
        status_code = 400 if "proxy ratio" in message.lower() else 503
        raise HTTPException(status_code=status_code, detail=message) from exc


@app.get("/api/gex/snapshot")
def gex_snapshot(
    provider: str = Query(default="webull", pattern="^(tradingview|webull)$"),
) -> dict[str, Any]:
    """Cache-only Home GEX snapshot; never fetch Cboe or market data."""
    price, price_source = _cached_gex_mnq_price(provider)
    if price is None:
        return {
            "status": "not_loaded",
            "source": "Cboe delayed NDX options",
            "message": f"No fresh cached MNQ price is available from {provider.upper()}; Home will not request market data.",
        }
    try:
        result = gex_service.peek_levels(price, price_source=price_source)
        if result is None:
            return {
                "status": "not_loaded",
                "source": "Cboe delayed NDX options",
                "message": "No cached options chain is available. Open GEX and request an update.",
            }
        return _attach_cached_realized_volatility(result, provider)
    except GexUnavailable as exc:
        return {"status": "unavailable", "source": "Cboe delayed NDX options", "message": str(exc)}


@app.get("/api/integrations")
async def integrations() -> dict[str, Any]:
    """Report connection state from the local OS keyring, not Claude Desktop."""
    authorized = await tradingview_authorized()
    webull_connected = await webull_authorized()
    webull_reauthorization = WebullTokenStorage().needs_reauthorization()
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
                "id": "webull",
                "name": "Webull Cloud MCP",
                "role": "Read-only futures market data and contract information",
                "status": (
                    "reauthorization_required"
                    if webull_reauthorization
                    else "authorized" if webull_connected else "authorization_required"
                ),
                "connected": webull_connected,
                "recovery_command": (
                    "python -m mission_control.webull_auth"
                    if webull_reauthorization or not webull_connected
                    else None
                ),
                "capabilities": [
                    "get_futures_bars",
                    "get_futures_snapshot",
                    "get_futures_depth",
                    "get_futures_tick",
                    "get_futures_instruments",
                    "get_futures_products",
                    "get_futures_product_class",
                ],
                "limitations": [
                    "Uses a separate local Webull OAuth authorization; Claude Desktop credentials are not reused.",
                    "Market-data delays and depth depend on Webull account subscriptions.",
                    "This integration does not enable account, order, instruction, or watchlist operations.",
                    "Market history is polled on demand; continuous streaming is not claimed.",
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


@app.get("/api/agents/market-structure")
def market_structure_agent() -> dict[str, Any]:
    """Run technical structure analysis using only the existing 1-minute cache."""
    cached_snapshot = market_data_manager.peek_snapshot("1m")
    base_reports = analyze_instruments(load_instruments(), cached_snapshot)
    ready_reports = {
        report["symbol"]: report
        for report in base_reports["agents"]
    }
    return analyze_market_structure(ready_reports, cached_snapshot)


@app.get("/api/news")
async def news() -> dict[str, Any]:
    """Return cached Yahoo Finance ticker headlines; never call TradingView."""
    return await yahoo_news_service.get_news()



@app.get("/api/news/webull")
async def webull_news(refresh: bool = Query(default=False)) -> dict[str, Any]:
    """Return independently cached Webull stock-news headlines for futures proxies."""
    if refresh:
        return await webull_news_service.get_news(force_refresh=True)
    return await webull_news_service.get_news()


@app.get("/api/news/intelligence")
def news_intelligence() -> dict[str, Any]:
    """Build combined per-instrument sentiment from existing provider caches only."""
    return build_instrument_news_intelligence(
        yahoo_news_service.peek_news(),
        webull_news_service.peek_news(),
    )


@app.get("/api/calendar/events")
async def macro_calendar_events() -> dict[str, Any]:
    """Return Webull's structured US macro calendar with bounded pagination."""
    try:
        return await webull_calendar_service.get_calendar()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



@app.get("/api/agents/opportunity-scout")
async def opportunity_scout_agent(
    provider: str = Query(default="webull", pattern="^(tradingview|webull)$"),
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    """Rank the selected provider's data; refresh only through its shared cache gate."""
    manager = webull_market_data_manager if provider == "webull" else market_data_manager
    loader = webull_market_snapshot if provider == "webull" else tradingview_market_snapshot
    authorized = await webull_authorized() if provider == "webull" else await tradingview_authorized()
    before_refreshes = manager.upstream_refreshes
    snapshot = None
    market_status = "cache_only"
    market_error = None

    if refresh and not authorized:
        snapshot = manager.peek_snapshot("1m")
        market_status = (
            "reauthorization_required"
            if provider == "webull" and WebullTokenStorage().needs_reauthorization()
            else "authorization_required"
        )
    elif refresh:
        try:
            # Refresh only when the selected provider's cache is missing or stale.
            # The manager still enforces single-flight, TTL and provider cooldowns.
            snapshot = await manager.get_snapshot("1m", loader, force_refresh=False)
            market_status = (snapshot.get("cache") or {}).get("status", "available")
        except ProviderCooldownActive as exc:
            snapshot = manager.peek_snapshot("1m")
            market_status = "cooldown"
            market_error = str(exc)
        except (WebullAuthorizationRequired, TradingViewAuthorizationRequired) as exc:
            snapshot = manager.peek_snapshot("1m")
            market_status = "reauthorization_required" if provider == "webull" else "authorization_required"
            market_error = str(exc)
        except Exception as exc:
            snapshot = manager.peek_snapshot("1m")
            market_status = "error"
            market_error = f"{type(exc).__name__}: {exc}"
    else:
        snapshot = manager.peek_snapshot("1m")
        market_status = (snapshot or {}).get("cache", {}).get("status", "not_loaded")

    base_payload = analyze_instruments(load_instruments(), snapshot)
    base_reports = {
        report["symbol"]: report
        for report in base_payload["agents"]
    }
    structure_payload = analyze_market_structure(base_reports, snapshot)
    catalyst_payload = analyze_news_catalysts(yahoo_news_service.peek_news())
    result = scout_opportunities(base_reports, structure_payload, catalyst_payload)
    result.update({
        "market_data_provider": provider,
        "market_data_status": market_status,
        "market_data_cache_status": (snapshot or {}).get("cache", {}).get("status", "not_loaded"),
        "market_data_error": market_error,
        "market_data_upstream_refreshes": manager.upstream_refreshes - before_refreshes,
    })
    return result


@app.post("/api/agents/chief-decision")
def chief_decision_agent(payload: dict[str, Any]) -> dict[str, Any]:
    """Coordinate cache-only specialist reports; never create or authorize trades."""
    cached_snapshot = market_data_manager.peek_snapshot("1m")
    base_payload = analyze_instruments(load_instruments(), cached_snapshot)
    base_reports = {report["symbol"]: report for report in base_payload["agents"]}
    structure_payload = analyze_market_structure(base_reports, cached_snapshot)
    catalyst_payload = analyze_news_catalysts(yahoo_news_service.peek_news())
    scout_payload = scout_opportunities(base_reports, structure_payload, catalyst_payload)
    sentiment_payload = compare_sentiment_assessments(payload.get("sentiment_assessments", []))
    account_payload = assess_account_protection(payload.get("account_protection"))
    return compose_chief_decision(
        base_payload, structure_payload, scout_payload,
        catalyst_payload, sentiment_payload, account_payload,
    )


@app.get("/api/approvals/proposals")
def approval_proposals() -> dict[str, Any]:
    return list_proposals()


@app.post("/api/approvals/proposals")
def submit_approval_proposal(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        actor = payload.pop("submitted_by", "local_operator")
        return create_proposal(payload, actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/approvals/proposals/{proposal_id}/decision")
def record_approval_decision(proposal_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return decide_proposal(proposal_id, payload.get("decision"), payload.get("actor"),
                               payload.get("reason"))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/approvals/proposals/from-webhook/{alert_id}")
def create_proposal_from_webhook(alert_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Route one BUY/SELL alert to one explicitly selected active account for risk review."""
    account_id = payload.get("account_id")
    account_state = payload.get("account_state")
    actor = payload.get("submitted_by", "local_operator")
    if not isinstance(account_id, str) or not account_id.strip():
        raise HTTPException(status_code=400, detail="Select a registered account explicitly.")
    if not isinstance(account_state, dict):
        raise HTTPException(status_code=400, detail="A fresh account_state snapshot is required.")
    try:
        signal = get_tradingview_signal(alert_id)
        if signal is None:
            raise HTTPException(status_code=404, detail="Webhook alert not found.")
        if signal["status"] != "received":
            raise HTTPException(status_code=409, detail="This alert has already entered the proposal-review workflow.")
        if signal["action"] not in {"buy", "sell"}:
            raise HTTPException(status_code=400, detail="CLOSE/FLATTEN alerts remain review-only and cannot create entry proposals.")
        from datetime import datetime, timedelta
        event_time = datetime.fromisoformat(signal["event_timestamp"])
        if event_time.tzinfo is None or datetime.now(UTC) - event_time.astimezone(UTC) > timedelta(minutes=15):
            raise HTTPException(status_code=409, detail="This alert is stale; only alerts within 15 minutes can enter risk review.")
        accounts = list_accounts(include_inactive=True).get("accounts", [])
        account = next((item for item in accounts if item.get("account_id") == account_id), None)
        if account is None:
            raise HTTPException(status_code=404, detail="Selected registered account was not found.")
        if account.get("status") != "active":
            raise HTTPException(status_code=409, detail="Selected account is inactive or completed.")
        if not claim_signal_for_review(alert_id):
            raise HTTPException(status_code=409, detail="This alert is already being reviewed or has already been submitted.")
        side = "long" if signal["action"] == "buy" else "short"
        proposal_payload = {
            "risk_profile_id": account["risk_profile_id"],
            "account_label": account["nickname"],
            "account_state": account_state,
            "proposal": {
                "symbol": signal["symbol"],
                "side": side,
                "quantity": signal["quantity"],
                "entry_price": signal["entry_price"],
                "stop_price": signal["stop_loss"],
                "target_price": signal["take_profit"],
                "session": account_state.get("current_session"),
            },
            "source": "tradingview_webhook",
            "source_alert_id": alert_id,
            "strategy_id": signal["strategy_id"],
            "route_key": signal.get("route_key"),
        }
        try:
            result = create_proposal(proposal_payload, actor)
        except Exception:
            set_signal_status(alert_id, "received")
            raise
        set_signal_status(alert_id, "proposal_created" if result.get("status") in {"pending", "vetoed"} else "review_blocked")
        return {
            **result,
            "source_alert_id": alert_id,
            "selected_account": {"account_id": account["account_id"], "nickname": account["nickname"],
                                 "firm": account["firm"], "account_mode": account["account_mode"]},
            "route_key_used_for_authorization": False,
            "human_approval_required": True,
            "execution": "disabled",
            "orders_created": 0,
        }
    except HTTPException:
        raise
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/webhooks/tradingview/inbox")
def tradingview_webhook_inbox(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    """Expose safe webhook metadata to the local dashboard; never approve or execute."""
    try:
        return list_tradingview_webhook_signals(limit=limit)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="TradingView webhook inbox unavailable") from exc


@app.get("/api/accounts")
def trading_accounts(include_inactive: bool = True) -> dict[str, Any]:
    """List separately registered evaluation/funded accounts; no broker access."""
    return list_accounts(include_inactive=include_inactive)


@app.post("/api/accounts")
def trading_account_create(payload: dict[str, Any]) -> dict[str, Any]:
    """Register one account and bind it to its own matching risk profile."""
    try:
        actor = payload.pop("actor", "local_operator")
        return create_account(payload, actor, get_profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/accounts/{account_id}")
def trading_account_update(account_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Update only account nickname, lifecycle status, or operator balance."""
    try:
        actor = payload.pop("actor", "local_operator")
        return update_account(account_id, payload, actor)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/broker-capabilities")
def broker_capabilities() -> dict[str, Any]:
    """List per-account provider/platform permissions; never enables execution."""
    return list_broker_capabilities()


@app.put("/api/accounts/{account_id}/broker-capability")
def broker_capability_update(account_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Record an operator-verified connection path for one registered account."""
    try:
        actor = payload.pop("actor", "local_operator")
        return upsert_broker_capability(account_id, payload, actor)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/accounts/{account_id}/events")
def trading_account_events(account_id: str) -> dict[str, Any]:
    """Return the local audit trail for one registered account."""
    try:
        return list_account_events(account_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/account-history")
def account_history_current(risk_profile_id: str = Query(..., min_length=1, max_length=120)) -> dict[str, Any]:
    """Return the latest local immutable account-history snapshot for one risk profile."""
    return get_current_history(risk_profile_id)


@app.get("/api/account-history/versions")
def account_history_versions(risk_profile_id: str = Query(..., min_length=1, max_length=120)) -> dict[str, Any]:
    """List immutable account-history revisions for one risk profile."""
    return list_history_versions(risk_profile_id)


@app.post("/api/account-history")
def account_history_save(payload: dict[str, Any]) -> dict[str, Any]:
    """Save a complete operator-imported history snapshot; no broker/provider access."""
    try:
        profile_id = payload.get("risk_profile_id")
        if not isinstance(profile_id, str) or get_profile(profile_id) is None:
            raise ValueError("A valid saved risk_profile_id is required before importing account history.")
        return save_history_snapshot(payload.get("account_history"), payload.get("actor"),
                                     payload.get("source_label"), profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/account-history/csv-preview")
def account_history_csv_preview(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a mapped CSV statement without saving or contacting any provider."""
    try:
        profile_id = payload.get("risk_profile_id")
        if not isinstance(profile_id, str) or get_profile(profile_id) is None:
            raise ValueError("Select a valid saved risk_profile_id before previewing a CSV.")
        return preview_csv(
            payload.get("csv_text"),
            payload.get("starting_balance"),
            payload.get("column_mapping"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/audit/events")
def audit_events(proposal_id: str | None = None) -> dict[str, Any]:
    return list_audit_events(proposal_id)


@app.get("/api/risk/profiles")
def risk_profiles() -> dict[str, Any]:
    """List current immutable local risk-profile versions."""
    return list_profiles()


@app.get("/api/risk/profiles/{profile_id}/versions")
def risk_profile_versions(profile_id: str) -> dict[str, Any]:
    """Return immutable profile history and configuration audit events."""
    return list_profile_versions(profile_id)


@app.post("/api/risk/profiles")
def save_risk_profile(payload: dict[str, Any]) -> dict[str, Any]:
    """Create a new immutable profile version after an explicit local action."""
    try:
        actor = payload.pop("actor", "local_operator")
        return create_profile(payload, actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/risk/prop-firm-evaluate")
def prop_firm_rules_review(payload: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a saved profile against caller-supplied account history; never executes."""
    try:
        profile_id = payload.get("risk_profile_id")
        item = get_profile(profile_id) if isinstance(profile_id, str) else None
        if item is None:
            raise ValueError("A saved risk_profile_id is required.")
        from mission_control.risk_config import resolve_effective_profile
        profile = resolve_effective_profile(profile_id)
        return evaluate_prop_firm_rules(profile, payload.get("account_history"), payload.get("account_state"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/risk/master")
def master_risk_review(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply the independent fail-closed veto; never approve or submit orders."""
    return evaluate_master_risk(payload, require_saved_profile=True)


@app.post("/api/agents/account-protection")
def account_protection_agent(payload: dict[str, Any]) -> dict[str, Any]:
    """Assess a caller-supplied account snapshot; no broker connection or persistence."""
    return assess_account_protection(payload)


@app.get("/api/agents/sentiment")
def sentiment_analyst_status() -> dict[str, Any]:
    """Describe Claude/ChatGPT review interfaces without making external calls."""
    return sentiment_interface_status()


@app.post("/api/agents/sentiment/compare")
def sentiment_analyst_compare(payload: dict[str, Any]) -> dict[str, Any]:
    """Compare caller-supplied structured reviews; no provider calls or persistence."""
    return compare_sentiment_assessments(payload.get("assessments", []))


@app.get("/api/agents/news-catalyst")
def news_catalyst_agent() -> dict[str, Any]:
    """Classify cached Yahoo headlines only; never trigger an RSS refresh."""
    return analyze_news_catalysts(yahoo_news_service.peek_news())


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


@app.get("/welcome", response_class=HTMLResponse)
def welcome_page() -> str:
    return (ROOT / "mission_control" / "welcome.html").read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
@app.get("/mission-control", response_class=HTMLResponse)
def dashboard() -> str:
    template = (ROOT / "mission_control" / "dashboard.html").read_text(encoding="utf-8")
    return build_broker_capabilities_dashboard(build_webhook_dashboard(build_dashboard(template)))
