"""Independent fail-closed Master Risk Engine for FAB787 QUANTUM."""
from __future__ import annotations
import math
from typing import Any
from mission_control.account_protection import assess_account_protection

POINT_VALUES = {"MNQ": 2.0, "MES": 5.0, "MGC": 10.0}
ALLOWED_SESSIONS = {"NY", "GLOBEX"}

def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))

def evaluate_master_risk(payload: Any) -> dict[str, Any]:
    """Veto unsafe or incomplete proposals; never approve or execute a trade."""
    base = {"engine":"master_risk_engine","authority":"independent_veto_only",
            "provider_requests_triggered":0,"broker_connections_triggered":0,
            "risk_settings_modified":False,"orders_created":0,"execution":"disabled",
            "human_approval_required":True}
    blockers: list[str] = []
    checks: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        payload = {}
        blockers.append("A structured risk-review payload is required.")
    limits, state, proposal = payload.get("risk_limits"), payload.get("account_state"), payload.get("proposal")
    if not isinstance(limits, dict):
        limits = {}
        blockers.append("Explicit risk_limits are required.")
    if not isinstance(state, dict):
        state = {}
        blockers.append("A fresh account_state snapshot is required.")
    if not isinstance(proposal, dict):
        proposal = {}
        blockers.append("A complete proposed-trade object is required.")
    account = assess_account_protection({"account_label":payload.get("account_label"),
                                         "risk_limits":limits,"account_state":state})
    if account.get("status") != "within_limits":
        blockers.append("Account Protection did not return within_limits; warnings, breaches, or invalid/missing account data veto this proposal.")
    if account.get("new_trade_proposals_allowed") is not True:
        blockers.append("Account Protection does not permit new trade proposals.")

    symbol, side = proposal.get("symbol"), proposal.get("side")
    point_value = POINT_VALUES.get(symbol) if isinstance(symbol, str) else None
    if point_value is None:
        blockers.append("Proposal symbol must be one of MNQ, MES, or MGC.")
    if not isinstance(side, str) or side not in {"long","short"}:
        blockers.append("Proposal side must be long or short.")
    quantity = proposal.get("quantity")
    qty = int(quantity) if _number(quantity) and float(quantity)>0 and float(quantity).is_integer() else None
    if qty is None:
        blockers.append("Proposal quantity must be a positive whole number.")
    prices = {}
    for key in ("entry_price","stop_price","target_price"):
        value=proposal.get(key)
        if not _number(value) or float(value)<=0:
            blockers.append(f"A positive finite {key} is required.")
            prices[key]=None
        else:
            prices[key]=float(value)
    entry, stop, target = prices["entry_price"],prices["stop_price"],prices["target_price"]
    if entry is not None and stop is not None and side in {"long","short"}:
        if not (stop<entry if side=="long" else stop>entry):
            blockers.append("Stop-loss must be below entry for long trades and above entry for short trades.")
        else:
            checks.append({"name":"stop_direction","status":"passed"})
    if entry is not None and target is not None and side in {"long","short"}:
        if not (target>entry if side=="long" else target<entry):
            blockers.append("Profit target must be above entry for long trades and below entry for short trades.")
        else:
            checks.append({"name":"target_direction","status":"passed"})

    max_risk=limits.get("max_per_trade_risk")
    if not _number(max_risk) or float(max_risk)<=0:
        blockers.append("A positive finite max_per_trade_risk must be explicitly configured.")
    allowed=limits.get("allowed_sessions")
    if not isinstance(allowed,list) or not allowed or any(not isinstance(x, str) or x not in ALLOWED_SESSIONS for x in allowed):
        blockers.append("allowed_sessions must explicitly contain NY and/or GLOBEX.")
    current_session, proposal_session=state.get("current_session"),proposal.get("session")
    if (
        not isinstance(current_session, str)
        or current_session not in ALLOWED_SESSIONS
        or not isinstance(proposal_session, str)
        or proposal_session not in ALLOWED_SESSIONS
    ):
    elif current_session != proposal_session:
        blockers.append("Proposal session does not match the account snapshot's current session.")
    elif isinstance(allowed,list) and proposal_session not in allowed:
        blockers.append("The current session is not permitted by configured allowed_sessions.")
    else:
        checks.append({"name":"session_permission","status":"passed","session":proposal_session})

    if qty is not None and _number(state.get("open_contracts")) and _number(limits.get("max_contracts")):
        projected=int(state["open_contracts"])+qty
        cap=int(limits["max_contracts"])
        checks.append({"name":"projected_contracts","actual":projected,"limit":cap,
                       "status":"passed" if projected<=cap else "veto"})
        if projected>cap:
            blockers.append("Existing exposure plus proposed quantity exceeds max_contracts.")
    estimated=None
    if point_value is not None and qty is not None and entry is not None and stop is not None:
        estimated=abs(entry-stop)*point_value*qty
        if _number(max_risk) and estimated>float(max_risk):
            blockers.append("Stop-distance risk exceeds max_per_trade_risk.")
        checks.append({"name":"estimated_stop_risk","amount":round(estimated,2),
                       "limit":float(max_risk) if _number(max_risk) else None,
                       "currency":"account_currency","includes_fees_slippage_or_gap_risk":False,
                       "status":"passed" if _number(max_risk) and estimated<=float(max_risk) else "veto"})
    veto=bool(blockers)
    return {**base,"status":"veto" if veto else "eligible_for_human_review","veto":veto,
            "blockers":blockers,"checks":checks,
            "proposal_summary":{"symbol":symbol if point_value is not None else None,
             "side":side if side in {"long","short"} else None,"quantity":qty,
             "estimated_stop_risk":round(estimated,2) if estimated is not None else None},
            "account_protection":{"status":account.get("status"),"breaches":account.get("breaches",[]),
             "warnings":account.get("warnings",[]),"validation_errors":account.get("validation_errors",[])},
            "final_gate":{"master_risk_veto":veto,"human_approval_required":True,
             "trade_approved":False,"order_submission_permitted":False},
            "limitations":["Account state and risk limits are caller-supplied and not broker-authenticated.",
             "Stop-risk estimate excludes commissions, slippage, gaps, and execution uncertainty.",
             "A pass means eligible for separate human review only; it is not trade approval.",
             "No strategy signal, order routing, position management, or broker-side enforcement exists."]}
