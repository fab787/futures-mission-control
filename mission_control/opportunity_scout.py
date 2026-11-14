"""Deterministic, cache-only Opportunity Scout.

Ranks descriptive cross-market context. Scores are confluence bookkeeping, not
probabilities, forecasts, entries, trade proposals, or risk approvals.
"""
from __future__ import annotations

from typing import Any


def _score_instrument(
    base: dict[str, Any],
    structure_item: dict[str, Any],
) -> dict[str, Any]:
    symbol = base.get("symbol", "UNKNOWN")
    if base.get("status") != "ready":
        return {
            "symbol": symbol,
            "status": "blocked",
            "reason": "Instrument report is not ready; no opportunity ranking is produced.",
            "context_score": None,
            "score_is_probability": False,
        }
    if structure_item.get("status") != "ready":
        return {
            "symbol": symbol,
            "status": "blocked",
            "reason": "Market structure report is not ready; no opportunity ranking is produced.",
            "context_score": None,
            "score_is_probability": False,
        }

    market = base.get("market_structure") or {}
    structure = structure_item.get("market_structure") or {}
    direction = market.get("direction_20_bars")
    alignment = structure.get("ema_alignment")
    swing = structure.get("confirmed_swing_structure")
    relative_volume = market.get("relative_volume")
    components: dict[str, int] = {
        "directional_alignment": 0,
        "swing_confirmation": 0,
        "relative_volume_context": 0,
        "nearby_structure_context": 0,
    }

    upward = direction == "upward" and alignment == "bullish_alignment"
    downward = direction == "downward" and alignment == "bearish_alignment"
    if upward or downward:
        components["directional_alignment"] = 30
    elif (
        direction == "upward" and alignment == "mixed_bullish"
    ) or (
        direction == "downward" and alignment == "mixed_bearish"
    ):
        components["directional_alignment"] = 15

    if (upward and swing == "higher_highs_higher_lows") or (
        downward and swing == "lower_highs_lower_lows"
    ):
        components["swing_confirmation"] = 25
    elif swing in {"higher_highs_higher_lows", "lower_highs_lower_lows"}:
        components["swing_confirmation"] = 10

    if isinstance(relative_volume, (int, float)) and relative_volume >= 1.5:
        components["relative_volume_context"] = 15
    elif isinstance(relative_volume, (int, float)) and relative_volume >= 1.0:
        components["relative_volume_context"] = 8

    atr = market.get("atr_14")
    if isinstance(atr, (int, float)) and atr > 0:
        distances = []
        for key in ("nearest_support", "nearest_resistance"):
            level = structure.get(key)
            if isinstance(level, dict) and isinstance(level.get("distance_points"), (int, float)):
                distances.append(abs(level["distance_points"]))
        if distances and min(distances) <= atr:
            components["nearby_structure_context"] = 10

    score = sum(components.values())
    if upward:
        context = "aligned_upward_context"
    elif downward:
        context = "aligned_downward_context"
    elif direction in {"upward", "downward"}:
        context = "direction_without_full_trend_alignment"
    else:
        context = "mixed_or_sideways_context"

    return {
        "symbol": symbol,
        "instrument_name": base.get("instrument_name", symbol),
        "status": "ranked",
        "context": context,
        "direction_20_bars": direction,
        "ema_alignment": alignment,
        "confirmed_swing_structure": swing,
        "last_close": market.get("last_close"),
        "atr_14": atr,
        "relative_volume": relative_volume,
        "nearest_support": structure.get("nearest_support"),
        "nearest_resistance": structure.get("nearest_resistance"),
        "freshness": base.get("freshness"),
        "context_score": score,
        "score_components": components,
        "score_scale": "0-80 deterministic confluence points; not a probability or expected return",
        "score_is_probability": False,
        "interpretation": (
            "Descriptive context only. No entry, exit, stop, target, sizing, or trade "
            "proposal is generated."
        ),
    }


def scout_opportunities(
    instrument_reports: dict[str, dict[str, Any]],
    market_structure: dict[str, Any] | None,
    news_catalysts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rank ready instruments using existing specialist reports only."""
    base = {
        "specialist_id": "opportunity_scout",
        "specialist_name": "Opportunity Scout",
        "mode": "deterministic_cache_only_context_ranking",
        "provider_requests_triggered": 0,
        "trade_proposals_created": 0,
        "risk_authority": "none",
        "trade_authority": "none",
        "execution": "disabled",
        "human_approval_required": True,
    }
    if market_structure is None:
        return {
            **base,
            "status": "blocked",
            "reason": "Market Structure report is unavailable.",
            "ranked_instruments": [],
            "summary": {"total_instruments": len(instrument_reports), "ranked": 0, "blocked": len(instrument_reports)},
        }

    structure_by_symbol = {
        item.get("symbol"): item
        for item in market_structure.get("instruments", [])
        if isinstance(item, dict) and isinstance(item.get("symbol"), str)
    }
    ranked = [
        _score_instrument(base_report, structure_by_symbol.get(symbol, {}))
        for symbol, base_report in instrument_reports.items()
    ]
    ranked.sort(
        key=lambda item: (
            item.get("status") == "ranked",
            item.get("context_score") if item.get("context_score") is not None else -1,
        ),
        reverse=True,
    )
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index if item.get("status") == "ranked" else None

    catalyst_status = (news_catalysts or {}).get("status", "not_loaded")
    catalyst_summary = (news_catalysts or {}).get("summary", {})
    high_priority = catalyst_summary.get("high_priority", 0)
    if catalyst_status == "stale_context":
        catalyst_note = "Cached headlines are stale; treat them only as historical context."
    elif catalyst_status != "ready":
        catalyst_note = "No current cached headline context is available."
    elif high_priority:
        catalyst_note = (
            f"{high_priority} high-priority keyword-tagged headline(s) are present. "
            "Review source stories manually; no directional impact is inferred."
        )
    else:
        catalyst_note = "No high-priority keyword-tagged headlines in the cached feed."

    ready_count = sum(item["status"] == "ranked" for item in ranked)
    return {
        **base,
        "status": "ready" if ready_count == len(ranked) and ranked else "partial" if ready_count else "blocked",
        "ranked_instruments": ranked,
        "catalyst_context": {
            "status": catalyst_status,
            "high_priority_headlines": high_priority,
            "note": catalyst_note,
            "directional_bias": "not_assessed",
        },
        "summary": {
            "total_instruments": len(ranked),
            "ranked": ready_count,
            "blocked": len(ranked) - ready_count,
            "top_ranked_symbol": next((item["symbol"] for item in ranked if item["status"] == "ranked"), None),
        },
        "scoring_method": (
            "Directional agreement (0-30), confirmed swing agreement (0-25), "
            "relative volume context (0-15), and proximity to a confirmed structure "
            "level within one ATR (0-10). Missing inputs receive zero points. "
            "The score is not a probability, forecast, expected return, or backtest result."
        ),
        "limitations": [
            "Ranks descriptive context, not actionable trade setups.",
            "No entry, exit, stop, target, contract size, or expected return is calculated.",
            "News is keyword triage only and is not mapped to individual futures contracts.",
            "Every downstream trade proposal must pass the independent Master Risk Engine and explicit human approval.",
        ],
    }
