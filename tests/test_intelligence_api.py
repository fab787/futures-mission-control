from auth_client import authenticated_client

client = authenticated_client()


def test_webull_news_endpoint_uses_service(monkeypatch):
    from mission_control import main
    async def fake():
        return {"status": "fresh", "source": "Webull Cloud MCP", "items": [], "item_count": 0}
    monkeypatch.setattr(main.webull_news_service, "get_news", fake)
    response = client.get("/api/news/webull")
    assert response.status_code == 200
    assert response.json()["source"] == "Webull Cloud MCP"


def test_news_intelligence_endpoint_is_cache_only(monkeypatch):
    from mission_control import main
    calls = {"yahoo": 0, "webull": 0}
    def yahoo():
        calls["yahoo"] += 1
        return {"status": "not_loaded", "items": []}
    def webull():
        calls["webull"] += 1
        return {"status": "not_loaded", "items": []}
    monkeypatch.setattr(main.yahoo_news_service, "peek_news", yahoo)
    monkeypatch.setattr(main.webull_news_service, "peek_news", webull)
    response = client.get("/api/news/intelligence")
    assert response.status_code == 200
    assert response.json()["provider_requests_triggered"] == 0
    assert calls == {"yahoo": 1, "webull": 1}


def test_webull_calendar_endpoint_uses_calendar_service(monkeypatch):
    from mission_control import main
    async def fake(**kwargs):
        return {"status": "fresh", "source": "Webull US Macroeconomic Calendar", "events": [], "event_count": 0}
    monkeypatch.setattr(main.webull_calendar_service, "get_calendar", fake)
    response = client.get("/api/calendar/events")
    assert response.status_code == 200
    assert response.json()["source"] == "Webull US Macroeconomic Calendar"


def test_gex_snapshot_endpoint_does_not_fetch_chain(monkeypatch):
    from mission_control import main
    monkeypatch.setattr(main.market_data_manager, "peek_snapshot", lambda _interval: None)
    response = client.get("/api/gex/snapshot")
    assert response.status_code == 200
    assert response.json()["status"] == "not_loaded"
