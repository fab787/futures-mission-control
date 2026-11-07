# Safety and Operating Rules

## Initial operating mode

- Read-only research by default.
- Paper trading only; no live brokerage connection.
- Human approval is mandatory for every proposed paper trade.
- No autonomous order execution.
- No agent may edit risk limits or grant itself additional permissions.

## Fail-safe behavior

Block new trade proposals when:
- Required market data is missing, stale, malformed, or contradictory.
- Instrument identity or contract mapping is uncertain.
- Account or position state cannot be verified when required.
- The Master Risk Engine or a critical dependency is unhealthy.
- A required risk setting is missing or invalid.

Existing positions must never be represented as flat merely because a data source is unavailable. Show the state as unknown and raise an alert.

## Secrets

Never commit API keys, access tokens, passwords, account identifiers, or populated environment files. Use environment variables or a local untracked `.env` file. Rotate any credential that is accidentally exposed.

## TradingView Paper Trading

Paper Trading is the intended initial simulation environment. Do not assume the available TradingView MCP exposes paper-account balances, positions, or simulated order submission. Verify each capability independently before enabling it. A chart-control tool is not automatically a trading tool.
