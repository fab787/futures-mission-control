# Safety and Operating Rules

## Initial operating mode

- Read-only research by default.
- TradingView MCP access is restricted to an explicit allow-list of read-only tools.
- Paper trading only; no live brokerage connection.
- Human approval is mandatory for every proposed paper trade.
- No autonomous order execution.
- No agent may edit risk limits or grant itself additional permissions.

## TradingView MCP permissions

- OAuth 2.1 is authorized separately for this local FAB787 QUANTUM application; Claude Desktop's login is not reused.
- OAuth tokens and client registration are stored in the OS keyring, never in source control, dashboard HTML, or plaintext project files.
- Only historical OHLCV and symbol quote-batch tools are allow-listed.
- TradingView news and economic-calendar tools are not enabled. Yahoo Finance is the sole news/calendar provider in this phase.
- Watchlist writes, alert creation/modification/deletion, paper-account actions and order submission are not exposed by the adapter.
- A successful OAuth login does not prove that every futures symbol is available or real-time.
- Market data is polled, not streamed. Every display must preserve source and timestamp/freshness information.
- If authentication, symbol mapping, parsing or data freshness fails, show unavailable and block dependent analysis.

## News and calendar limitations

- Yahoo ticker headlines are fetched through one combined RSS request, cached for ten minutes, and protected by a fifteen-minute failure cooldown.
- Stale headlines must be labeled with their cache age. Publisher, original publication time, received time and source URL must be retained.
- Yahoo ticker RSS is a bounded ticker-headline feed, not a complete real-time market-news service.
- The Yahoo economic calendar is currently a page link only. Do not display invented event rows or infer an event's time when structured Yahoo data has not been verified.
- Yahoo's current calendar page displays event times in UTC. Do not label them New York time until a validated structured timestamp can be converted correctly.
- No news or calendar component may call TradingView or introduce a backup provider during this phase.

## GEX data limitations

- GEX uses Cboe delayed NDX options data only and is requested on demand.
- The raw options chain is cached for 15 minutes; stale output must be visibly labeled.
- NDX-to-MNQ/NQ levels use a futures/spot ratio and require a fresh cached MNQ price or explicit manual input. GEX must not trigger a TradingView refresh to obtain that price.
- The calls-positive / puts-negative convention is an assumption about dealer positioning, not verified dealer inventory.
- Open interest is generally prior-session data; the options quote feed is delayed. GEX estimates are contextual reference levels, never trade authorization or standalone signals.
- The current GEX integration supports NDX-to-MNQ/NQ mapping only. Do not imply MES or MGC coverage.

## Instrument-agent boundary

- Initial MNQ/MES/MGC agents are deterministic and descriptive; they are not trade signals.
- The instrument-agent endpoint may read only the existing cached 1-minute snapshot through the cache-only accessor. It must never call a provider refresh.
- Reports are blocked if the latest bar timestamp is missing, fewer than 20 valid OHLC bars exist, the latest bar is older than 15 minutes, or the cache marks the snapshot stale.
- Instrument agents have no authority to modify risk settings, approve trades, or submit orders.
- Every report must disclose freshness and confirm zero provider requests triggered by analysis.

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
