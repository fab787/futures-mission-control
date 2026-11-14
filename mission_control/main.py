from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from mission_control.analysis_agents import analyze_instruments
from mission_control.chief_decision_agent import compose_chief_decision
from mission_control.account_protection import assess_account_protection
from mission_control.account_registry import create_account, list_account_events, list_accounts, update_account
from mission_control.gex import GexUnavailable, gex_service
from mission_control.market_data_manager import market_data_manager
from mission_control.master_risk_engine import evaluate_master_risk
from mission_control.prop_firm_rules import evaluate_prop_firm_rules
from mission_control.risk_config import create_profile, get_profile, list_profile_versions, list_profiles
from mission_control.audit_ledger import create_proposal, decide_proposal, list_audit_events, list_proposals
from mission_control.account_history import get_current_history, list_history_versions, save_history_snapshot
from mission_control.account_history_csv import preview_csv
from mission_control.market_structure import analyze_market_structure
from mission_control.news import YAHOO_CALENDAR_URL, yahoo_news_service
from mission_control.news_catalyst import analyze_news_catalysts
from mission_control.opportunity_scout import scout_opportunities
from mission_control.sentiment_analyst import compare_sentiment_assessments, sentiment_interface_status
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


@app.get("/api/agents/opportunity-scout")
def opportunity_scout_agent() -> dict[str, Any]:
    """Rank cached cross-instrument context without triggering provider refreshes."""
    cached_snapshot = market_data_manager.peek_snapshot("1m")
    base_payload = analyze_instruments(load_instruments(), cached_snapshot)
    base_reports = {
        report["symbol"]: report
        for report in base_payload["agents"]
    }
    structure_payload = analyze_market_structure(base_reports, cached_snapshot)
    catalyst_payload = analyze_news_catalysts(yahoo_news_service.peek_news())
    return scout_opportunities(base_reports, structure_payload, catalyst_payload)


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


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return (ROOT / "mission_control" / "dashboard.html").read_text(encoding="utf-8")
