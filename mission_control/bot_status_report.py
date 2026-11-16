"""Read-only status report for the MNQ, MES, and MGC analysis bots.

Runs each bot against the same cached snapshot and returns one summary.
It never fetches provider data, never creates orders or trade proposals,
and never changes settings or the dashboard.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from mission_control.mes_bot import MES_BOT_PROFILE, run_mes_bot
from mission_control.mgc_bot import MGC_BOT_PROFILE, run_mgc_bot
from mission_control.mnq_bot import MNQ_BOT_PROFILE, run_mnq_bot

BOTS = (
    ("MNQ", run_mnq_bot, MNQ_BOT_PROFILE),
    ("MES", run_mes_bot, MES_BOT_PROFILE),
    ("MGC", run_mgc_bot, MGC_BOT_PROFILE),
)

VERIFICATION = {
    "MNQ": "Verified against CME/broker contract specifications.",
    "MES": "Owner-approved from public broker references; CME re-check pending.",
    "MGC": "Verified against CME notice SER-9247.",
}

SAFETY = {
    "mode": "read_only_status_report",
    "trade_authority": "none",
    "trade_proposals_created": 0,
    "order_execution": "disabled",
    "human_approval_required": True,
    "provider_requests_triggered": 0,
}


def _summarize(symbol: str, report: dict[str, Any]) -> dict[str, Any]:
    analysis = report.get("analysis") or {}
    freshness = analysis.get("freshness") or {}
    contract = report.get("contract") or {}
    return {
        "symbol": symbol,
        "enabled": report["enabled"],
        "status": report["status"],
        "reason": report["reason"],
        "freshness_status": freshness.get("status"),
        "latest_bar_at": freshness.get("latest_bar_at"),
        "age_seconds": freshness.get("age_seconds"),
        "contract_multiplier": contract.get("multiplier"),
        "contract_tick_size": contract.get("tick_size"),
        "contract_tick_value": contract.get("tick_value"),
        "verification": VERIFICATION.get(symbol, "Not recorded."),
        "safety": dict(SAFETY),
    }


def build_bot_status(
    snapshot: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run every bot against one cached snapshot and summarize the results."""
    bots = [
        _summarize(symbol, runner(snapshot, profile, now=now))
        for symbol, runner, profile in BOTS
    ]
    total = len(bots)
    ready = sum(bot["status"] == "ready" for bot in bots)
    blocked = sum(bot["status"] == "blocked" for bot in bots)
    disabled = sum(bot["status"] == "disabled" for bot in bots)

    if ready == total:
        overall = "ready"
    elif ready > 0:
        overall = "partial"
    else:
        overall = "blocked"

    return {
        "report": "bot_status",
        "overall_status": overall,
        "summary": {
            "total": total,
            "ready": ready,
            "blocked": blocked,
            "disabled": disabled,
        },
        "bots": bots,
        "safety": dict(SAFETY),
    }
