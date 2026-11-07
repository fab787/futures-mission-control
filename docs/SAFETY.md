# Safety and Operating Rules

## Initial operating mode

- Read-only research by default.
- TradingView MCP access is restricted to an explicit allow-list of read-only tools.
- Paper trading only; no live brokerage connection.
- Human approval is mandatory for every proposed paper trade.
- No autonomous order execution.
- No agent may edit risk limits or grant itself additional permissions.

## TradingView MCP permissions

- OAuth 2.1 is authorized separately for this local Mission Control application; Claude Desktop's login is not reused.
- OAuth tokens and client registration are stored in the OS keyring, never in source control, dashboard HTML, or plaintext project files.
- Only historical OHLCV, symbol quote batches, news and economic-calendar read tools are allow-listed.
- Watchlist writes, alert creation/modification/deletion, paper-account actions and order submission are not exposed by the adapter.
- A successful OAuth login does not prove that every futures symbol is available or real-time.
- Market data is polled, not streamed. Every display must preserve source and timestamp/freshness information.
- If authentication, symbol mapping, parsing or data freshness fails, show unavailable and block dependent analysis.

## Fail-safe behavior

Block new trade proposals when:
- Required market data is missing, stale, malformed, or contradictory.
- Instrument identity or contract mapping is uncertain.
- Account or position state cannot be verified when required.
- The Master Risk Engine or a critical dependency is unhealthy.
- A required risk setting is missing or invalid.

Existing positions must never be represented as flat merely because a data source is unavailable. Show the state as unknown and raise an alert.

## Secrets

Never commit API keys, access tokens, passwords, account identifiers, or populated environment files. Use OS credential storage or a dedicated secret manager. Rotate any credential that is accidentally exposed.

## TradingView Paper Trading

Paper Trading is the intended initial simulation environment. Do not assume the available TradingView MCP exposes paper-account balances, positions, or simulated order submission. Verify each capability independently before enabling it. A chart-control tool is not automatically a trading tool.
