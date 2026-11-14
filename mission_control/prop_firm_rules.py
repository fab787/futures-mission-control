"""Fail-closed evaluator for explicitly configured prop-firm rules and account history."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def evaluate_prop_firm_rules(profile: Any, account_history: Any, account_state: Any) -> dict[str, Any]:
    """Recalculate configured drawdown, daily/session loss, consistency and trading-day rules.

    All history is caller-supplied and must be reconciled to an authenticated source
    before this evaluator can be treated as account-side enforcement.
    """
    base = {
        "evaluator": "prop_firm_rules",
        "provider_requests_triggered": 0,
        "broker_connections_triggered": 0,
        "orders_created": 0,
        "execution": "disabled",
        "trade_authority": "none",
        "human_approval_required": True,
    }
    blockers: list[str] = []
    progress: list[str] = []
    checks: list[dict[str, Any]] = []
    if not isinstance(profile, dict) or not isinstance(account_history, dict) or not isinstance(account_state, dict):
        return {**base, "status": "blocked", "blockers": ["A resolved profile, account history and current account state are required."], "checks": [], "new_trade_proposals_allowed": False}
    if profile.get("verification_status") != "user_verified":
        blockers.append("Official prop-firm maximums have not been verified.")
    if profile.get("rule_coverage_status") != "complete":
        blockers.append("Prop-firm rule coverage is incomplete; verify all applicable rules and explicitly record non-applicable rules.")
    limits = profile.get("effective_limits")
    if not isinstance(limits, dict):
        blockers.append("Effective configured risk limits are unavailable.")
        limits = {}
    drawdown_type = profile.get("drawdown_type")
    if drawdown_type not in {"trailing", "end_of_day", "static"}:
        blockers.append("Drawdown method must be trailing, end_of_day or static.")
    maximum_drawdown = limits.get("max_drawdown")
    if not _finite(maximum_drawdown) or float(maximum_drawdown) <= 0:
        blockers.append("A positive configured maximum drawdown is required.")

    observations = account_history.get("equity_observations")
    if not isinstance(observations, list) or not observations:
        blockers.append("Equity history is required to verify the configured drawdown method.")
        observations = []
    parsed_observations = []
    for index, item in enumerate(observations):
        if not isinstance(item, dict) or not _finite(item.get("equity")) or float(item["equity"]) <= 0:
            blockers.append(f"Equity observation {index + 1} is malformed.")
            continue
        stamp = _timestamp(item.get("timestamp"))
        if stamp is not None and stamp > datetime.now(timezone.utc) + timedelta(seconds=60):
            blockers.append(f"Equity observation {index + 1} timestamp is too far in the future.")
            continue
        if stamp is None:
            blockers.append(f"Equity observation {index + 1} needs a timezone-aware timestamp.")
            continue
        kind = item.get("observation_type")
        if kind not in {"intraday", "end_of_day"}:
            blockers.append(f"Equity observation {index + 1} needs observation_type intraday or end_of_day.")
            continue
        parsed_observations.append((stamp, float(item["equity"]), kind))
    starting_balance = account_history.get("starting_balance")
    if not _finite(starting_balance) or float(starting_balance) <= 0:
        blockers.append("A positive starting_balance is required to calculate drawdown.")
    if parsed_observations:
        parsed_observations.sort(key=lambda row: row[0])
        if len({row[0] for row in parsed_observations}) != len(parsed_observations):
            blockers.append("Equity history contains duplicate timestamps.")
        if drawdown_type == "end_of_day":
            equity_series = [row[1] for row in parsed_observations if row[2] == "end_of_day"]
        else:
            equity_series = [row[1] for row in parsed_observations]
        if _finite(starting_balance):
            equity_series.insert(0, float(starting_balance))
        if not equity_series or (drawdown_type in {"trailing", "end_of_day"} and len(equity_series) < 3):
            blockers.append("At least two usable equity observations plus starting balance are required to calculate trailing/end-of-day drawdown.")
        else:
            if drawdown_type == "static":
                measured_drawdown = max(0.0, float(starting_balance) - min(equity_series))
            else:
                peak = equity_series[0]
                measured_drawdown = 0.0
                for equity in equity_series:
                    peak = max(peak, equity)
                    measured_drawdown = max(measured_drawdown, peak - equity)
            if measured_drawdown is not None:
                checks.append({"name": "drawdown_method", "method": drawdown_type, "measured": round(measured_drawdown, 2), "limit": float(maximum_drawdown) if _finite(maximum_drawdown) else None, "status": "breached" if _finite(maximum_drawdown) and measured_drawdown >= float(maximum_drawdown) else "within_limit"})
                if _finite(maximum_drawdown) and measured_drawdown >= float(maximum_drawdown):
                    blockers.append("Historical drawdown exceeds the configured maximum for the selected drawdown method.")

    daily_results = account_history.get("daily_results")
    if not isinstance(daily_results, list):
        blockers.append("Daily account history is required to verify daily loss, consistency and trading-day rules.")
        daily_results = []
    if not daily_results:
        blockers.append("At least one daily result is required to verify daily-loss history.")
    valid_days = []
    for index, day in enumerate(daily_results):
        if not isinstance(day, dict) or not isinstance(day.get("date"), str) or not _finite(day.get("net_pnl")) or not isinstance(day.get("trading_day"), bool):
            blockers.append(f"Daily result {index + 1} must include date, finite net_pnl and boolean trading_day.")
            continue
        try:
            datetime.strptime(day["date"], "%Y-%m-%d")
        except ValueError:
            blockers.append(f"Daily result {index + 1} date must use YYYY-MM-DD.")
            continue
        valid_days.append(day)
    dates = [day["date"] for day in valid_days]
    if len(set(dates)) != len(dates):
        blockers.append("Daily history contains duplicate dates.")
    daily_cap = limits.get("max_daily_loss")
    if not _finite(daily_cap) or float(daily_cap) <= 0:
        blockers.append("A positive maximum daily loss is required.")
    elif valid_days:
        worst_day = min(float(day["net_pnl"]) for day in valid_days)
        checks.append({"name": "historical_daily_loss", "worst_day": worst_day, "limit": float(daily_cap), "status": "breached" if worst_day <= -float(daily_cap) else "within_limit"})
        if worst_day <= -float(daily_cap):
            blockers.append("Historical daily loss exceeded the configured daily-loss limit.")

    session_results = account_history.get("session_results")
    if not isinstance(session_results, list):
        blockers.append("Session account history is required to verify historical session loss.")
        session_results = []
    session_cap = limits.get("max_session_loss")
    if not _finite(session_cap) or float(session_cap) <= 0:
        blockers.append("A positive maximum session loss is required.")
    elif session_results:
        parsed_sessions = []
        for index, session in enumerate(session_results):
            if not isinstance(session, dict) or not isinstance(session.get("date"), str) or session.get("session") not in {"NY", "GLOBEX"} or not _finite(session.get("net_pnl")):
                blockers.append(f"Session result {index + 1} is malformed.")
                continue
            parsed_sessions.append(session)
        session_keys = [(item["date"], item["session"]) for item in parsed_sessions]
        if len(set(session_keys)) != len(session_keys):
            blockers.append("Session history contains duplicate date/session records.")
        worst_session = min((float(item["net_pnl"]) for item in parsed_sessions), default=0.0)
        checks.append({"name": "historical_session_loss", "worst_session": worst_session, "limit": float(session_cap), "status": "breached" if worst_session <= -float(session_cap) else "within_limit"})
        if worst_session <= -float(session_cap):
            blockers.append("Historical session loss exceeded the configured session-loss limit.")
    else:
        blockers.append("At least one session result is required to verify session-loss history.")

    evaluation_rules = profile.get("evaluation_rules")
    if not isinstance(evaluation_rules, dict):
        blockers.append("Evaluation rules must be explicitly configured.")
        evaluation_rules = {}
    minimum_days = evaluation_rules.get("minimum_trading_days")
    if minimum_days is not None:
        if not isinstance(minimum_days, int) or isinstance(minimum_days, bool) or minimum_days < 1:
            blockers.append("minimum_trading_days must be a positive whole number or explicitly null.")
        else:
            trading_days = len({day["date"] for day in valid_days if day["trading_day"]})
            checks.append({"name": "minimum_trading_days", "actual": trading_days, "required": minimum_days, "status": "met" if trading_days >= minimum_days else "in_progress"})
            if trading_days < minimum_days:
                progress.append("minimum trading-day requirement is still in progress.")

    consistency = profile.get("consistency_rule")
    if consistency is not None:
        if not isinstance(consistency, dict) or not _finite(consistency.get("max_best_day_profit_share_pct")) or not 0 < float(consistency["max_best_day_profit_share_pct"]) <= 100:
            blockers.append("Configured consistency rule is invalid.")
        else:
            total_net = sum(float(day["net_pnl"]) for day in valid_days)
            best_day = max((float(day["net_pnl"]) for day in valid_days), default=0.0)
            if total_net <= 0 or best_day <= 0:
                blockers.append("Positive cumulative net profit is required to calculate best-day consistency.")
            else:
                share = best_day / total_net * 100.0
                maximum_share = float(consistency["max_best_day_profit_share_pct"])
                checks.append({"name": "best_day_profit_share", "actual_pct": round(share, 2), "maximum_pct": maximum_share, "status": "within_limit" if share <= maximum_share else "breached"})
                if share > maximum_share:
                    progress.append("Best-day profit share is currently above the configured consistency threshold; additional net profit may change this ratio.")

    profit_target = limits.get("profit_target")
    if profit_target is not None:
        cumulative = account_state.get("cumulative_net_profit")
        if not _finite(profit_target) or float(profit_target) <= 0:
            blockers.append("Configured profit_target must be positive and finite.")
        elif not _finite(cumulative):
            blockers.append("cumulative_net_profit is required to verify the configured profit target.")
        else:
            reached = float(cumulative) >= float(profit_target)
            checks.append({"name": "profit_target", "actual": float(cumulative), "target": float(profit_target), "status": "reached" if reached else "in_progress"})
            if reached:
                blockers.append("Configured profit target has been reached; additional proposals are blocked pending the firm's completion process.")

    status = "blocked" if blockers else "in_progress" if progress else "within_rules"
    return {**base, "status": status, "blockers": blockers, "progress_notes": progress, "checks": checks,
            "drawdown_method": drawdown_type, "new_trade_proposals_allowed": status in {"within_rules", "in_progress"},
            "history_source": "caller_supplied_unverified", "history_is_broker_authenticated": False}
