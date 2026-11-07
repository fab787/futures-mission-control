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
    assert integrations["tradingview"]["connected"] is False
    assert integrations["yahoo_finance"]["connected"] is False


def test_news_does_not_fabricate_headlines() -> None:
    response = client.get("/api/news")
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["status"] == "not_connected"
