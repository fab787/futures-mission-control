"""Read-only Webull Cloud MCP market-data adapter for FAB787 QUANTUM."""
from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any

import httpx2
import keyring
from mcp import Client
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)

MCP_URL = "https://api.webull.com/mcp"
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 8766
WEBULL_SCOPES = "market:read instrument:read"

KEYRING_SERVICE = "fab787-quantum-webull"
TOKEN_KEY = "oauth_tokens"
CLIENT_KEY = "oauth_client_info"
EXPIRY_KEY = "oauth_token_expiry"
PRM_URL = "https://api.webull.com/.well-known/oauth-protected-resource/mcp"
OAUTH_METADATA_URL = "https://api.webull.com/.well-known/oauth-authorization-server"
OAUTH_ISSUER = "https://api.webull.com"
OAUTH_TOKEN_ENDPOINT = "https://u1suserauth.webullfintech.com/api/userauth/oauth/token/token"

# Explicit allowlist: no account, order, instruction, or watchlist operations.
READ_ONLY_TOOLS = frozenset({
    "get_futures_bars",
    "get_futures_depth",
    "get_futures_instruments",
    "get_futures_product_class",
    "get_futures_products",
    "get_futures_snapshot",
    "get_futures_tick",
})

INSTRUMENT_CODES = {
    "MNQ": "MNQ",
    "MES": "MES",
    "MGC": "MGC",
}

INTERVALS = {
    "1m": "M1",
    "5m": "M5",
    "15m": "M15",
    "30m": "M30",
    "1h": "M60",
    "2h": "M120",
    "4h": "M240",
    "1D": "D",
    "1W": "W",
}


class AuthorizationRequired(RuntimeError):
    """The user must authorize this FAB787 QUANTUM Webull installation."""


class WebullTokenStorage(TokenStorage):
    """Persist Webull OAuth tokens and absolute expiry separately in keyring."""

    async def get_tokens(self) -> OAuthToken | None:
        value = keyring.get_password(KEYRING_SERVICE, TOKEN_KEY)
        return OAuthToken.model_validate_json(value) if value else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        keyring.set_password(KEYRING_SERVICE, TOKEN_KEY, tokens.model_dump_json())
        now = time.time()
        remaining = (
            max(float(tokens.expires_in) - 60.0, 0.0)
            if tokens.expires_in is not None
            else 0.0
        )
        expiry = now + remaining if remaining > 0 else now - 1.0
        keyring.set_password(KEYRING_SERVICE, EXPIRY_KEY, str(expiry))

    async def get_token_expiry(self) -> float | None:
        value = keyring.get_password(KEYRING_SERVICE, EXPIRY_KEY)
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return 0.0

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        value = keyring.get_password(KEYRING_SERVICE, CLIENT_KEY)
        return OAuthClientInformationFull.model_validate_json(value) if value else None

    async def set_client_info(
        self,
        client_info: OAuthClientInformationFull,
    ) -> None:
        keyring.set_password(
            KEYRING_SERVICE,
            CLIENT_KEY,
            client_info.model_dump_json(),
        )


class WebullOAuthClientProvider(OAuthClientProvider):
    """OAuth provider that restores expiry and preloads Webull's published metadata."""

    def __init__(
        self,
        storage: WebullTokenStorage,
        redirect_handler: Callable[[str], Awaitable[None]] | None = None,
        callback_handler: Callable[[], Awaitable[AuthorizationCodeResult]] | None = None,
    ) -> None:
        self._webull_storage = storage
        super().__init__(
            server_url=MCP_URL,
            client_metadata=OAuthClientMetadata(
                client_name="FAB787 QUANTUM Webull Market Data",
                redirect_uris=[
                    f"http://{CALLBACK_HOST}:{CALLBACK_PORT}/callback"
                ],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                scope=WEBULL_SCOPES,
            ),
            storage=storage,
            redirect_handler=redirect_handler or _no_browser_auth,
            callback_handler=callback_handler or _no_callback,
        )

    async def _load_webull_metadata(self) -> None:
        async with httpx2.AsyncClient(
            timeout=httpx2.Timeout(15.0),
            event_hooks={"request": [_stamp_user_agent]},
        ) as http:
            prm_response = await http.get(PRM_URL)
            prm_response.raise_for_status()
            oauth_response = await http.get(OAUTH_METADATA_URL)
            oauth_response.raise_for_status()

        prm = ProtectedResourceMetadata.model_validate(prm_response.json())
        metadata = OAuthMetadata.model_validate(oauth_response.json())

        if str(prm.resource).rstrip("/") != OAUTH_ISSUER:
            raise RuntimeError("Webull protected-resource metadata has an unexpected resource.")
        if OAUTH_ISSUER not in {str(item).rstrip("/") for item in prm.authorization_servers}:
            raise RuntimeError("Webull metadata does not advertise the expected authorization server.")
        if str(metadata.issuer).rstrip("/") != OAUTH_ISSUER:
            raise RuntimeError("Webull OAuth metadata has an unexpected issuer.")
        if str(metadata.token_endpoint) != OAUTH_TOKEN_ENDPOINT:
            raise RuntimeError("Webull OAuth token endpoint differs from the verified endpoint.")
        if "none" not in (metadata.token_endpoint_auth_methods_supported or []):
            raise RuntimeError("Webull metadata does not advertise the expected public-client authentication.")
        if "S256" not in (metadata.code_challenge_methods_supported or []):
            raise RuntimeError("Webull metadata does not advertise PKCE S256.")
        supported_scopes = set(metadata.scopes_supported or [])
        if not {"market:read", "instrument:read"}.issubset(supported_scopes):
            raise RuntimeError("Webull metadata does not advertise both requested read-only scopes.")

        self.context.protected_resource_metadata = prm
        self.context.oauth_metadata = metadata
        self.context.auth_server_url = OAUTH_ISSUER

    async def _initialize(self) -> None:
        self._initialized = False
        try:
            await super()._initialize()
            if self.context.current_tokens is not None:
                expiry = await self._webull_storage.get_token_expiry()
                # Missing expiry is treated as expired so the stored refresh token
                # is tried instead of assuming an old access token is still valid.
                self.context.token_expiry_time = (
                    expiry if expiry is not None and expiry > 0 else time.time() - 1.0
                )

            # Load the advertised token endpoint before any refresh can occur.
            await self._load_webull_metadata()
            self._initialized = True
        except Exception:
            self._initialized = False
            raise


async def _no_browser_auth(_url: str) -> None:
    raise AuthorizationRequired(
        "Authorize Webull with: python -m mission_control.webull_auth"
    )


async def _no_callback() -> AuthorizationCodeResult:
    raise AuthorizationRequired(
        "Authorize Webull with: python -m mission_control.webull_auth"
    )


def _oauth_provider(
    redirect_handler: Callable[[str], Awaitable[None]] | None = None,
    callback_handler: Callable[[], Awaitable[AuthorizationCodeResult]] | None = None,
) -> OAuthClientProvider:
    return WebullOAuthClientProvider(
        storage=WebullTokenStorage(),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )

async def _stamp_user_agent(request: httpx2.Request) -> None:
    if "user-agent" not in request.headers:
        request.headers["User-Agent"] = "FAB787-QUANTUM/1.0"


def _decode_content(items: list[Any]) -> Any:
    decoded: list[Any] = []
    for item in items:
        value = getattr(item, "text", None)
        if value is None:
            decoded.append(
                item.model_dump(mode="json")
                if hasattr(item, "model_dump")
                else str(item)
            )
            continue
        try:
            decoded.append(json.loads(value))
        except (TypeError, json.JSONDecodeError):
            decoded.append(value)
    return decoded[0] if len(decoded) == 1 else decoded


async def _call_with_client(
    client: Client,
    name: str,
    arguments: dict[str, Any],
) -> Any:
    if name not in READ_ONLY_TOOLS:
        raise ValueError(f"Webull tool is not enabled by FAB787 QUANTUM: {name}")

    result = await client.call_tool(name, arguments)
    if result.is_error:
        details = _decode_content(result.content)
        raise RuntimeError(
            f"Webull MCP returned an error for {name}: "
            f"{json.dumps(details, default=str)[:1000]}"
        )

    if result.structured_content is not None:
        return result.structured_content
    return _decode_content(result.content)


async def call_readonly_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Call one explicitly allowlisted Webull futures market-data tool."""
    if name not in READ_ONLY_TOOLS:
        raise ValueError(f"Webull tool is not enabled by FAB787 QUANTUM: {name}")

    oauth = _oauth_provider()
    async with httpx2.AsyncClient(
        auth=oauth,
        timeout=httpx2.Timeout(30.0, read=90.0),
        event_hooks={"request": [_stamp_user_agent]},
    ) as http:
        transport = streamable_http_client(MCP_URL, http_client=http)
        async with Client(transport) as client:
            return await _call_with_client(client, name, arguments)


def _records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("data", "items", "instruments", "records", "result", "results"):
            child = value.get(key)
            if isinstance(child, list):
                return [item for item in child if isinstance(item, dict)]
    return []


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_contract_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _select_contract(
    candidates: list[dict[str, Any]],
    snapshots: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    """Choose the highest-volume tradable expiry; fall back to nearest expiry."""
    if not candidates:
        raise ValueError("Webull returned no tradable contracts for this product.")

    today = datetime.now(UTC).date()
    valid: list[dict[str, Any]] = []
    for item in candidates:
        if item.get("status", "OC") != "OC" or not item.get("symbol"):
            continue
        last_trade = _parse_contract_date(item.get("last_trading_date"))
        if last_trade is not None and last_trade < today:
            continue
        valid.append(item)

    if not valid:
        raise ValueError("No currently tradable Webull futures contract was found.")

    ranked: list[tuple[float, float, str, dict[str, Any]]] = []
    for item in valid:
        symbol = str(item["symbol"])
        snap = snapshots.get(symbol, {})
        volume = _number(snap.get("volume"))
        open_interest = _number(snap.get("open_interest"))
        month = str(item.get("contract_month") or "")
        ranked.append((volume, open_interest, month, item))

    with_quotes = [row for row in ranked if row[0] > 0 or row[1] > 0]
    if with_quotes:
        selected = max(with_quotes, key=lambda row: (row[0], row[1], row[2]))
        return selected[3], "highest_snapshot_volume_then_open_interest"

    fallback = min(
        valid,
        key=lambda item: str(item.get("contract_month") or "999999"),
    )
    return fallback, "nearest_listed_contract_fallback"


def _extract_bar_groups(value: Any) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            symbol = node.get("symbol")
            if symbol and (
                isinstance(node.get("result"), list)
                or isinstance(node.get("bars"), list)
            ):
                groups[str(symbol)] = node
            else:
                for child in node.values():
                    if isinstance(child, (dict, list)):
                        visit(child)

    visit(value)
    return groups


def _timestamp_seconds(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return int(number / 1000) if number > 10_000_000_000 else int(number)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())


def _normalize_bars(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []

    bars: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        timestamp = _timestamp_seconds(row.get("time", row.get("t")))
        if timestamp is None:
            continue

        open_price = row.get("open", row.get("o"))
        high_price = row.get("high", row.get("h"))
        low_price = row.get("low", row.get("l"))
        close_price = row.get("close", row.get("c"))
        if None in (open_price, high_price, low_price, close_price):
            continue

        bar = {
            "t": timestamp,
            "o": _number(open_price),
            "h": _number(high_price),
            "l": _number(low_price),
            "c": _number(close_price),
            "v": _number(row.get("volume", row.get("v", 0))),
        }
        bars.append(bar)

    # Webull currently returns futures bars newest-first; dashboard bars are oldest-first.
    bars.sort(key=lambda item: item["t"])
    return bars


async def _resolve_active_contracts(
    client: Client,
) -> tuple[dict[str, dict[str, Any]], int]:
    candidates_by_code: dict[str, list[dict[str, Any]]] = {}
    requests = 0

    for symbol, code in INSTRUMENT_CODES.items():
        payload = await _call_with_client(
            client,
            "get_futures_instruments",
            {"category": "US_FUTURES", "code": code},
        )
        requests += 1
        records = _records(payload)
        candidates_by_code[symbol] = [
            item for item in records
            if item.get("code") == code and item.get("symbol")
        ]

    all_candidates = [
        item
        for candidates in candidates_by_code.values()
        for item in candidates
        if item.get("status", "OC") == "OC"
    ]
    symbols = list(dict.fromkeys(str(item["symbol"]) for item in all_candidates))

    snapshots: dict[str, dict[str, Any]] = {}
    if symbols:
        payload = await _call_with_client(
            client,
            "get_futures_snapshot",
            {
                "symbols": ",".join(symbols[:20]),
                "category": "US_FUTURES",
            },
        )
        requests += 1
        snapshots = {
            str(item["symbol"]): item
            for item in _records(payload)
            if item.get("symbol")
        }

    selected: dict[str, dict[str, Any]] = {}
    for symbol, candidates in candidates_by_code.items():
        try:
            contract, method = _select_contract(candidates, snapshots)
            selected[symbol] = {
                **contract,
                "selection_method": method,
            }
        except ValueError as exc:
            selected[symbol] = {
                "symbol": None,
                "selection_error": str(exc),
            }

    return selected, requests


async def market_snapshot(interval: str = "1m", count: int = 120) -> dict[str, Any]:
    """Fetch one normalized, contract-aware snapshot for MNQ, MES and MGC."""
    if interval not in INTERVALS:
        raise ValueError(f"Unsupported Webull interval: {interval}")
    if not 1 <= count <= 1200:
        raise ValueError("Webull futures bar count must be between 1 and 1200.")

    oauth = _oauth_provider()
    async with httpx2.AsyncClient(
        auth=oauth,
        timeout=httpx2.Timeout(30.0, read=90.0),
        event_hooks={"request": [_stamp_user_agent]},
    ) as http:
        transport = streamable_http_client(MCP_URL, http_client=http)
        async with Client(transport) as client:
            selected, provider_requests = await _resolve_active_contracts(client)
            valid_contracts = {
                name: item for name, item in selected.items()
                if item.get("symbol")
            }

            bar_groups: dict[str, dict[str, Any]] = {}
            if valid_contracts:
                payload = await _call_with_client(
                    client,
                    "get_futures_bars",
                    {
                        "symbols": ",".join(
                            str(item["symbol"]) for item in valid_contracts.values()
                        ),
                        "category": "US_FUTURES",
                        "timespan": INTERVALS[interval],
                        "count": count,
                    },
                )
                provider_requests += 1
                bar_groups = _extract_bar_groups(payload)

    instruments: list[dict[str, Any]] = []
    for name in INSTRUMENT_CODES:
        contract = selected.get(name, {})
        contract_symbol = contract.get("symbol")
        group = bar_groups.get(str(contract_symbol), {}) if contract_symbol else {}
        raw_bars = group.get("result", group.get("bars", []))
        bars = _normalize_bars(raw_bars)
        instruments.append({
            "symbol": name,
            "webull_symbol": contract_symbol,
            "status": "connected" if bars else "unavailable",
            "error": contract.get("selection_error"),
            "bars": bars,
            "latest": bars[-1] if bars else None,
            "source": "Webull Cloud MCP",
            "interval": interval,
            "delay_minutes": group.get("delay_minutes"),
            "contract_month": contract.get("contract_month"),
            "exchange_code": contract.get("exchange_code"),
            "contract_selection": contract.get("selection_method"),
        })

    return {
        "source": "Webull Cloud MCP",
        "read_only": True,
        "interval": interval,
        "provider_requests": provider_requests,
        "instruments": instruments,
    }
