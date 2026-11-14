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



## Account Protection Analyst boundary

- The analyst evaluates only explicitly supplied account snapshots and risk limits; it has no broker/account connection in this phase.
- Never assume account size, prop-firm rules, loss limits, contract caps, or profit-protection settings. Missing required inputs block the assessment.
- Account snapshots must include a timezone-aware `snapshot_at`; snapshots older than 120 seconds or more than 60 seconds in the future are blocked.
- Position state must be verified as flat or open and consistent with the supplied open-contract count. Unknown or contradictory exposure blocks downstream decisions.
- Breaches and reached profit targets block new trade proposals; warnings require attention. The analyst cannot close positions, cancel orders, or alter risk settings.
- Profit-protection giveback checks require the day's P&L high-water value; current P&L alone cannot prove whether an activation threshold was previously reached.
- This is point-in-time assessment, not continuous monitoring or broker-side enforcement.
- The independent Master Risk Engine retains veto authority and explicit human approval remains mandatory.

## Sentiment Analyst boundary

- Claude and ChatGPT are separate analysis resources; no API calls or paid service assumptions are enabled in this phase.
- Only structured external reviews with provider identity, timezone-aware analysis timestamp, instrument scope and cited HTTPS sources may be compared.
- Reviews older than 15 minutes, malformed reviews, unknown instruments/stances and duplicate provider submissions must be rejected.
- Preserve provider disagreement; never average conflicting views into artificial consensus.
- Model agreement is not proof of correctness and is never a trade signal.
- Sentiment analysis cannot refresh market data or news, change risk settings, approve trades, or submit orders.
- Every future trade proposal remains subject to the independent Master Risk Engine and explicit human approval.

## Opportunity Scout boundary

- Opportunity Scout may read only the existing instrument, Market Structure and Yahoo news cache results.
- It must not call TradingView or Yahoo provider refresh methods, Claude, ChatGPT, or any backup provider.
- Context scores are deterministic confluence points, not probabilities, forecasts, expected returns, or win rates.
- It must not generate entries, exits, stop-losses, profit targets, contract sizes, or executable trade proposals.
- It has no risk authority and no trade authority. Any future trade proposal must pass the independent Master Risk Engine and explicit human approval.

## News specialist boundary

- News & Catalyst Intelligence may read only the existing Yahoo RSS cache through the cache-only accessor.
- It must not call the Yahoo refresh method, TradingView, or any backup provider.
- Headline theme and priority tags are keyword-based triage, not market-impact or directional assessments.
- Combined-feed headlines must not be attributed to a specific futures contract without source-provided mapping.
- Stale headlines must remain labeled stale; unavailable headlines block the specialist.
- The specialist must not create economic-calendar events while Yahoo structured event extraction remains unverified.

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


## Chief Decision Agent boundary

- The Chief Decision Agent may coordinate specialist outputs but may not bypass or replace the independent Master Risk Engine.
- It reads TradingView market data and Yahoo headlines only through existing cache-only accessors; it must never trigger provider refreshes.
- Claude/ChatGPT reviews and account snapshots are caller-supplied, validated inputs; no automatic AI API or broker calls are enabled.
- Missing, stale, malformed, or contradictory market/account inputs block downstream risk review. Model disagreement and missing news must be disclosed, not hidden.
- The agent creates no entries, exits, stops, targets, contract sizes, or orders. It has no trade or risk-setting authority.
- Master Risk Engine review and explicit human approval remain mandatory for every trade.


## Master Risk Engine veto boundary

- The Master Risk Engine is independent of the Chief Decision Agent and Account Protection Analyst; it may veto but never approve or submit orders.
- It requires explicit maximum per-trade risk, maximum account exposure, allowed session(s), a fresh account snapshot, and a complete proposal.
- Unknown instruments, invalid stop/target orientation, excess risk, excess projected contracts, disallowed or mismatched session, account warnings/breaches, and missing/stale/contradictory inputs veto the proposal.
- A non-veto result means eligible for human review only. It is not trade approval; human approval remains mandatory.
- Stop-risk calculations exclude fees, slippage, gaps and execution uncertainty and must never be represented as guaranteed maximum loss.
- No broker connection, order submission, automatic flattening, provider refresh, risk-setting mutation or strategy update is permitted.


## Human approval and audit controls

- Every proposal must pass the independent Master Risk Engine before entering the pending queue; the engine is re-run at decision time.
- A vetoed proposal cannot be approved. A pending proposal can receive exactly one final decision; repeat or conflicting decisions are rejected.
- Approval and rejection require an actor label and written reason. Each event is appended to a local SQLite ledger with UTC timestamp and proposal ID.
- SQLite triggers prevent update/delete of audit-event rows through the application database connection. This is not cryptographic tamper-proof storage or an external immutable archive.
- Actor labels are supplied by the caller and are not authenticated in this phase. Bind the application to localhost and do not expose approval endpoints to an untrusted network.
- Approval records a human decision only. No broker connection, order submission, execution, automatic position management or risk-setting change exists.
