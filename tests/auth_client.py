from __future__ import annotations

import secrets
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from mission_control import local_auth
from mission_control.main import app

# Existing API tests exercise authenticated application behavior. Give each
# test process one isolated administrator and a real signed session cookie.
_TEST_DATA = Path(tempfile.mkdtemp(prefix="fab787-auth-tests-"))
local_auth.USER_FILE = _TEST_DATA / "local_auth.json"
local_auth.SECRET_FILE = _TEST_DATA / ".local_auth_session.key"

# Keep tests independent of the machine's OS keyring. The local_auth fallback
# stores this test-only signing key in the temporary directory above.
def _keyring_get(_service: str, _key: str) -> None:
    return None


def _keyring_set(_service: str, _key: str, _value: str) -> None:
    raise RuntimeError("OS keyring intentionally disabled in tests")


local_auth.keyring.get_password = _keyring_get
local_auth.keyring.set_password = _keyring_set
_TEST_PASSWORD = secrets.token_urlsafe(32)
local_auth.create_user("pytest_admin", _TEST_PASSWORD)
_TEST_SESSION = local_auth.create_session("pytest_admin")


def authenticated_client() -> TestClient:
    """Return a TestClient carrying a valid, locally signed test session."""
    client = TestClient(app)
    client.cookies.set("fq_session", _TEST_SESSION)
    return client
