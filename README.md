# Futures Mission Control

A private, modular futures-market intelligence and paper-trading research workspace for MNQ, MES, and MGC.

> **Safety status:** Research/read-only first. Paper trading only during initial development. No live brokerage connection, live order routing, or autonomous execution.

## Initial instruments

- **MNQ** — Micro E-mini Nasdaq-100
- **MES** — Micro E-mini S&P 500
- **MGC** — Micro Gold

Each instrument has an independent analysis profile and its own configurable session, strategy, and risk settings. New instruments should be addable through configuration rather than a dashboard redesign.

## Architecture

- Instrument agents: MNQ, MES, MGC
- Shared specialists: Opportunity Scout, News & Catalyst Intelligence, Sentiment Analyst, Market Structure Analyst, Account Protection Analyst
- Chief Decision Agent: coordinates analysis and produces trade proposals
- Master Risk Engine: independent veto authority; cannot be overridden by AI agents
- Human approval: required for every proposed paper trade

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/SAFETY.md](docs/SAFETY.md).

## Development principles

1. Build a working, observable foundation before adding sophisticated agents.
2. Treat market data as untrusted until source, timestamp, and freshness are verified.
3. Fail safe: stale data or critical component failure blocks new trade proposals.
4. Keep secrets out of Git. Use local environment variables and an untracked `.env` file.
5. Do not assume an MCP server is directly callable by this standalone dashboard. Validate each integration path before implementing it.
6. TradingView Paper Trading is the intended initial simulation venue, but account/position/order capabilities must be verified before depending on them.

## Planned build sequence

1. Dashboard and application foundation
2. Read-only data adapters and source-health reporting
3. Independent instrument analysis
4. Shared specialist analysis
5. Chief Decision Agent
6. Master Risk Engine and human approval workflow
7. Verified TradingView Paper Trading integration
8. Testing, replay, and iterative tuning

## Current status

**Stage 1 — TradingView MCP adapter scaffold.** Direct OAuth 2.1 authorization, OS-keyring token storage, and a read-only historical OHLCV API for MNQ/MES/MGC are implemented on a feature branch. The user must authorize this local app and verify symbol coverage before prices appear. Data is polled, not streamed. AI agents, broker adapter and order execution remain disabled.

## TradingView connection

After pulling the approved update and installing dependencies, run:

```powershell
python -m mission_control.tradingview_auth
```

Approve the OAuth request in TradingView. Tokens are stored in the OS keyring. Then restart Mission Control. The Overview and Charts workspaces request historical bars through `/api/market-data`. This is a separate authorization from Claude Desktop. Only read-only market history is enabled; no watchlist changes, alerts, account access or order tools are allowed.
