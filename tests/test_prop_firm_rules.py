from datetime import datetime, timedelta, timezone

from mission_control.prop_firm_rules import evaluate_prop_firm_rules


def sample_profile(drawdown_type="trailing"):
    return {
        "verification_status": "user_verified",
        "rule_coverage_status": "complete",
        "drawdown_type": drawdown_type,
        "effective_limits": {
            "max_daily_loss": 500, "max_session_loss": 250,
            "max_drawdown": 2000, "profit_target": 3000,
        },
        "evaluation_rules": {"minimum_trading_days": 5},
        "consistency_rule": {"max_best_day_profit_share_pct": 50},
    }


def history():
    now = datetime.now(timezone.utc)
    return {
        "starting_balance": 50000,
        "equity_observations": [
            {"timestamp": (now - timedelta(days=2)).isoformat(), "equity": 50000, "observation_type": "intraday"},
            {"timestamp": (now - timedelta(days=1)).isoformat(), "equity": 51000, "observation_type": "intraday"},
            {"timestamp": now.isoformat(), "equity": 50500, "observation_type": "intraday"},
        ],
        "daily_results": [
            {"date": f"2026-11-{day:02d}", "net_pnl": pnl, "trading_day": True}
            for day, pnl in [(10, 100), (11, 100), (12, 100), (13, 100), (14, 100)]
        ],
        "session_results": [
            {"date": "2026-11-14", "session": "NY", "net_pnl": 100},
            {"date": "2026-11-14", "session": "GLOBEX", "net_pnl": 0},
        ],
    }


def state():
    return {"cumulative_net_profit": 500}


def test_rules_evaluator_passes_complete_consistent_history():
    result = evaluate_prop_firm_rules(sample_profile(), history(), state())
    assert result["status"] == "within_rules"
    assert result["new_trade_proposals_allowed"] is True
    assert result["provider_requests_triggered"] == 0
    assert result["orders_created"] == 0
    assert {item["name"] for item in result["checks"]} >= {
        "drawdown_method", "historical_daily_loss", "historical_session_loss",
        "minimum_trading_days", "best_day_profit_share", "profit_target",
    }


def test_rules_evaluator_distinguishes_trailing_eod_and_static_drawdown():
    data = history()
    data["equity_observations"] = [
        {"timestamp": "2026-11-10T15:00:00+00:00", "equity": 50000, "observation_type": "intraday"},
        {"timestamp": "2026-11-10T16:00:00+00:00", "equity": 52000, "observation_type": "intraday"},
        {"timestamp": "2026-11-10T17:00:00+00:00", "equity": 50500, "observation_type": "end_of_day"},
        {"timestamp": "2026-11-11T17:00:00+00:00", "equity": 51900, "observation_type": "end_of_day"},
    ]
    trailing = evaluate_prop_firm_rules(sample_profile("trailing"), data, state())
    eod = evaluate_prop_firm_rules(sample_profile("end_of_day"), data, state())
    static = evaluate_prop_firm_rules(sample_profile("static"), data, state())
    assert next(x for x in trailing["checks"] if x["name"] == "drawdown_method")["measured"] == 1500
    assert next(x for x in eod["checks"] if x["name"] == "drawdown_method")["measured"] == 0
    assert next(x for x in static["checks"] if x["name"] == "drawdown_method")["measured"] == 0


def test_rules_evaluator_vetoes_best_day_consistency_violation():
    data = history()
    data["daily_results"] = [
        {"date": "2026-11-10", "net_pnl": 700, "trading_day": True},
        {"date": "2026-11-11", "net_pnl": 100, "trading_day": True},
        {"date": "2026-11-12", "net_pnl": 100, "trading_day": True},
    ]
    result = evaluate_prop_firm_rules(sample_profile(), data, state())
    assert result["status"] == "in_progress"
    assert result["new_trade_proposals_allowed"] is True
    assert any("consistency threshold" in item for item in result["progress_notes"])


def test_rules_evaluator_blocks_missing_history_and_incomplete_rules():
    profile = sample_profile()
    profile["rule_coverage_status"] = "incomplete"
    result = evaluate_prop_firm_rules(profile, {}, {})
    assert result["status"] == "blocked"
    assert not result["new_trade_proposals_allowed"]
    assert any("rule coverage" in item for item in result["blockers"])
    assert any("Equity history" in item for item in result["blockers"])


def test_rules_evaluator_vetoes_historical_daily_and_session_losses():
    data = history()
    data["daily_results"][0]["net_pnl"] = -600
    data["session_results"][0]["net_pnl"] = -300
    result = evaluate_prop_firm_rules(sample_profile(), data, state())
    assert result["status"] == "blocked"
    assert any("daily-loss" in item for item in result["blockers"])
    assert any("session-loss" in item for item in result["blockers"])


def test_rules_evaluator_requires_history_for_profit_target():
    profile = sample_profile()
    result = evaluate_prop_firm_rules(profile, history(), {})
    assert result["status"] == "blocked"
    assert any("cumulative_net_profit" in item for item in result["blockers"])

def test_minimum_trading_days_is_progress_not_a_permanent_breach():
    profile = sample_profile()
    profile["evaluation_rules"]["minimum_trading_days"] = 10
    result = evaluate_prop_firm_rules(profile, history(), state())
    assert result["status"] == "in_progress"
    assert result["new_trade_proposals_allowed"] is True
    assert any("minimum trading-day" in item for item in result["progress_notes"])
