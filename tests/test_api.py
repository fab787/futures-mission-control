from datetime import datetime, timezone

from mission_control.main import app
from auth_client import authenticated_client

client = authenticated_client()


def test_multi_account_registry_api_is_local_and_read_only() -> None:
    response = client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    assert "accounts" in payload
    assert payload["provider_requests_triggered"] == 0
    assert payload["broker_connections_triggered"] == 0
    assert payload["execution"] == "disabled"

    missing = client.patch("/api/accounts/not-a-real-account", json={"status": "inactive"})
    assert missing.status_code == 404


def test_health_is_read_only() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "mode": "read-only",
        "execution": "disabled",
    }


def test_initial_instruments_are_configured() -> None:
    response = client.get("/api/instruments")
    assert response.status_code == 200
    symbols = {item["symbol"] for item in response.json()["instruments"]}
    assert symbols == {"MNQ", "MES", "MGC"}


def test_dashboard_serves_workspaces() -> None:
    response = client.get("/")
    assert response.status_code == 200
    for workspace in ("Overview", "Charts", "GEX", "News & Events", "AI Desk", "Risk & Approvals", "Accounts", "Signals", "Integrations", "Expansion"):
        assert workspace in response.text


def test_integrations_are_reported_honestly() -> None:
    response = client.get("/api/integrations")
    assert response.status_code == 200
    integrations = {item["id"]: item for item in response.json()["integrations"]}
    assert isinstance(integrations["tradingview"]["connected"], bool)
    assert integrations["tradingview"]["capabilities"] == ["get_ohlcv", "get_symbol_data_batch"]
    assert integrations["tradingview"]["status"] in {"authorized", "authorization_required"}
    assert integrations["yahoo_finance"]["connected"] is False


def test_news_endpoint_uses_yahoo_adapter_without_live_provider_call(monkeypatch) -> None:
    from mission_control import main

    async def fake_get_news() -> dict:
        return {"status": "fresh", "source": "Yahoo Finance RSS", "items": [], "item_count": 0}

    monkeypatch.setattr(main.yahoo_news_service, "get_news", fake_get_news)
    response = client.get("/api/news")
    assert response.status_code == 200
    assert response.json()["status"] == "fresh"
    assert response.json()["source"] == "Yahoo Finance RSS"


def test_economic_calendar_is_yahoo_link_only_and_does_not_call_tradingview() -> None:
    response = client.get("/api/calendar")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "page_link_only"
    assert payload["source"] == "Yahoo Finance Economic Calendar"
    assert payload["url"] == "https://finance.yahoo.com/calendar/economic/"
    assert payload["items"] == []
    assert "America/New_York" in payload["timezone_note"]


def test_tradingview_adapter_excludes_news_and_economic_calendar_tools() -> None:
    from mission_control.tradingview import READ_ONLY_TOOLS

    assert READ_ONLY_TOOLS == {"get_ohlcv", "get_symbol_data_batch"}


def test_tradingview_status_endpoint_is_read_only() -> None:
    response = client.get("/api/tradingview/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["endpoint"] == "https://mcp.tradingview.com/mcp"
    assert payload["mode"] == "read-only"
    assert payload["authentication"] == "OAuth 2.1"


def test_market_data_rejects_unsupported_interval() -> None:
    response = client.get("/api/market-data?interval=10m")
    assert response.status_code == 422



def test_instrument_agents_endpoint_reads_cache_without_refresh(monkeypatch) -> None:
    from mission_control import main

    calls = {"peek": 0, "refresh": 0}

    def fake_peek(interval: str):
        calls["peek"] += 1
        assert interval == "1m"
        return None

    async def forbidden_refresh(*args, **kwargs):
        calls["refresh"] += 1
        raise AssertionError("Agent endpoint must never refresh provider data")

    monkeypatch.setattr(main.market_data_manager, "peek_snapshot", fake_peek)
    monkeypatch.setattr(main.market_data_manager, "get_snapshot", forbidden_refresh)
    response = client.get("/api/agents/instruments")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert [agent["symbol"] for agent in payload["agents"]] == ["MNQ", "MES", "MGC"]
    assert payload["summary"]["provider_requests_triggered"] == 0
    assert payload["summary"]["trade_proposals_created"] == 0
    assert calls == {"peek": 1, "refresh": 0}



def test_market_structure_endpoint_uses_cache_only(monkeypatch) -> None:
    from mission_control import main

    calls = {"peek": 0, "refresh": 0}

    def fake_peek(interval: str):
        calls["peek"] += 1
        assert interval == "1m"
        return None

    async def forbidden_refresh(*args, **kwargs):
        calls["refresh"] += 1
        raise AssertionError("Market Structure must never refresh provider data")

    monkeypatch.setattr(main.market_data_manager, "peek_snapshot", fake_peek)
    monkeypatch.setattr(main.market_data_manager, "get_snapshot", forbidden_refresh)
    response = client.get("/api/agents/market-structure")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["specialist_id"] == "market_structure"
    assert payload["provider_requests_triggered"] == 0
    assert payload["trade_authority"] == "none"
    assert calls == {"peek": 1, "refresh": 0}



def test_news_catalyst_endpoint_reads_cache_without_rss_fetch(monkeypatch) -> None:
    from mission_control import main

    calls = {"peek": 0, "fetch": 0}

    def fake_peek():
        calls["peek"] += 1
        return {"status": "not_loaded", "items": []}

    async def forbidden_fetch():
        calls["fetch"] += 1
        raise AssertionError("News Catalyst must not fetch Yahoo RSS")

    monkeypatch.setattr(main.yahoo_news_service, "peek_news", fake_peek)
    monkeypatch.setattr(main.yahoo_news_service, "get_news", forbidden_fetch)
    response = client.get("/api/agents/news-catalyst")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["provider_requests_triggered"] == 0
    assert payload["calendar"]["events"] == []
    assert calls == {"peek": 1, "fetch": 0}



def test_opportunity_scout_endpoint_reads_existing_caches_only(monkeypatch) -> None:
    from mission_control import main

    calls = {"market_peek": 0, "market_refresh": 0, "news_peek": 0, "news_refresh": 0}

    def fake_market_peek(interval: str):
        calls["market_peek"] += 1
        assert interval == "1m"
        return None

    async def forbidden_market_refresh(*args, **kwargs):
        calls["market_refresh"] += 1
        raise AssertionError("Opportunity Scout must not refresh TradingView")

    def fake_news_peek():
        calls["news_peek"] += 1
        return {"status": "not_loaded", "items": []}

    async def forbidden_news_refresh():
        calls["news_refresh"] += 1
        raise AssertionError("Opportunity Scout must not refresh Yahoo RSS")

    monkeypatch.setattr(main.market_data_manager, "peek_snapshot", fake_market_peek)
    monkeypatch.setattr(main.market_data_manager, "get_snapshot", forbidden_market_refresh)
    monkeypatch.setattr(main.yahoo_news_service, "peek_news", fake_news_peek)
    monkeypatch.setattr(main.yahoo_news_service, "get_news", forbidden_news_refresh)

    response = client.get("/api/agents/opportunity-scout")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["provider_requests_triggered"] == 0
    assert payload["trade_proposals_created"] == 0
    assert payload["execution"] == "disabled"
    assert payload["risk_authority"] == "none"
    assert calls == {
        "market_peek": 1,
        "market_refresh": 0,
        "news_peek": 1,
        "news_refresh": 0,
    }


def test_sentiment_interface_endpoint_does_not_connect_to_ai_providers() -> None:
    response = client.get("/api/agents/sentiment")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "awaiting_reviews"
    assert payload["comparison_endpoint"] == "POST /api/agents/sentiment/compare"
    assert [provider["id"] for provider in payload["providers"]] == ["claude", "chatgpt"]
    assert all(provider["connected"] is False for provider in payload["providers"])
    assert payload["provider_requests_triggered"] == 0


def test_sentiment_comparison_endpoint_accepts_empty_payload_without_provider_calls() -> None:
    response = client.post("/api/agents/sentiment/compare", json={"assessments": []})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "awaiting_reviews"
    assert payload["instruments"] == []
    assert payload["provider_requests_triggered"] == 0
    assert payload["market_data_requests_triggered"] == 0
    assert payload["trade_authority"] == "none"


def test_account_protection_endpoint_fails_closed_without_account_state() -> None:
    response = client.post("/api/agents/account-protection", json={})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["new_trade_proposals_allowed"] is False
    assert payload["provider_requests_triggered"] == 0
    assert payload["account_connections_triggered"] == 0
    assert payload["risk_settings_modified"] is False


def test_account_protection_endpoint_evaluates_supplied_snapshot_only() -> None:
    response = client.post(
        "/api/agents/account-protection",
        json={
            "risk_limits": {
                "max_daily_loss": 500,
                "max_session_loss": 200,
                "max_drawdown": 2000,
                "max_contracts": 10,
            },
            "account_state": {
                "snapshot_at": datetime.now(timezone.utc).isoformat(),
                "daily_pnl": -500,
                "session_pnl": -10,
                "drawdown_from_high_water": 100,
                "open_contracts": 0,
                "position_state": "flat",
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "breached"
    assert payload["new_trade_proposals_allowed"] is False
    assert any(item["name"] == "daily_loss" for item in payload["breaches"])


def test_chief_decision_endpoint_uses_only_existing_caches(monkeypatch) -> None:
    from mission_control import main

    calls = {"market_peek": 0, "market_refresh": 0, "news_peek": 0, "news_refresh": 0}

    def fake_market_peek(interval: str):
        calls["market_peek"] += 1
        assert interval == "1m"
        return None

    async def forbidden_market_refresh(*args, **kwargs):
        calls["market_refresh"] += 1
        raise AssertionError("Chief Decision Agent must not refresh TradingView")

    def fake_news_peek():
        calls["news_peek"] += 1
        return {"status": "not_loaded", "items": []}

    async def forbidden_news_refresh():
        calls["news_refresh"] += 1
        raise AssertionError("Chief Decision Agent must not refresh Yahoo RSS")

    monkeypatch.setattr(main.market_data_manager, "peek_snapshot", fake_market_peek)
    monkeypatch.setattr(main.market_data_manager, "get_snapshot", forbidden_market_refresh)
    monkeypatch.setattr(main.yahoo_news_service, "peek_news", fake_news_peek)
    monkeypatch.setattr(main.yahoo_news_service, "get_news", forbidden_news_refresh)

    response = client.post("/api/agents/chief-decision", json={})
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "blocked"
    assert result["decision"]["trade_proposals_created"] == 0
    assert result["provider_requests_triggered"] == 0
    assert result["decision"]["trade_authority"] == "none"
    assert calls == {"market_peek": 1, "market_refresh": 0, "news_peek": 1, "news_refresh": 0}


def test_master_risk_endpoint_is_independent_pure_veto(monkeypatch) -> None:
    from mission_control import main
    def forbidden(*args, **kwargs):
        raise AssertionError("Master Risk Engine must not access providers")
    monkeypatch.setattr(main.market_data_manager, "get_snapshot", forbidden)
    monkeypatch.setattr(main.yahoo_news_service, "get_news", forbidden)
    response = client.post("/api/risk/master", json={})
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "veto"
    assert result["final_gate"]["master_risk_veto"] is True
    assert result["final_gate"]["trade_approved"] is False
    assert result["orders_created"] == 0
    assert result["provider_requests_triggered"] == 0


def test_approval_api_never_creates_orders(tmp_path, monkeypatch) -> None:
    from mission_control import audit_ledger, main
    monkeypatch.setattr(audit_ledger, "DEFAULT_DB", tmp_path / "api-audit.sqlite3")
    response = client.post("/api/approvals/proposals", json={"submitted_by":"tester"})
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "vetoed"
    assert result["orders_created"] == 0
    assert client.get("/api/audit/events").status_code == 200



def test_chief_decision_blocks_account_warning_before_risk_engine(monkeypatch) -> None:
    from mission_control import main

    def fake_peek(interval: str):
        return None

    def fake_news():
        return {"status": "not_loaded", "items": []}

    monkeypatch.setattr(main.market_data_manager, "peek_snapshot", fake_peek)
    monkeypatch.setattr(main.yahoo_news_service, "peek_news", fake_news)
    response = client.post(
        "/api/agents/chief-decision",
        json={
            "account_protection": {
                "risk_limits": {},
                "account_state": {},
            }
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "blocked"
    assert result["downstream_gate"]["inputs_ready_for_master_risk_engine"] is False
    assert result["decision"]["orders_created"] == 0
    assert result["provider_requests_triggered"] == 0


def test_master_risk_api_rejects_caller_limits_without_saved_profile():
    response = client.post("/api/risk/master", json={
        "risk_limits": {"max_daily_loss": 500, "max_session_loss": 200,
            "max_drawdown": 2000, "max_contracts": 10,
            "max_per_trade_risk": 150, "allowed_sessions": ["NY"]},
        "account_state": {"snapshot_at": datetime.now(timezone.utc).isoformat(),
            "daily_pnl": 0, "session_pnl": 0, "drawdown_from_high_water": 0,
            "open_contracts": 0, "position_state": "flat", "current_session": "NY"},
        "proposal": {"symbol": "MNQ", "side": "long", "quantity": 1,
            "entry_price": 20000, "stop_price": 19950, "target_price": 20100, "session": "NY"}
    })
    assert response.status_code == 200
    result = response.json()
    assert result["veto"] is True
    assert any("saved, verified risk_profile_id is mandatory" in item for item in result["blockers"])
    assert result["orders_created"] == 0

def test_account_history_api_saves_and_returns_immutable_snapshot(tmp_path, monkeypatch):
    from mission_control import account_history, risk_config
    monkeypatch.setattr(account_history, "DEFAULT_DB", tmp_path / "history.sqlite3")
    monkeypatch.setattr(risk_config, "DB", tmp_path / "profiles.sqlite3")
    profile = risk_config.create_profile({"profile_name": "History API fixture", "prop_firm": "Synthetic",
        "account_mode": "evaluation", "drawdown_type": "static", "verification_status": "user_verified",
        "rule_coverage_status": "complete", "evaluation_rules": {"minimum_trading_days": None},
        "consistency_rule": None, "prop_firm_maximums": {"max_daily_loss": 500, "max_session_loss": 200,
        "max_drawdown": 2000, "max_contracts": 10, "max_per_trade_risk": 150, "allowed_sessions": ["NY"]},
        "user_limits": {}}, "api-test")
    now = datetime.now(timezone.utc)
    payload = {
        "risk_profile_id": profile["profile_id"],
        "account_history": {
            "starting_balance": 50000,
            "equity_observations": [
                {"timestamp": (now - __import__("datetime").timedelta(minutes=2)).isoformat(),
                 "equity": 50000, "observation_type": "intraday"},
                {"timestamp": (now - __import__("datetime").timedelta(minutes=1)).isoformat(),
                 "equity": 50010, "observation_type": "intraday"},
            ],
            "daily_results": [{"date": now.date().isoformat(), "net_pnl": 10, "trading_day": True}],
            "session_results": [{"date": now.date().isoformat(), "session": "NY", "net_pnl": 5}],
        },
        "actor": "api-test",
        "source_label": "Synthetic API fixture",
    }
    saved = client.post("/api/account-history", json=payload)
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    current = client.get("/api/account-history", params={"risk_profile_id": profile["profile_id"]}).json()
    assert current["status"] == "available"
    assert current["summary"]["cumulative_net_profit"] == 10
    assert current["summary"]["history_is_broker_authenticated"] is False
    assert client.get("/api/account-history/versions", params={"risk_profile_id": profile["profile_id"]}).json()["versions"][0]["revision"] == 1


def test_account_history_api_rejects_naive_timestamps(tmp_path, monkeypatch):
    from mission_control import account_history, risk_config
    monkeypatch.setattr(account_history, "DEFAULT_DB", tmp_path / "history-invalid.sqlite3")
    monkeypatch.setattr(risk_config, "DB", tmp_path / "profiles-invalid.sqlite3")
    profile = risk_config.create_profile({"profile_name": "Invalid History fixture", "prop_firm": "Synthetic",
        "account_mode": "evaluation", "drawdown_type": "static", "verification_status": "user_verified",
        "rule_coverage_status": "complete", "evaluation_rules": {"minimum_trading_days": None},
        "consistency_rule": None, "prop_firm_maximums": {"max_daily_loss": 500, "max_session_loss": 200,
        "max_drawdown": 2000, "max_contracts": 10, "max_per_trade_risk": 150, "allowed_sessions": ["NY"]},
        "user_limits": {}}, "api-test")
    now = datetime.now(timezone.utc)
    payload = {
        "risk_profile_id": profile["profile_id"],
        "account_history": {
            "starting_balance": 50000,
            "equity_observations": [
                {"timestamp": now.replace(tzinfo=None).isoformat(), "equity": 50000, "observation_type": "intraday"},
                {"timestamp": now.isoformat(), "equity": 50010, "observation_type": "intraday"},
            ],
            "daily_results": [{"date": now.date().isoformat(), "net_pnl": 10, "trading_day": True}],
            "session_results": [{"date": now.date().isoformat(), "session": "NY", "net_pnl": 5}],
        },
        "actor": "api-test",
        "source_label": "Invalid fixture",
    }
    assert client.post("/api/account-history", json=payload).status_code == 400



def test_tradingview_webhook_inbox_api_is_read_only(monkeypatch) -> None:
    from mission_control import main

    monkeypatch.setattr(
        main,
        "list_tradingview_webhook_signals",
        lambda limit=50: {
            "signals": [],
            "count": 0,
            "execution_enabled": False,
            "human_approval_required": True,
        },
    )
    response = client.get("/api/webhooks/tradingview/inbox")
    assert response.status_code == 200
    payload = response.json()
    assert payload["signals"] == []
    assert payload["execution_enabled"] is False
    assert payload["human_approval_required"] is True

    invalid = client.get("/api/webhooks/tradingview/inbox?limit=500")
    assert invalid.status_code == 422



def test_signal_workspace_is_read_only_and_uses_local_inbox_api() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="signals"' in response.text
    assert 'fetch("/api/webhooks/tradingview/inbox?limit=100")' in response.text
    assert "not verified or authorized" in response.text



def test_webhook_alert_creates_account_specific_risk_proposal_not_order(monkeypatch) -> None:
    from datetime import datetime, timezone, timedelta
    from mission_control import main

    now = datetime.now(timezone.utc)
    signal = {
        "alert_id": "tv-test-account-route",
        "event_timestamp": (now - timedelta(seconds=5)).isoformat(),
        "symbol": "MNQ",
        "action": "buy",
        "quantity": 2,
        "entry_price": 20000.0,
        "stop_loss": 19990.0,
        "take_profit": 20020.0,
        "strategy_id": "test-strategy",
        "route_key": "eval-a",
        "status": "received",
    }
    account = {
        "account_id": "registered-account-1",
        "nickname": "Phidias Eval A",
        "firm": "Phidias",
        "account_mode": "evaluation",
        "status": "active",
        "risk_profile_id": "risk-profile-1",
    }
    captured = {}
    monkeypatch.setattr(main, "get_tradingview_signal", lambda alert_id: signal)
    monkeypatch.setattr(main, "list_accounts", lambda include_inactive=True: {"accounts": [account]})
    monkeypatch.setattr(main, "claim_signal_for_review", lambda alert_id: True)
    monkeypatch.setattr(main, "set_signal_status", lambda alert_id, status: captured.update(status=status))

    def fake_create_proposal(payload, actor):
        captured["payload"] = payload
        captured["actor"] = actor
        return {"proposal_id": "proposal-1", "status": "pending", "execution": "disabled", "orders_created": 0}

    monkeypatch.setattr(main, "create_proposal", fake_create_proposal)
    response = client.post(
        "/api/approvals/proposals/from-webhook/tv-test-account-route",
        json={
            "account_id": "registered-account-1",
            "submitted_by": "operator-test",
            "account_state": {
                "snapshot_at": now.isoformat(),
                "current_session": "NY",
                "daily_pnl": 100,
                "session_pnl": 50,
                "drawdown_from_high_water": 0,
                "open_contracts": 0,
                "position_state": "flat",
            },
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["proposal_id"] == "proposal-1"
    assert result["selected_account"]["account_id"] == "registered-account-1"
    assert result["route_key_used_for_authorization"] is False
    assert result["human_approval_required"] is True
    assert result["execution"] == "disabled"
    assert result["orders_created"] == 0
    assert captured["payload"]["risk_profile_id"] == "risk-profile-1"
    assert captured["payload"]["proposal"]["side"] == "long"
    assert captured["payload"]["proposal"]["session"] == "NY"
    assert captured["status"] == "proposal_created"


def test_webhook_close_alert_cannot_create_entry_proposal(monkeypatch) -> None:
    from datetime import datetime, timezone
    from mission_control import main

    signal = {
        "alert_id": "tv-close-only",
        "event_timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": "MES",
        "action": "close",
        "status": "received",
    }
    monkeypatch.setattr(main, "get_tradingview_signal", lambda alert_id: signal)
    response = client.post(
        "/api/approvals/proposals/from-webhook/tv-close-only",
        json={"account_id": "registered-account-1", "account_state": {}},
    )
    assert response.status_code == 400
    assert "remain review-only" in response.json()["detail"]


def test_webhook_alert_requires_active_registered_account(monkeypatch) -> None:
    from datetime import datetime, timezone
    from mission_control import main

    signal = {
        "alert_id": "tv-inactive-account",
        "event_timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": "MES",
        "action": "sell",
        "quantity": 1,
        "entry_price": 5000.0,
        "stop_loss": 5010.0,
        "take_profit": 4980.0,
        "strategy_id": "test",
        "status": "received",
    }
    monkeypatch.setattr(main, "get_tradingview_signal", lambda alert_id: signal)
    monkeypatch.setattr(main, "list_accounts", lambda include_inactive=True: {
        "accounts": [{
            "account_id": "inactive-account", "status": "inactive",
            "risk_profile_id": "profile-x", "nickname": "Inactive",
            "firm": "Lucid", "account_mode": "funded",
        }]
    })
    response = client.post(
        "/api/approvals/proposals/from-webhook/tv-inactive-account",
        json={"account_id": "inactive-account", "account_state": {}},
    )
    assert response.status_code == 409
    assert "inactive or completed" in response.json()["detail"]



def test_signals_workspace_exposes_explicit_account_snapshot_controls() -> None:
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    for control in (
        'id="tv-route-account"',
        'id="tv-snapshot-at"',
        'id="tv-current-session"',
        'id="tv-daily-pnl"',
        'id="tv-session-pnl"',
        'id="tv-drawdown"',
        'id="tv-open-contracts"',
        'id="tv-position-state"',
    ):
        assert control in html
    assert "Submit to Master Risk Engine" in html
    assert "Alias is informational only" in html


def test_webhook_alert_rejects_stale_signal_before_claiming(monkeypatch) -> None:
    from datetime import datetime, timezone, timedelta
    from mission_control import main

    signal = {
        "alert_id": "tv-stale",
        "event_timestamp": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
        "symbol": "MNQ", "action": "buy", "status": "received",
    }
    calls = {"claim": 0}
    monkeypatch.setattr(main, "get_tradingview_signal", lambda alert_id: signal)
    monkeypatch.setattr(main, "claim_signal_for_review", lambda alert_id: calls.update(claim=calls["claim"] + 1) or True)
    response = client.post(
        "/api/approvals/proposals/from-webhook/tv-stale",
        json={"account_id": "some-account", "account_state": {}},
    )
    assert response.status_code == 409
    assert "stale" in response.json()["detail"]
    assert calls["claim"] == 0



def test_broker_capability_api_lists_and_updates_per_account(monkeypatch) -> None:
    from mission_control import main

    monkeypatch.setattr(main, "list_broker_capabilities", lambda: {
        "capabilities": [{"account_id": "acct-1", "permission_status": "unverified"}],
        "execution_enabled": False,
        "orders_created": 0,
    })
    listed = client.get("/api/broker-capabilities")
    assert listed.status_code == 200
    assert listed.json()["capabilities"][0]["permission_status"] == "unverified"
    assert listed.json()["execution_enabled"] is False

    captured = {}
    def fake_upsert(account_id, payload, actor):
        captured.update(account_id=account_id, payload=payload, actor=actor)
        return {"account_id": account_id, "permission_status": payload["permission_status"],
                "execution_enabled": False, "orders_created": 0}
    monkeypatch.setattr(main, "upsert_broker_capability", fake_upsert)
    updated = client.put(
        "/api/accounts/acct-1/broker-capability",
        json={"connection_provider": "tradovate", "platform": "tradingview",
              "integration_method": "manual_only", "permission_status": "unverified"},
    )
    assert updated.status_code == 200
    assert captured["account_id"] == "acct-1"
    assert captured["actor"] == "local_operator"
    assert updated.json()["execution_enabled"] is False


def test_broker_capability_api_rejects_unknown_account(monkeypatch) -> None:
    from mission_control import main

    def missing(*args, **kwargs):
        raise KeyError("Registered account not found.")
    monkeypatch.setattr(main, "upsert_broker_capability", missing)
    response = client.put("/api/accounts/missing/broker-capability", json={})
    assert response.status_code == 404



def test_accounts_workspace_renders_broker_capability_registry_controls() -> None:
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert 'id="broker-capability-panel"' in html
    assert 'id="broker-capability-refresh"' in html
    assert 'id="broker-capability-list"' in html
    assert "async function loadBrokerCapabilities()" in html
    assert "/api/broker-capabilities" in html
    assert "/broker-capability" in html
    assert "This registry does not connect or execute trades." in html


def test_market_data_refresh_flag_forces_selected_provider_request(monkeypatch):
    from mission_control import main

    calls = []

    async def authorized() -> bool:
        return True

    async def fake_get_snapshot(interval, loader, *, force_refresh=False):
        calls.append((interval, force_refresh))
        return {
            "source": "test provider",
            "instruments": [],
            "fetched_at": "2026-11-15T17:00:00+00:00",
            "cache": {"status": "miss", "age_seconds": 0},
        }

    monkeypatch.setattr(main, "tradingview_authorized", authorized)
    monkeypatch.setattr(main.market_data_manager, "get_snapshot", fake_get_snapshot)
    response = client.get("/api/market-data?provider=tradingview&interval=1m&refresh=true")
    assert response.status_code == 200
    assert calls == [("1m", True)]



def test_opportunity_scout_can_refresh_the_selected_webull_provider(monkeypatch) -> None:
    from mission_control import main

    calls = {"count": 0}

    async def authorized() -> bool:
        return True

    async def fake_snapshot(interval, loader, *, force_refresh=False):
        calls["count"] += 1
        assert interval == "1m"
        assert loader is main.webull_market_snapshot
        assert force_refresh is False
        return {
            "source": "Webull Cloud MCP",
            "instruments": [],
            "cache": {"status": "miss"},
        }

    monkeypatch.setattr(main, "webull_authorized", authorized)
    monkeypatch.setattr(main.webull_market_data_manager, "get_snapshot", fake_snapshot)

    response = client.get("/api/agents/opportunity-scout?provider=webull&refresh=true")
    assert response.status_code == 200
    payload = response.json()
    assert payload["market_data_provider"] == "webull"
    assert payload["market_data_status"] == "miss"
    assert calls["count"] == 1
    assert payload["trade_proposals_created"] == 0
    assert payload["execution"] == "disabled"
