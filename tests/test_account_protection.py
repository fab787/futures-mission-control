from mission_control.account_protection import assess_account_protection


def payload():
    return {
        "account_label": "SIM-50K",
        "risk_limits": {
            "max_daily_loss": 500,
            "max_session_loss": 200,
            "max_drawdown": 2000,
            "max_contracts": 10,
            "profit_target": 300,
            "profit_protection_activation": 150,
            "profit_protection_floor": 75,
        },
        "account_state": {
            "daily_pnl": 100,
            "daily_high_water_pnl": 100,
            "session_pnl": 40,
            "drawdown_from_high_water": 100,
            "open_contracts": 1,
            "position_state": "open",
        },
    }


def test_account_protection_reports_within_limits_without_approving_trades():
    result = assess_account_protection(payload())
    assert result["status"] == "within_limits"
    assert result["breaches"] == []
    assert result["new_trade_proposals_allowed"] is True
    assert result["trade_authority"] == "none"
    assert result["risk_settings_modified"] is False
    assert result["provider_requests_triggered"] == 0


def test_account_protection_blocks_daily_and_session_loss_breaches():
    data = payload()
    data["account_state"]["daily_pnl"] = -500
    data["account_state"]["session_pnl"] = -205
    result = assess_account_protection(data)
    assert result["status"] == "breached"
    assert {item["name"] for item in result["breaches"]} >= {"daily_loss", "session_loss"}
    assert result["new_trade_proposals_allowed"] is False


def test_account_protection_warns_at_eighty_percent_utilization():
    data = payload()
    data["account_state"]["daily_pnl"] = -400
    result = assess_account_protection(data)
    assert result["status"] == "warning"
    assert result["warnings"][0]["name"] == "daily_loss"


def test_account_protection_blocks_high_water_drawdown_and_contract_cap():
    data = payload()
    data["account_state"]["drawdown_from_high_water"] = 2001
    data["account_state"]["open_contracts"] = 11
    result = assess_account_protection(data)
    assert result["status"] == "breached"
    assert {item["name"] for item in result["breaches"]} >= {"high_water_drawdown", "open_contracts"}


def test_account_protection_enforces_profit_protection_floor():
    data = payload()
    data["account_state"]["daily_high_water_pnl"] = 200
    data["account_state"]["daily_pnl"] = 60
    result = assess_account_protection(data)
    assert result["status"] == "breached"
    assert any(item["name"] == "profit_protection_floor" for item in result["breaches"])


def test_account_protection_stops_new_proposals_when_profit_target_reached():
    data = payload()
    data["account_state"]["daily_pnl"] = 300
    result = assess_account_protection(data)
    assert result["profit_target_reached"] is True
    assert result["new_trade_proposals_allowed"] is False


def test_account_protection_fails_closed_when_limits_or_state_missing():
    result = assess_account_protection({"risk_limits": {}, "account_state": {}})
    assert result["status"] == "blocked"
    assert result["new_trade_proposals_allowed"] is False
    assert result["validation_errors"]


def test_account_protection_fails_closed_when_position_state_unknown():
    data = payload()
    data["account_state"]["position_state"] = "unknown"
    result = assess_account_protection(data)
    assert result["status"] == "blocked"
    assert result["new_trade_proposals_allowed"] is False


def test_account_protection_rejects_nonfinite_and_negative_limits():
    data = payload()
    data["risk_limits"]["max_daily_loss"] = float("nan")
    data["risk_limits"]["max_contracts"] = -1
    result = assess_account_protection(data)
    assert result["status"] == "blocked"
    assert len(result["validation_errors"]) >= 2


def test_account_protection_detects_inconsistent_position_snapshot():
    data = payload()
    data["account_state"]["position_state"] = "flat"
    result = assess_account_protection(data)
    assert result["status"] == "breached"
    assert any(item["name"] == "position_state_consistency" for item in result["breaches"])



def test_contract_cap_allows_exactly_configured_maximum():
    data = payload()
    data["risk_limits"]["max_contracts"] = 1
    result = assess_account_protection(data)
    assert not any(item["name"] == "open_contracts" for item in result["breaches"])


def test_account_protection_blocks_if_profit_protection_history_is_missing():
    data = payload()
    del data["account_state"]["daily_high_water_pnl"]
    result = assess_account_protection(data)
    assert result["status"] == "blocked"
    assert any("daily_high_water_pnl" in item for item in result["validation_errors"])
