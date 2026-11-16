"""Dedicated MNQ instrument bot for FAB787 QUANTUM.

Cache-only and analysis-only. This wrapper reuses the deterministic
analyze_instrument() agent, filters the snapshot down to MNQ so no other
instrument's data can leak in, and never fetches provider data, creates
orders, or bypasses the Master Risk Engine or human approval.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from mission_control.analysis_agents import analyze_instrument

SYMBOL = "MNQ"

MNQ_BOT_PROFILE: dict[str, Any] = {
    "symbol": SYMBOL,
    "name": "Micro E-mini Nasdaq-100",
    "enabled": True,
    "analysis_profile": "mnq",
    "session_profile": "us_index_futures",
    "risk_profile": "mnq",
    "strategy_instructions": (
        "Descriptive market-structure context only. No trade signals until a "
        "strategy is defined and verified in a later phase."
    ),
    # Intentionally empty: fill from the verified contract specification
    # before any sizing logic uses these values.
    "contract": {"multiplier": None, "tick_size": None},
}


def _isolate_snapshot(snapshot: Any) -> dict[str, Any] | None:
    """Return a copy of the snapshot that contains only MNQ rows."""
    if not isinstance(snapshot, dict):
        return None
    instruments = snapshot.get("instruments")
    if not isinstance(instruments, list):
        return None
    mnq_rows = [
        item for item in instruments
        if isinstance(item, dict) and item.get("symbol") == SYMBOL
    ]
    return {**snapshot, "instruments": mnq_rows}


def _bot_report(
    profile: dict[str, Any],
    *,
    status: str,
    reason: str | None,
    analysis: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "bot_id": "mnq_bot",
        "bot_name": "MNQ Bot",
        "symbol": SYMBOL,
        "enabled": profile.get("enabled") is True,
        "status": status,
        "reason": reason,
        "session_profile": profile.get("session_profile"),
        "risk_profile": profile.get("risk_profile"),
        "contract": profile.get("contract"),
        "strategy_instructions": profile.get("strategy_instructions"),
        "analysis": analysis,
        "mode": "paper_analysis_only",
        "trade_authority": "none",
        "trade_proposals_created": 0,
        "order_execution": "disabled",
        "human_approval_required": True,
        "provider_requests_triggered": 0,
    }


def run_mnq_bot(
    snapshot: dict[str, Any] | None,
    profile: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Produce one MNQ analysis report from an already-cached snapshot."""
    profile = profile if profile is not None else MNQ_BOT_PROFILE
    if profile.get("symbol") != SYMBOL:
        raise ValueError("run_mnq_bot requires an MNQ profile")

    if profile.get("enabled") is not True:
        return _bot_report(
            profile,
            status="disabled",
            reason="MNQ bot is disabled in configuration.",
            analysis=None,
        )

    isolated = _isolate_snapshot(snapshot)
    analysis = analyze_instrument(SYMBOL, profile, isolated, now=now)
    return _bot_report(
        profile,
        status=analysis["status"],
        reason=analysis.get("reason"),
        analysis=analysis,
    )
