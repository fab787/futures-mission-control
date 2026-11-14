"""One-time interactive OAuth authorization for TradingView MCP.

Run from the project folder with the virtual environment active:
    python -m mission_control.tradingview_auth

OAuth tokens and client registration are stored in the operating system keyring.
"""
from __future__ import annotations

import asyncio
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import keyring
from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

SERVICE = "futures-mission-control-tradingview"
TOKEN_KEY = "oauth_tokens"
CLIENT_KEY = "oauth_client_info"
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 8765


class KeyringTokenStorage(TokenStorage):
    """Persist OAuth tokens and client registration in the OS keyring."""

    async def get_tokens(self) -> OAuthToken | None:
        value = keyring.get_password(SERVICE, TOKEN_KEY)
        return OAuthToken.model_validate_json(value) if value else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        keyring.set_password(SERVICE, TOKEN_KEY, tokens.model_dump_json())

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        value = keyring.get_password(SERVICE, CLIENT_KEY)
        return OAuthClientInformationFull.model_validate_json(value) if value else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        keyring.set_password(SERVICE, CLIENT_KEY, client_info.model_dump_json())


class CallbackServer(HTTPServer):
    callback: AuthorizationCodeResult | None = None

    def __init__(self, server_address: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(server_address, handler)
        self.callback_event = threading.Event()


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        query = parse_qs(urlparse(self.path).query)
        if "code" in query and "state" in query:
            self.server.callback = AuthorizationCodeResult(
                code=query["code"][0],
                state=query["state"][0],
                iss=query.get("iss", [None])[0],
            )
            self.server.callback_event.set()
            body = b"TradingView authorization received. You can close this browser tab."
            self.send_response(200)
        else:
            body = b"TradingView authorization did not complete. Return to FAB787 QUANTUM."
            self.send_response(400)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


async def _open_browser(url: str) -> None:
    print("Opening TradingView sign-in. Approve access for FAB787 QUANTUM.")
    webbrowser.open(url)


async def _wait_for_callback(server: CallbackServer) -> AuthorizationCodeResult:
    received = await asyncio.to_thread(server.callback_event.wait, 300)
    if not received or server.callback is None:
        raise RuntimeError("Timed out waiting for TradingView OAuth callback.")
    return server.callback


async def main() -> None:
    # Start the loopback listener before opening the browser to avoid a callback race.
    callback_server = CallbackServer((CALLBACK_HOST, CALLBACK_PORT), CallbackHandler)
    callback_thread = threading.Thread(target=callback_server.serve_forever, daemon=True)
    callback_thread.start()
    oauth = OAuthClientProvider(
        server_url="https://mcp.tradingview.com/mcp",
        client_metadata=OAuthClientMetadata(
            client_name="FAB787 QUANTUM",
            redirect_uris=[f"http://{CALLBACK_HOST}:{CALLBACK_PORT}/callback"],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
        storage=KeyringTokenStorage(),
        redirect_handler=_open_browser,
        callback_handler=lambda: _wait_for_callback(callback_server),
    )
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    try:
        async with httpx2.AsyncClient(auth=oauth, timeout=httpx2.Timeout(30.0, read=90.0)) as http:
            transport = streamable_http_client("https://mcp.tradingview.com/mcp", http_client=http)
            async with Client(transport) as client:
                tools = await client.list_tools()
                readonly = sorted(
                    tool.name for tool in tools.tools
                    if tool.name in {"get_ohlcv", "get_symbol_data_batch", "get_news", "get_economic_calendar"}
                )
                print("TradingView MCP authorization successful.")
                print("Read-only tools available:", ", ".join(readonly) or "none")
    finally:
        callback_server.shutdown()
        callback_server.server_close()
        callback_thread.join(timeout=2)


if __name__ == "__main__":
    asyncio.run(main())
