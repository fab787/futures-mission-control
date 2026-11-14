"""Read-only account protection assessment for FAB787 QUANTUM.

This module evaluates an explicitly supplied account/risk snapshot. It does not
connect to a broker, persist account state, change limits, or authorize trades.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

MAX_SNAPSHOT_AGE_SECONDS = 120.0
MAX_FUTURE_SKEW_SECONDS = 60.0

REQUIRED_LIMITS = (
    "max_daily_loss",
    "max_session_loss",
    "max_drawdown",
    "max_contracts",
)
REQUIRED_STATE = (
    "daily_pnl",
    "session_pnl",
    "drawdown_from_high_water",
    "open_contracts",
    "position_state",
)


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _validate_positive_limit(limits: dict[str, Any], key: str, errors: list[str]) -> float | None:
    value = limits.get(key)
    if not _finite_number(value) or float(value) <= 0:
        errors.append(f"Risk limit '{key}' must be a positive finite number.")
        return None
    return float(value)


def assess_account_protection(payload: Any) -> dict[str, Any]:
    """Assess account state against supplied limits; fail closed on uncertainty."""
    base = {
        "specialist_id": "account_protection_analyst",
        "specialist_name": "Account Protection Analyst",
        "mode": "read_only_snapshot_assessment",
        "provider_requests_triggered": 0,
        "account_connections_triggered": 0,
        "risk_settings_modified": False,
        "trade_proposals_created": 0,
        "trade_authority": "none",
        "execution": "disabled",
        "human_approval_required": True,
    }
    if not isinstance(payload, dict):
        return {
            **base, "status": "blocked",
            "reason": "A structured account snapshot and explicit risk limits are required.",
            "breaches": [], "warnings": [], "checks": [],
            "new_trade_proposals_allowed": False,
        }

    limits = payload.get("risk_limits")
    state = payload.get("account_state")
    if not isinstance(limits, dict) or not isinstance(state, dict):
        return {
            **base, "status": "blocked",
            "reason": "Both risk_limits and account_state objects are required; no defaults are assumed.",
            "breaches": [], "warnings": [], "checks": [],
            "new_trade_proposals_allowed": False,
        }

    errors: list[str] = []
    snapshot_value = state.get("snapshot_at")
    snapshot_time = None
    if not isinstance(snapshot_value, str):
        errors.append("Account state snapshot_at must be a timezone-aware ISO 8601 timestamp.")
    else:
        try:
            snapshot_time = datetime.fromisoformat(snapshot_value.replace("Z", "+00:00"))
        except ValueError:
            errors.append("Account state snapshot_at must be a valid ISO 8601 timestamp.")
        if snapshot_time is not None and snapshot_time.tzinfo is None:
            errors.append("Account state snapshot_at must include a timezone.")
            snapshot_time = None
    if snapshot_time is not None:
        snapshot_age = (datetime.now(timezone.utc) - snapshot_time.astimezone(timezone.utc)).total_seconds()
        if snapshot_age < -MAX_FUTURE_SKEW_SECONDS:
            errors.append("Account state snapshot_at is too far in the future.")
        elif snapshot_age > MAX_SNAPSHOT_AGE_SECONDS:
            errors.append("Account state snapshot is stale; maximum age is 120 seconds.")
    missing_limits = [key for key in REQUIRED_LIMITS if key not in limits]
    missing_state = [key for key in REQUIRED_STATE if key not in state]
    if missing_limits:
        errors.append("Missing required risk limits: " + ", ".join(missing_limits) + ".")
    if missing_state:
        errors.append("Missing required account state: " + ", ".join(missing_state) + ".")

    parsed_limits = {
        key: _validate_positive_limit(limits, key, errors)
        for key in REQUIRED_LIMITS
    }
    if parsed_limits["max_contracts"] is not None and not parsed_limits["max_contracts"].is_integer():
        errors.append("Risk limit 'max_contracts' must be a whole number.")
    for optional in ("profit_target", "profit_protection_activation", "profit_protection_floor"):
        if optional in limits and (
            not _finite_number(limits[optional]) or float(limits[optional]) < 0
        ):
            errors.append(f"Optional limit '{optional}' must be a non-negative finite number.")
    if (
        "profit_protection_activation" in limits
        and "profit_protection_floor" in limits
        and _finite_number(limits["profit_protection_activation"])
        and _finite_number(limits["profit_protection_floor"])
        and float(limits["profit_protection_floor"]) > float(limits["profit_protection_activation"])
    ):
        errors.append("profit_protection_floor cannot exceed its activation threshold.")

    numeric_state: dict[str, float] = {}
    for key in ("daily_pnl", "session_pnl", "drawdown_from_high_water", "open_contracts"):
        value = state.get(key)
        if not _finite_number(value):
            errors.append(f"Account state '{key}' must be a finite number.")
        else:
            numeric_state[key] = float(value)

    if "profit_protection_activation" in limits and "profit_protection_floor" in limits:
        value = state.get("daily_high_water_pnl")
        if not _finite_number(value):
            errors.append("Account state daily_high_water_pnl is required when profit protection is configured.")
        else:
            numeric_state["daily_high_water_pnl"] = float(value)

    position_state = state.get("position_state")
    if not isinstance(position_state, str) or position_state not in {"flat", "open", "unknown"}:
        errors.append("Account state 'position_state' must be flat, open, or unknown.")
    elif position_state == "unknown":
        errors.append("Position state is unknown; existing exposure cannot be verified.")

    if "open_contracts" in numeric_state and (
        numeric_state["open_contracts"] < 0
        or not numeric_state["open_contracts"].is_integer()
    ):
        errors.append("Account state 'open_contracts' must be a non-negative whole number.")
    if "drawdown_from_high_water" in numeric_state and numeric_state["drawdown_from_high_water"] < 0:
        errors.append("drawdown_from_high_water must be non-negative.")
    if errors:
        return {
            **base, "status": "blocked",
            "reason": "Account protection cannot be evaluated safely.",
            "validation_errors": errors,
            "breaches": [], "warnings": [], "checks": [],
            "new_trade_proposals_allowed": False,
        }

    breaches: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    def evaluate(name: str, actual: float, limit: float, *, comparison: str, unit: str) -> None:
        breached = (
            actual <= -limit if comparison == "loss"
            else actual > limit if comparison == "cap"
            else actual >= limit
        )
        utilization = abs(actual) / limit if comparison == "loss" else actual / limit
        item = {
            "name": name,
            "actual": actual,
            "limit": limit,
            "unit": unit,
            "utilization_pct": round(max(0.0, utilization) * 100.0, 1),
            "status": "breached" if breached else "warning" if utilization >= 0.8 else "within_limit",
        }
        checks.append(item)
        if breached:
            breaches.append(item)
        elif utilization >= 0.8:
            warnings.append(item)

    evaluate("daily_loss", numeric_state["daily_pnl"], parsed_limits["max_daily_loss"], comparison="loss", unit="account_currency")
    evaluate("session_loss", numeric_state["session_pnl"], parsed_limits["max_session_loss"], comparison="loss", unit="account_currency")
    evaluate("high_water_drawdown", numeric_state["drawdown_from_high_water"], parsed_limits["max_drawdown"], comparison="drawdown", unit="account_currency")
    evaluate("open_contracts", numeric_state["open_contracts"], parsed_limits["max_contracts"], comparison="cap", unit="contracts")

    if position_state == "flat" and numeric_state["open_contracts"] != 0:
        breaches.append({
            "name": "position_state_consistency",
            "status": "breached",
            "reason": "Position state says flat but open_contracts is nonzero.",
        })
    if position_state == "open" and numeric_state["open_contracts"] == 0:
        breaches.append({
            "name": "position_state_consistency",
            "status": "breached",
            "reason": "Position state says open but open_contracts is zero.",
        })

    profit_activation = limits.get("profit_protection_activation")
    profit_floor = limits.get("profit_protection_floor")
    if profit_activation is not None and profit_floor is not None:
        daily_pnl = numeric_state["daily_pnl"]
        daily_high_water = numeric_state["daily_high_water_pnl"]
        if daily_high_water >= float(profit_activation) and daily_pnl < float(profit_floor):
            breaches.append({
                "name": "profit_protection_floor",
                "status": "breached",
                "actual": daily_pnl,
                "daily_high_water_pnl": daily_high_water,
                "activation": float(profit_activation),
                "floor": float(profit_floor),
                "reason": "Daily P&L is below the configured protection floor after activation.",
            })
        elif daily_high_water >= float(profit_activation):
            checks.append({
                "name": "profit_protection_floor",
                "status": "active",
                "actual": daily_pnl,
                "activation": float(profit_activation),
                "floor": float(profit_floor),
            })

    profit_target = limits.get("profit_target")
    target_reached = profit_target is not None and numeric_state["daily_pnl"] >= float(profit_target)
    if target_reached:
        warnings.append({
            "name": "profit_target_reached",
            "status": "target_reached",
            "actual": numeric_state["daily_pnl"],
            "target": float(profit_target),
            "reason": "Configured daily profit target reached; stop-new-trades policy should be enforced by the Master Risk Engine.",
        })

    if breaches:
        status = "breached"
    elif warnings:
        status = "warning"
    else:
        status = "within_limits"
    return {
        **base,
        "status": status,
        "account_id": str(payload.get("account_label") or "unspecified")[:80],
        "breaches": breaches,
        "warnings": warnings,
        "checks": checks,
        "profit_target_reached": target_reached,
        "new_trade_proposals_allowed": not breaches and not target_reached,
        "decision_note": (
            "A breach is a veto condition for new proposals; existing positions require verified account/position handling."
            if breaches else
            "The configured profit target has been reached; new trade proposals should stop."
            if target_reached else
            "Warnings require attention. This analyst does not approve trades."
            if warnings else
            "No configured limit breach was detected in this supplied snapshot; this is not trade approval."
        ),
        "limitations": [
            "Account state and risk limits are supplied by the caller; no broker/account connection exists.",
            "This is a point-in-time check, not continuous monitoring.",
            "The analyst cannot flatten positions, cancel orders, change limits, or enforce broker-side controls.",
            "A missing, stale, or unverifiable account/position snapshot must block downstream decisions.",
            "The independent Master Risk Engine retains veto authority and human approval remains mandatory.",
        ],
    }
