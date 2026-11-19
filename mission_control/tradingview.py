"""Read-only TradingView MCP adapter for FAB787 QUANTUM."""
from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime
from typing import Any

import httpx2
from mcp import Client
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientMetadata, OAuthMetadata, ProtectedResourceMetadata

from mission_control.provider_errors import TradingViewQuotaExceeded, is_quota_error
from mission_control.tradingview_auth import KeyringTokenStorage

MCP_URL = "https://mcp.tradingview.com/mcp"
AUTH_SERVER_URL = "https://mcp.tradingview.com/mcp"
SYMBOLS = {
    "MNQ": "CME_MINI:MNQ1!",
    "MES": "CME_MINI:MES1!",
    "MGC": "COMEX:MGC1!",
}
READ_ONLY_TOOLS = {"get_ohlcv", "get_symbol_data_batch"}
TRADINGVIEW_TOOL_NAMES = {
    "get_ohlcv": "mcp-tv-get-ohlcv",
    "get_symbol_data_batch": "mcp-tv-get-symbol-data-batch",
}
RATE_LIMIT_HEADERS = (
    "retry-after",
    "ratelimit",
    "ratelimit-limit",
    "ratelimit-remaining",
    "ratelimit-reset",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "x-rate-limit-limit",
    "x-rate-limit-remaining",
    "x-rate-limit-reset",
    "x-request-id",
)
_LAST_DIAGNOSTIC: dict[str, Any] = {
    "last_http_status": None,
    "last_http_at": None,
    "rate_limit_headers": {},
    "last_tool": None,
    "last_error": None,
    "last_error_at": None,
    "quota_detected": False,
}


def _safe_diagnostic_text(value: Any) -> str:
    message = str(value)
    message = re.sub(
        r"(?i)\bbearer\s+[^\s,;]+",
        "Bearer [REDACTED]",
        message,
    )
    message = re.sub(
        r"(?i)\b(authorization|access[_-]?token|refresh[_-]?token|api[_-]?key|password)\b"
        r"(\s*[:=]\s*)[^\s,;]+",
        r"\1=[REDACTED]",
        message,
    )
    return message[:600]


def diagnostics_snapshot() -> dict[str, Any]:
    """Return sanitized in-memory TradingView HTTP and quota diagnostics."""
    return copy.deepcopy(_LAST_DIAGNOSTIC)


def _reset_diagnostics() -> None:
    """Reset diagnostic state for isolated tests."""
    _LAST_DIAGNOSTIC.update({
        "last_http_status": None,
        "last_http_at": None,
        "rate_limit_headers": {},
        "last_tool": None,
        "last_error": None,
        "last_error_at": None,
        "quota_detected": False,
    })


async def _capture_response(response: httpx2.Response) -> None:
    headers = {
        name: response.headers.get(name)
        for name in RATE_LIMIT_HEADERS
        if response.headers.get(name) is not None
    }
    _LAST_DIAGNOSTIC.update({
        "last_http_status": response.status_code,
        "last_http_at": datetime.now(UTC).isoformat(),
        "rate_limit_headers": headers,
    })
    if response.status_code == 429:
        _LAST_DIAGNOSTIC["quota_detected"] = True
    elif response.status_code < 400:
        _LAST_DIAGNOSTIC["last_error"] = None
        _LAST_DIAGNOSTIC["last_error_at"] = None
        _LAST_DIAGNOSTIC["quota_detected"] = False
    if response.status_code >= 400:
        body_text = ""
        try:
            body = await response.aread()
            body_text = body.decode("utf-8", errors="replace")
        except Exception:
            pass
        _LAST_DIAGNOSTIC["last_error"] = _safe_diagnostic_text(
            f"HTTP {response.status_code} {getattr(response, 'reason_phrase', '')} {body_text}"
        )
        _LAST_DIAGNOSTIC["last_error_at"] = datetime.now(UTC).isoformat()


def _record_tool_error(name: str, details: Any) -> None:
    _LAST_DIAGNOSTIC["last_tool"] = name
    _LAST_DIAGNOSTIC["last_error"] = _safe_diagnostic_text(details)
    _LAST_DIAGNOSTIC["last_error_at"] = datetime.now(UTC).isoformat()


class AuthorizationRequired(RuntimeError):
    """The user must complete the one-time TradingView OAuth flow."""


async def _no_browser_auth(_url: str) -> None:
    raise AuthorizationRequired("Authorize TradingView with: python -m mission_control.tradingview_auth")


async def _no_callback() -> Any:
    raise AuthorizationRequired("Authorize TradingView with: python -m mission_control.tradingview_auth")


def _oauth_provider() -> OAuthClientProvider:
    storage = KeyringTokenStorage()
    oauth = OAuthClientProvider(
        server_url=AUTH_SERVER_URL,
        client_metadata=OAuthClientMetadata(
            client_name="FAB787 QUANTUM",
            redirect_uris=["http://127.0.0.1:8765/callback"],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
        storage=storage,
        redirect_handler=_no_browser_auth,
        callback_handler=_no_callback,
    )

    # Restore the actual expiry time for tokens loaded from the OS keyring.
    original_initialize = oauth._initialize

    async def _initialize_with_expiry() -> None:
        await original_initialize()

        # Preload TradingView's published OAuth metadata before token refresh.
        # The MCP resource URL and authorization-server issuer are different.
        oauth.context.auth_server_url = "https://www.tradingview.com"
        oauth.context.protected_resource_metadata = ProtectedResourceMetadata(
            resource=MCP_URL,
            authorization_servers=["https://www.tradingview.com"],
        )
        oauth.context.oauth_metadata = OAuthMetadata(
            issuer="https://www.tradingview.com",
            authorization_endpoint=(
                "https://www.tradingview.com/mcp/oauth/authorize"
            ),
            token_endpoint="https://www.tradingview.com/mcp/oauth/token",
            registration_endpoint=(
                "https://www.tradingview.com/mcp/oauth/register"
            ),
            token_endpoint_auth_methods_supported=[
                "none",
                "client_secret_basic",
                "client_secret_post",
            ],
        )

        tokens = oauth.context.current_tokens
        if tokens is None or tokens.expires_in is None:
            return

        saved_at = storage.get_token_saved_at()
        if saved_at is None:
            # Legacy token: its issue time is unknown, so refresh it once.
            oauth.context.token_expiry_time = 0.0
        else:
            # Refresh slightly before the provider's stated expiry.
            oauth.context.token_expiry_time = (
                saved_at + int(tokens.expires_in) - 30
            )

    oauth._initialize = _initialize_with_expiry
    return oauth


def _decode_content(items: list[Any]) -> Any:
    decoded: list[Any] = []
    for item in items:
        value = getattr(item, "text", None)
        if value is None:
            decoded.append(item.model_dump(mode="json") if hasattr(item, "model_dump") else str(item))
            continue
        try:
            decoded.append(json.loads(value))
        except (TypeError, json.JSONDecodeError):
            decoded.append(value)
    return decoded[0] if len(decoded) == 1 else decoded


def _find_bars(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        if isinstance(value.get("bars"), list):
            return [bar for bar in value["bars"] if isinstance(bar, dict)]
        for child in value.values():
            found = _find_bars(child)
            if found:
                return found
    elif isinstance(value, list):
        if value and all(isinstance(row, dict) and {"t", "o", "h", "l", "c"}.issubset(row) for row in value):
            return value
        for child in value:
            found = _find_bars(child)
            if found:
                return found
    return []


async def call_readonly_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Call only explicitly allow-listed read-only TradingView tools."""
    if name not in READ_ONLY_TOOLS:
        raise ValueError(f"Tool is not enabled by FAB787 QUANTUM: {name}")
    oauth = _oauth_provider()

    async def _stamp_user_agent(request: httpx2.Request) -> None:
        if "user-agent" not in request.headers:
            request.headers["User-Agent"] = "FAB787-QUANTUM/1.0"

    async with httpx2.AsyncClient(
        auth=oauth,
        timeout=httpx2.Timeout(30.0, read=90.0),
        event_hooks={"request": [_stamp_user_agent], "response": [_capture_response]},
    ) as http:
        transport = streamable_http_client(MCP_URL, http_client=http)
        async with Client(transport, mode="legacy") as client:
            try:
                result = await client.call_tool(TRADINGVIEW_TOOL_NAMES[name], arguments)
            except Exception as exc:
                _record_tool_error(name, exc)
                if is_quota_error(exc):
                    _LAST_DIAGNOSTIC["quota_detected"] = True
                    raise TradingViewQuotaExceeded(
                        f"TradingView quota/rate limit transport failure: "
                        f"{_safe_diagnostic_text(exc)}"
                    ) from exc
                raise
            _LAST_DIAGNOSTIC["last_tool"] = name
            if result.is_error:
                details = _decode_content(result.content)
                _record_tool_error(name, details)
                detail_text = _safe_diagnostic_text(json.dumps(details, default=str))
                if is_quota_error(details):
                    _LAST_DIAGNOSTIC["quota_detected"] = True
                    raise TradingViewQuotaExceeded(
                        f"TradingView quota/rate limit reached: {detail_text}"
                    )
                raise RuntimeError(
                    f"TradingView MCP returned an error for {name}: {detail_text}"
                )
            return result.structured_content or _decode_content(result.content)


async def market_snapshot(interval: str = "1m") -> dict[str, Any]:
    """Fetch historical bars at a supported interval; this is polling, not streaming."""
    if interval not in {"1m", "5m", "15m", "1h", "4h", "1D"}:
        raise ValueError("Unsupported interval")
    async def one(symbol: str, tv_symbol: str) -> dict[str, Any]:
        try:
            payload = await call_readonly_tool(
                "get_ohlcv", {"symbol": tv_symbol, "interval": interval, "count": 120}
            )
            bars = _find_bars(payload)
            return {
                "symbol": symbol,
                "tradingview_symbol": tv_symbol,
                "status": "connected" if bars else "received_unparsed",
                "bars": bars,
                "latest": bars[-1] if bars else None,
                "source": "TradingView MCP",
                "interval": interval,
            }
        except (AuthorizationRequired, TradingViewQuotaExceeded):
            raise
        except Exception as exc:
            return {
                "symbol": symbol,
                "tradingview_symbol": tv_symbol,
                "status": "unavailable",
                "error": type(exc).__name__,
                "bars": [],
                "latest": None,
            }

    # Keep calls sequential to avoid simultaneous OAuth refresh attempts from one user session.
    results = []
    for symbol, tv_symbol in SYMBOLS.items():
        results.append(await one(symbol, tv_symbol))
    return {
        "source": "TradingView MCP",
        "read_only": True,
        "interval": interval,
        "provider_requests": len(results),
        "instruments": results,
    }
