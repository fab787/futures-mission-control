from fastapi.testclient import TestClient

from mission_control.main import app

client = TestClient(app)


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
    for workspace in ("Overview", "Charts", "GEX", "News & Events", "AI Desk", "Risk & Approvals", "Integrations", "Expansion"):
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
