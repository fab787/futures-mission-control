"""Dedicated MGC instrument bot for FAB787 QUANTUM.

Cache-only and analysis-only. This wrapper reuses the deterministic
analyze_instrument() agent, filters the snapshot down to MGC so no other
instrument's data can leak in, and never fetches provider data, creates
orders, or bypasses the Master Risk Engine or human approval.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from mission_control.analysis_agents import analyze_instrument

SYMBOL = "MGC"

MGC_BOT_PROFILE: dict[str, Any] = {
    "symbol": SYMBOL,
    "name": "Micro Gold",
    "enabled": True,
    "analysis_profile": "mgc",
    "session_profile": "metals_futures",
    "risk_profile": "mgc",
    "strategy_instructions": (
        "Descriptive market-structure context only. No trade signals until a "
        "strategy is defined and verified in a later phase."
    ),
    # Verified against CME notice SER-9247 and CME document PM264:
    # 10 troy ounces, $0.10 minimum fluctuation, $1.00 value per tick.
    "contract": {"multiplier": 10, "tick_size": 0.10, "tick_value": 1.00},
}


def _isolate_snapshot(snapshot: Any) -> dict[str, Any] | None:
    """Return a copy of the snapshot that contains only MGC rows."""
    if not isinstance(snapshot, dict):
        return None
    instruments = snapshot.get("instruments")
    if not isinstance(instruments, list):
        return None
    mgc_rows = [
        item for item in instruments
        if isinstance(item, dict) and item.get("symbol") == SYMBOL
    ]
    return {**snapshot, "instruments": mgc_rows}


def _bot_report(
    profile: dict[str, Any],
    *,
    status: str,
    reason: str | None,
    analysis: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "bot_id": "mgc_bot",
        "bot_name": "MGC Bot",
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


def run_mgc_bot(
    snapshot: dict[str, Any] | None,
    profile: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Produce one MGC analysis report from an already-cached snapshot."""
    profile = profile if profile is not None else MGC_BOT_PROFILE
    if profile.get("symbol") != SYMBOL:
        raise ValueError("run_mgc_bot requires an MGC profile")

    if profile.get("enabled") is not True:
        return _bot_report(
            profile,
            status="disabled",
            reason="MGC bot is disabled in configuration.",
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
