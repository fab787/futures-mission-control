"""One-time interactive OAuth authorization for Webull Cloud MCP.

Run from the project folder:
    python -m mission_control.webull_auth

Webull credentials are stored separately in the operating system keyring.
Only market:read and instrument:read scopes are requested.
"""
from __future__ import annotations

import asyncio
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import httpx2
from mcp import Client
from mcp.client.auth import AuthorizationCodeResult
from mcp.client.streamable_http import streamable_http_client

from mission_control.webull import (
    CALLBACK_HOST,
    CALLBACK_PORT,
    MCP_URL,
    READ_ONLY_TOOLS,
    _oauth_provider,
)


class CallbackServer(HTTPServer):
    callback: AuthorizationCodeResult | None = None

    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
    ) -> None:
        super().__init__(server_address, handler)
        self.callback_event = threading.Event()


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        query = parse_qs(urlparse(self.path).query)
        if "error" in query:
            description = query.get("error_description", query["error"])[0]
            body = (
                "Webull authorization was not completed: "
                + description
            ).encode("utf-8", errors="replace")
            self.send_response(400)
        elif "code" in query and "state" in query:
            self.server.callback = AuthorizationCodeResult(
                code=query["code"][0],
                state=query["state"][0],
                iss=query.get("iss", [None])[0],
            )
            self.server.callback_event.set()
            body = (
                b"Webull authorization received. "
                b"You can close this browser tab."
            )
            self.send_response(200)
        else:
            body = (
                b"Webull authorization did not complete. "
                b"Return to FAB787 QUANTUM."
            )
            self.send_response(400)

        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


async def _open_browser(url: str) -> None:
    print("Opening Webull authorization for FAB787 QUANTUM.")
    print("Requested permissions: market:read instrument:read")
    webbrowser.open(url)


async def _wait_for_callback(
    server: CallbackServer,
) -> AuthorizationCodeResult:
    received = await asyncio.to_thread(server.callback_event.wait, 300)
    if not received or server.callback is None:
        raise RuntimeError("Timed out waiting for the Webull OAuth callback.")
    return server.callback


async def main() -> None:
    callback_server = CallbackServer(
        (CALLBACK_HOST, CALLBACK_PORT),
        CallbackHandler,
    )
    callback_thread = threading.Thread(
        target=callback_server.serve_forever,
        daemon=True,
    )
    callback_thread.start()

    oauth = _oauth_provider(
        redirect_handler=_open_browser,
        callback_handler=lambda: _wait_for_callback(callback_server),
    )

    async def stamp_user_agent(request: httpx2.Request) -> None:
        if "user-agent" not in request.headers:
            request.headers["User-Agent"] = "FAB787-QUANTUM/1.0"

    try:
        async with httpx2.AsyncClient(
            auth=oauth,
            timeout=httpx2.Timeout(30.0, read=90.0),
            event_hooks={"request": [stamp_user_agent]},
        ) as http:
            transport = streamable_http_client(MCP_URL, http_client=http)
            async with Client(transport) as client:
                tools = await client.list_tools()
                readonly = sorted(
                    tool.name
                    for tool in tools.tools
                    if tool.name in READ_ONLY_TOOLS
                )
                print("Webull MCP authorization successful.")
                print(
                    "Allowlisted futures market-data tools:",
                    ", ".join(readonly) or "none",
                )
                if not readonly:
                    print(
                        "WARNING: No allowlisted tools were discovered. "
                        "No trading or account tools will be enabled."
                    )
    finally:
        callback_server.shutdown()
        callback_server.server_close()
        callback_thread.join(timeout=2)


if __name__ == "__main__":
    asyncio.run(main())
