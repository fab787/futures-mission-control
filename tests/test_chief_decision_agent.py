from mission_control.chief_decision_agent import compose_chief_decision


def ready_inputs():
    return (
        {"status": "ready", "agents": []},
        {"status": "ready"},
        {"status": "ready", "ranked_instruments": [{"symbol": "MNQ", "context_score": 50}]},
        {"status": "ready", "calendar": {"status": "page_link_only"}},
        {"status": "ready", "instruments": [{"symbol": "MNQ", "status": "disagreement"}]},
        {"status": "within_limits", "breaches": [], "warnings": [], "new_trade_proposals_allowed": True},
    )


def test_chief_decision_coordinates_without_trade_authority():
    result = compose_chief_decision(*ready_inputs())
    assert result["status"] == "warning"
    assert result["downstream_gate"]["inputs_ready_for_master_risk_engine"] is True
    assert result["downstream_gate"]["master_risk_engine_review_required"] is True
    assert result["downstream_gate"]["human_approval_required"] is True
    assert result["downstream_gate"]["this_is_trade_approval"] is False
    assert result["decision"]["outcome"] == "no_trade_decision"
    assert result["decision"]["trade_proposals_created"] == 0
    assert result["decision"]["orders_created"] == 0
    assert result["decision"]["trade_authority"] == "none"
    assert any("disagree" in warning for warning in result["warnings"])


def test_chief_decision_blocks_missing_or_breached_account_protection():
    inputs = list(ready_inputs())
    inputs[5] = {"status": "breached", "breaches": [{"name": "daily_loss"}]}
    result = compose_chief_decision(*inputs)
    assert result["status"] == "blocked"
    assert result["downstream_gate"]["inputs_ready_for_master_risk_engine"] is False
    assert result["decision"]["trade_authority"] == "none"


def test_chief_decision_blocks_unavailable_market_context():
    inputs = list(ready_inputs())
    inputs[0] = {"status": "blocked"}
    inputs[1] = {"status": "blocked"}
    inputs[2] = {"status": "blocked"}
    result = compose_chief_decision(*inputs)
    assert result["status"] == "blocked"
    assert len(result["blocking_conditions"]) >= 3


def test_chief_decision_warning_does_not_hide_risk_warnings():
    inputs = list(ready_inputs())
    inputs[5] = {"status": "warning", "breaches": [], "warnings": [{"name": "daily_loss"}],
                 "new_trade_proposals_allowed": True}
    result = compose_chief_decision(*inputs)
    assert result["status"] == "warning"
    assert result["account_protection"]["warnings"] == [{"name": "daily_loss"}]
