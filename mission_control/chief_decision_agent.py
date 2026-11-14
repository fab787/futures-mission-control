"""Cache-only coordination layer for FAB787 QUANTUM specialist reports."""
from __future__ import annotations

from typing import Any


def compose_chief_decision(
    instruments: dict[str, Any],
    market_structure: dict[str, Any],
    opportunity_scout: dict[str, Any],
    news_catalyst: dict[str, Any],
    sentiment: dict[str, Any],
    account_protection: dict[str, Any],
) -> dict[str, Any]:
    """Summarize specialist readiness without making a trading decision."""
    blockers: list[str] = []
    warnings: list[str] = []

    market_status = instruments.get("status")
    structure_status = market_structure.get("status")
    scout_status = opportunity_scout.get("status")
    if market_status not in {"ready", "partial"}:
        blockers.append("Instrument market context is unavailable or blocked.")
    if structure_status not in {"ready", "partial"}:
        blockers.append("Market structure context is unavailable or blocked.")
    if scout_status not in {"ready", "partial"}:
        blockers.append("Opportunity Scout context is unavailable or blocked.")
    if market_status == "partial" or structure_status == "partial" or scout_status == "partial":
        warnings.append("One or more instruments have incomplete market context.")

    account_status = account_protection.get("status")
    if account_status in {"blocked", "breached"}:
        blockers.append("Account protection is blocked or a configured risk limit is breached.")
    elif account_status == "warning":
        warnings.append("Account protection reported one or more risk warnings.")
        blockers.append("Account Protection warnings must be resolved before Master Risk Engine review.")
    elif account_status != "within_limits":
        blockers.append("A verified account-protection assessment is required.")
    if account_protection.get("new_trade_proposals_allowed") is not True:
        blockers.append("Account Protection has not explicitly permitted new trade proposals.")

    if sentiment.get("status") == "awaiting_reviews":
        warnings.append("Claude/ChatGPT reviews are not available; no AI consensus is claimed.")
    elif sentiment.get("status") not in {"ready", "partial", "awaiting_reviews"}:
        warnings.append("Sentiment review comparison is unavailable or invalid.")
    sentiment_items = sentiment.get("instruments", [])
    if not isinstance(sentiment_items, list):
        warnings.append("Sentiment comparison payload is malformed; model context is unavailable.")
        sentiment_items = []
    for item in sentiment_items:
        if not isinstance(item, dict):
            warnings.append("A malformed sentiment item was ignored.")
            continue
        if item.get("status") == "disagreement":
            warnings.append(
                f"Claude and ChatGPT disagree on {item.get('symbol', 'an instrument')}; both views are preserved."
            )
    if news_catalyst.get("status") in {"blocked", "stale_context"}:
        warnings.append("News and catalyst context is unavailable or stale.")
    if not isinstance(news_catalyst.get("calendar", {}), dict):
        warnings.append("Economic-calendar context is malformed and unavailable.")
    elif news_catalyst.get("calendar", {}).get("status") == "page_link_only":
        warnings.append("Structured economic-calendar events are not verified in this phase.")

    ready_for_risk = not blockers
    status = "blocked" if blockers else "warning" if warnings else "ready"
    return {
        "specialist_id": "chief_decision_agent",
        "specialist_name": "Chief Decision Agent",
        "status": status,
        "mode": "read_only_specialist_coordination",
        "market_context": {
            "instrument_status": market_status,
            "market_structure_status": structure_status,
            "opportunity_scout_status": scout_status,
            "ranked_context": opportunity_scout.get("ranked_instruments", []),
            "news_status": news_catalyst.get("status"),
            "sentiment_status": sentiment.get("status"),
            "sentiment_instruments": sentiment.get("instruments", []),
        },
        "account_protection": {
            "status": account_status,
            "breaches": account_protection.get("breaches", []),
            "warnings": account_protection.get("warnings", []),
            "new_trade_proposals_allowed": account_protection.get(
                "new_trade_proposals_allowed", False
            ),
        },
        "blocking_conditions": blockers,
        "warnings": warnings,
        "downstream_gate": {
            "inputs_ready_for_master_risk_engine": ready_for_risk,
            "master_risk_engine_review_required": True,
            "human_approval_required": True,
            "this_is_trade_approval": False,
        },
        "decision": {
            "classification": "context_only",
            "outcome": "no_trade_decision",
            "trade_proposals_created": 0,
            "orders_created": 0,
            "trade_authority": "none",
            "risk_setting_changes": False,
            "execution": "disabled",
        },
        "provider_requests_triggered": 0,
        "account_connections_triggered": 0,
        "limitations": [
            "Market and news inputs are read from existing caches only.",
            "Sentiment reviews and account snapshots are supplied by the caller and are not independently authenticated.",
            "A ready downstream gate means only that required inputs are present for independent Master Risk Engine review.",
            "This agent cannot approve trades, enforce broker-side controls, or manage existing positions.",
        ],
    }
