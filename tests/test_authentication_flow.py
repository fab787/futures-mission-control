from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mission_control import local_auth
from mission_control.main import app


@pytest.fixture
def isolated_auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep authentication records and signing keys inside the test directory."""
    monkeypatch.setattr(local_auth, "USER_FILE", tmp_path / "local_auth.json")
    monkeypatch.setattr(local_auth, "SECRET_FILE", tmp_path / ".local_auth_session.key")
    keyring_values: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(
        local_auth.keyring,
        "get_password",
        lambda service, key: keyring_values.get((service, key)),
    )
    monkeypatch.setattr(
        local_auth.keyring,
        "set_password",
        lambda service, key, value: keyring_values.__setitem__((service, key), value),
    )


def test_first_run_redirects_to_setup_and_setup_authenticates(
    isolated_auth: None,
) -> None:
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.url.path == "/login"
        assert 'id="username"' in response.text
        assert 'id="password"' in response.text
        assert "Create your administrator" in response.text

        setup = client.post(
            "/api/auth/setup",
            json={"username": "local_admin", "password": "Strong-test-password-123"},
        )
        assert setup.status_code == 200
        assert setup.json()["ok"] is True
        assert setup.cookies.get("fq_session")
        assert local_auth.is_configured()

        status = client.get("/api/auth/status")
        assert status.status_code == 200
        assert status.json()["authenticated"] is True
        assert status.json()["username"] == "local_admin"

        dashboard = client.get("/", follow_redirects=False)
        assert dashboard.status_code == 200
        assert "FAB787 QUANTUM" in dashboard.text


def test_login_succeeds_and_failed_login_is_rejected(
    isolated_auth: None,
) -> None:
    with TestClient(app) as client:
        setup = client.post(
            "/api/auth/setup",
            json={"username": "local_admin", "password": "Strong-test-password-123"},
        )
        assert setup.status_code == 200
        client.post("/api/auth/logout")

        failed = client.post(
            "/api/auth/login",
            json={"username": "local_admin", "password": "incorrect-password"},
        )
        assert failed.status_code == 401

        successful = client.post(
            "/api/auth/login",
            json={"username": "local_admin", "password": "Strong-test-password-123"},
        )
        assert successful.status_code == 200
        assert successful.json()["ok"] is True
        assert client.get("/api/auth/status").json()["authenticated"] is True


def test_setup_cannot_be_repeated(isolated_auth: None) -> None:
    with TestClient(app) as client:
        payload = {"username": "local_admin", "password": "Strong-test-password-123"}
        assert client.post("/api/auth/setup", json=payload).status_code == 200
        repeated = client.post(
            "/api/auth/setup",
            json={"username": "another_admin", "password": "Another-strong-password-456"},
        )
        assert repeated.status_code == 409


def test_expired_session_is_rejected(
    isolated_auth: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_auth.create_user("local_admin", "Strong-test-password-123")
    token = local_auth.create_session("local_admin")
    future = time.time() + local_auth.SESSION_TTL_SECONDS + 1
    monkeypatch.setattr(local_auth.time, "time", lambda: future)
    assert local_auth.get_session_username(token) is None


def test_remote_access_is_rejected(isolated_auth: None) -> None:
    with TestClient(app, client=("203.0.113.10", 12345)) as client:
        response = client.get("/login")
    assert response.status_code == 403
    assert "restricted to this computer" in response.json()["detail"]


def test_unauthenticated_api_requests_are_rejected(isolated_auth: None) -> None:
    with TestClient(app) as client:
        response = client.get("/api/instruments")
    assert response.status_code == 401
    assert "Authentication required" in response.json()["detail"]
