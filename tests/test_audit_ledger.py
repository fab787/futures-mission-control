from datetime import datetime, timedelta, timezone
import sqlite3
import pytest
from mission_control.audit_ledger import create_proposal, decide_proposal, list_audit_events, list_proposals
from mission_control import risk_config

@pytest.fixture
def saved_risk_profile(tmp_path, monkeypatch):
    profile_db = tmp_path / "risk_profiles.sqlite3"
    monkeypatch.setattr(risk_config, "DB", profile_db)
    created = risk_config.create_profile({
        "profile_name": "Audit-ledger CI fixture",
        "prop_firm": "Synthetic test fixture",
        "account_mode": "evaluation",
        "drawdown_type": "static",
        "verification_status": "user_verified",
        "rule_coverage_status": "complete",
        "evaluation_rules": {"minimum_trading_days": None},
        "prop_firm_maximums": {
            "max_daily_loss": 500, "max_session_loss": 200,
            "max_drawdown": 2000, "max_contracts": 10,
            "max_per_trade_risk": 150, "allowed_sessions": ["NY"],
        },
        "user_limits": {},
    }, "CI test fixture")
    return created["profile_id"]

def valid_payload(risk_profile_id):
    now = datetime.now(timezone.utc)
    return {
      "account_label": "SIM",
      "risk_profile_id": risk_profile_id,
      "risk_limits": {"max_daily_loss":500,"max_session_loss":200,
        "max_drawdown":2000,"max_contracts":10,"max_per_trade_risk":150,
        "allowed_sessions":["NY"]},
      "account_history": {
        "starting_balance": 50000,
        "equity_observations": [
          {"timestamp": (now - timedelta(minutes=2)).isoformat(),
           "equity": 50000, "observation_type": "intraday"},
          {"timestamp": (now - timedelta(minutes=1)).isoformat(),
           "equity": 50010, "observation_type": "intraday"},
        ],
        "daily_results": [{"date": now.date().isoformat(), "net_pnl": 10,
                           "trading_day": True}],
        "session_results": [{"date": now.date().isoformat(), "session": "NY",
                             "net_pnl": 5}],
      },
      "account_state": {"snapshot_at":now.isoformat(),"daily_pnl":10,
        "session_pnl":5,"drawdown_from_high_water":10,"open_contracts":0,
        "position_state":"flat","current_session":"NY"},
      "proposal": {"symbol":"MNQ","side":"long","quantity":1,"entry_price":20000,
        "stop_price":19950,"target_price":20100,"session":"NY"},
    }

def test_proposal_requires_fresh_risk_pass_and_records_audit(tmp_path, saved_risk_profile):
    db=tmp_path/"audit.sqlite3"
    result=create_proposal(valid_payload(saved_risk_profile),"operator",db)
    assert result["status"]=="pending" and result["orders_created"]==0
    events=list_audit_events(result["proposal_id"],db)["events"]
    assert len(events)==1 and events[0]["event_type"]=="proposal_submitted"

def test_vetoed_proposal_cannot_be_approved(tmp_path, saved_risk_profile):
    data=valid_payload(saved_risk_profile); data["proposal"]["stop_price"]=19900
    db=tmp_path/"audit.sqlite3"
    result=create_proposal(data,"operator",db)
    assert result["status"]=="vetoed"
    with pytest.raises(ValueError):
        decide_proposal(result["proposal_id"],"approve","operator","try",db)

def test_approval_and_rejection_are_single_use_decisions(tmp_path, saved_risk_profile):
    db=tmp_path/"audit.sqlite3"
    p=create_proposal(valid_payload(saved_risk_profile),"operator",db)
    result=decide_proposal(p["proposal_id"],"approve","human reviewer","Reviewed",db)
    assert result["status"]=="approved" and result["orders_created"]==0
    assert result["execution"]=="disabled" and result["decision_recorded"]
    with pytest.raises(ValueError):
        decide_proposal(p["proposal_id"],"reject","human reviewer","second decision",db)
    assert [x["event_type"] for x in list_audit_events(p["proposal_id"],db)["events"]]==[
      "proposal_submitted","human_approval_recorded"]

def test_risk_is_rechecked_at_decision_time_and_can_veto(tmp_path, saved_risk_profile):
    db=tmp_path/"audit.sqlite3"
    p=create_proposal(valid_payload(saved_risk_profile),"operator",db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE proposals SET payload_json=? WHERE proposal_id=?",
          ('{"invalid":true}',p["proposal_id"]))
    result=decide_proposal(p["proposal_id"],"approve","reviewer","recheck",db)
    assert result["status"]=="vetoed" and result["decision_recorded"] is False

def test_audit_events_are_append_only(tmp_path):
    db=tmp_path/"audit.sqlite3"
    p=create_proposal(valid_payload(saved_risk_profile),"operator",db)
    with sqlite3.connect(db) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE audit_events SET actor='tampered'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM audit_events")

def test_list_proposals_includes_trade_details_for_human_review(tmp_path):
    from mission_control.audit_ledger import list_proposals
    payload = {
        "risk_limits": {},
        "account_state": {},
        "proposal": {
            "symbol": "MNQ", "side": "long", "quantity": 2,
            "entry_price": 20000, "stop_price": 19950,
            "target_price": 20100, "session": "NY",
        },
    }
    created = create_proposal(payload, "operator", tmp_path / "audit.sqlite3")
    item = list_proposals(tmp_path / "audit.sqlite3")["proposals"][0]
    assert item["proposal"]["symbol"] == "MNQ"
    assert item["proposal"]["quantity"] == 2
    assert item["proposal"]["entry_price"] == 20000
    assert item["proposal"]["stop_price"] == 19950
    assert item["proposal"]["target_price"] == 20100
    assert created["orders_created"] == 0
