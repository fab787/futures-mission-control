from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path
from typing import Any

import keyring

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
USER_FILE = DATA_DIR / "local_auth.json"
SECRET_FILE = DATA_DIR / ".local_auth_session.key"

KEYRING_SERVICE = "fab787-quantum-local-auth"
SESSION_SECRET_KEY = "session-signing-secret"
SESSION_TTL_SECONDS = 8 * 60 * 60
PASSWORD_ITERATIONS = 600_000
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


class AuthenticationError(ValueError):
    """Invalid local authentication operation."""


def is_configured() -> bool:
    return USER_FILE.is_file()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _load_user() -> dict[str, Any] | None:
    try:
        value = json.loads(USER_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def create_user(username: str, password: str) -> None:
    """Create the first and only local administrator account."""
    username = username.strip()
    if is_configured():
        raise AuthenticationError("Administrator setup is already complete.")
    if not USERNAME_PATTERN.fullmatch(username):
        raise AuthenticationError("Username must be 3?32 valid characters.")
    if not 12 <= len(password) <= 256:
        raise AuthenticationError("Password must contain 12?256 characters.")

    salt = secrets.token_bytes(32)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt,
        PASSWORD_ITERATIONS, dklen=32,
    )
    record = {
        "username": username,
        "salt": _encode(salt),
        "password_hash": _encode(digest),
        "iterations": PASSWORD_ITERATIONS,
        "created_at": int(time.time()),
        "version": 1,
    }
    _atomic_write(USER_FILE, json.dumps(record, indent=2))


def verify_credentials(username: str, password: str) -> bool:
    record = _load_user()
    if not record or len(password) > 256:
        return False
    try:
        expected_username = str(record["username"])
        salt = _decode(str(record["salt"]))
        expected_hash = _decode(str(record["password_hash"]))
        iterations = int(record["iterations"])
        if not PASSWORD_ITERATIONS <= iterations <= 2_000_000:
            return False
    except (KeyError, TypeError, ValueError):
        return False

    username_ok = hmac.compare_digest(
        username.strip().casefold().encode(),
        expected_username.casefold().encode(),
    )
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt,
        iterations, dklen=len(expected_hash),
    )
    return username_ok and hmac.compare_digest(candidate, expected_hash)


def _signing_secret() -> bytes:
    """Keep the cookie-signing key in Windows Credential Manager where available."""
    try:
        saved = keyring.get_password(KEYRING_SERVICE, SESSION_SECRET_KEY)
        if saved:
            secret = _decode(saved)
            if len(secret) >= 32:
                return secret
        secret = secrets.token_bytes(48)
        keyring.set_password(KEYRING_SERVICE, SESSION_SECRET_KEY, _encode(secret))
        return secret
    except Exception:
        if SECRET_FILE.is_file():
            try:
                secret = _decode(SECRET_FILE.read_text(encoding="utf-8").strip())
                if len(secret) >= 32:
                    return secret
            except (OSError, ValueError):
                pass
        secret = secrets.token_bytes(48)
        _atomic_write(SECRET_FILE, _encode(secret))
        return secret


def create_session(username: str) -> str:
    record = _load_user()
    if not record or not hmac.compare_digest(
        username.casefold(), str(record.get("username", "")).casefold()
    ):
        raise AuthenticationError("Unknown local administrator.")

    now = int(time.time())
    payload = {
        "sub": str(record["username"]),
        "iat": now,
        "exp": now + SESSION_TTL_SECONDS,
        "nonce": secrets.token_urlsafe(16),
    }
    encoded = _encode(json.dumps(
        payload, separators=(",", ":"), sort_keys=True
    ).encode("utf-8"))
    signature = hmac.new(
        _signing_secret(), encoded.encode("ascii"), hashlib.sha256
    ).digest()
    return encoded + "." + _encode(signature)


def get_session_username(token: str | None) -> str | None:
    if not token or "." not in token or len(token) > 4096:
        return None
    try:
        encoded, supplied = token.split(".", 1)
        expected = hmac.new(
            _signing_secret(), encoded.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _decode(supplied)):
            return None

        payload = json.loads(_decode(encoded))
        username = payload["sub"]
        issued = int(payload["iat"])
        expires = int(payload["exp"])
        now = int(time.time())

        if not isinstance(username, str) or not USERNAME_PATTERN.fullmatch(username):
            return None
        if issued > now + 60 or expires <= now:
            return None
        if expires - issued > SESSION_TTL_SECONDS:
            return None

        record = _load_user()
        if not record or not hmac.compare_digest(
            username.casefold(), str(record.get("username", "")).casefold()
        ):
            return None
        return username
    except (KeyError, TypeError, ValueError, UnicodeError):
        return None
