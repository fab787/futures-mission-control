from datetime import datetime, timezone
from mission_control.master_risk_engine import evaluate_master_risk

def payload():
    return {"account_label":"SIM-50K",
      "risk_limits":{"max_daily_loss":500,"max_session_loss":200,"max_drawdown":2000,
        "max_contracts":10,"max_per_trade_risk":150,"allowed_sessions":["NY","GLOBEX"]},
      "account_state":{"snapshot_at":datetime.now(timezone.utc).isoformat(),
        "daily_pnl":20,"session_pnl":10,"drawdown_from_high_water":50,
        "open_contracts":1,"position_state":"open","current_session":"NY"},
      "proposal":{"symbol":"MNQ","side":"long","quantity":1,"entry_price":20000,
        "stop_price":19950,"target_price":20100,"session":"NY"}}

def test_master_risk_passes_only_to_human_review():
    r=evaluate_master_risk(payload())
    assert r["status"]=="eligible_for_human_review" and not r["veto"]
    assert r["proposal_summary"]["estimated_stop_risk"]==100
    assert r["final_gate"]["trade_approved"] is False
    assert r["final_gate"]["human_approval_required"] is True
    assert r["final_gate"]["order_submission_permitted"] is False
    assert r["orders_created"]==0 and r["provider_requests_triggered"]==0

def test_master_risk_vetoes_per_trade_risk_over_limit():
    d=payload(); d["proposal"]["stop_price"]=19900
    r=evaluate_master_risk(d)
    assert r["status"]=="veto" and any("max_per_trade_risk" in x for x in r["blockers"])

def test_master_risk_vetoes_projected_contract_cap():
    d=payload(); d["account_state"]["open_contracts"]=10
    r=evaluate_master_risk(d)
    assert r["status"]=="veto" and any("max_contracts" in x for x in r["blockers"])

def test_master_risk_vetoes_account_loss_breach():
    d=payload(); d["account_state"]["daily_pnl"]=-500
    r=evaluate_master_risk(d)
    assert r["status"]=="veto" and r["account_protection"]["status"]=="breached"

def test_master_risk_vetoes_session_mismatch_and_disallowed_session():
    d=payload(); d["proposal"]["session"]="GLOBEX"
    assert any("does not match" in x for x in evaluate_master_risk(d)["blockers"])
    d=payload(); d["risk_limits"]["allowed_sessions"]=["GLOBEX"]
    assert any("not permitted" in x for x in evaluate_master_risk(d)["blockers"])

def test_master_risk_fails_closed_on_missing_stale_or_invalid_inputs():
    assert evaluate_master_risk({})["status"]=="veto"
    d=payload(); del d["risk_limits"]["max_per_trade_risk"]
    assert evaluate_master_risk(d)["status"]=="veto"
    d=payload(); d["account_state"]["snapshot_at"]="2000-01-01T00:00:00+00:00"
    assert evaluate_master_risk(d)["status"]=="veto"

def test_master_risk_vetoes_wrong_stop_and_target_orientation():
    d=payload(); d["proposal"]["stop_price"]=20010; d["proposal"]["target_price"]=19900
    r=evaluate_master_risk(d)
    assert r["status"]=="veto"
    assert any("Stop-loss" in x for x in r["blockers"])
    assert any("Profit target" in x for x in r["blockers"])

def test_master_risk_rejects_unknown_instrument_and_session():
    d=payload(); d["proposal"]["symbol"]="ES"; d["proposal"]["session"]="unknown"
    r=evaluate_master_risk(d)
    assert r["status"]=="veto"
    assert any("symbol" in x for x in r["blockers"])
    assert any("session" in x for x in r["blockers"])


def test_master_risk_malformed_unhashable_fields_fail_closed():
    d=payload()
    d["proposal"]["symbol"] = {"unexpected": "object"}
    d["proposal"]["side"] = ["long"]
    d["proposal"]["session"] = ["NY"]
    d["account_state"]["current_session"] = {"session": "NY"}
    d["risk_limits"]["allowed_sessions"] = [["NY"]]
    result=evaluate_master_risk(d)
    assert result["status"]=="veto"
    assert result["final_gate"]["trade_approved"] is False


def test_master_risk_requires_saved_profile_for_production_review():
    result = evaluate_master_risk(payload(), require_saved_profile=True)
    assert result["status"] == "veto"
    assert result["veto"] is True
    assert any("saved, verified risk_profile_id is mandatory" in item for item in result["blockers"])
    assert result["final_gate"]["trade_approved"] is False
