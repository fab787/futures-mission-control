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
    for workspace in ("Overview", "Charts", "News & Events", "AI Desk", "Risk & Approvals", "Integrations", "Expansion"):
        assert workspace in response.text


def test_integrations_are_reported_honestly() -> None:
    response = client.get("/api/integrations")
    assert response.status_code == 200
    integrations = {item["id"]: item for item in response.json()["integrations"]}
    assert isinstance(integrations["tradingview"]["connected"], bool)
    assert integrations["tradingview"]["status"] in {"authorized", "authorization_required"}
    assert integrations["yahoo_finance"]["connected"] is False


def test_news_does_not_fabricate_headlines() -> None:
    response = client.get("/api/news")
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["status"] == "not_connected"


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
