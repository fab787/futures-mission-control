"""Read-only TradingView MCP adapter for Mission Control."""
from __future__ import annotations

import json
from typing import Any

import httpx2
from mcp import Client
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientMetadata

from mission_control.tradingview_auth import KeyringTokenStorage
from mission_control.provider_errors import TradingViewQuotaExceeded

MCP_URL = "https://mcp.tradingview.com/mcp"
AUTH_SERVER_URL = "https://mcp.tradingview.com/mcp"
SYMBOLS = {
    "MNQ": "CME_MINI:MNQ1!",
    "MES": "CME_MINI:MES1!",
    "MGC": "COMEX:MGC1!",
}
READ_ONLY_TOOLS = {"get_ohlcv", "get_symbol_data_batch", "get_news", "get_economic_calendar"}


class AuthorizationRequired(RuntimeError):
    """The user must complete the one-time TradingView OAuth flow."""


async def _no_browser_auth(_url: str) -> None:
    raise AuthorizationRequired("Authorize TradingView with: python -m mission_control.tradingview_auth")


async def _no_callback() -> Any:
    raise AuthorizationRequired("Authorize TradingView with: python -m mission_control.tradingview_auth")


def _oauth_provider() -> OAuthClientProvider:
    return OAuthClientProvider(
        server_url=AUTH_SERVER_URL,
        client_metadata=OAuthClientMetadata(
            client_name="Futures Mission Control",
            redirect_uris=["http://127.0.0.1:8765/callback"],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
        storage=KeyringTokenStorage(),
        redirect_handler=_no_browser_auth,
        callback_handler=_no_callback,
    )


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
        raise ValueError(f"Tool is not enabled by Mission Control: {name}")
    oauth = _oauth_provider()
    async with httpx2.AsyncClient(auth=oauth, timeout=httpx2.Timeout(30.0, read=90.0)) as http:
        transport = streamable_http_client(MCP_URL, http_client=http)
        async with Client(transport) as client:
            result = await client.call_tool(name, arguments)
            if result.is_error:
                details = _decode_content(result.content)
                detail_text = json.dumps(details, default=str)[:1000]
                lowered = detail_text.lower()
                quota_markers = ("quota", "rate limit", "too many requests", "resource exhausted")
                if any(marker in lowered for marker in quota_markers):
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
        except AuthorizationRequired:
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
    return {"source": "TradingView MCP", "read_only": True, "interval": interval, "instruments": results}
