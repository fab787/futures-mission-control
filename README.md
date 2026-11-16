# FAB787 QUANTUM

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

**Foundation and provider-protection phase.** The isolated working branch includes read-only TradingView history for MNQ/MES/MGC behind a centralized five-minute cache, cross-timeframe request serialization, quota cooldowns, stale-data handling, and conservative dashboard polling. The native GEX workspace uses Cboe delayed NDX options data on demand, cached for 15 minutes, and maps levels to MNQ/NQ using an already cached MNQ price or a manual price. GEX does not make TradingView requests. News uses one combined Yahoo Finance ticker RSS request with a ten-minute cache and failure cooldown; Yahoo's economic calendar is linked externally until its structured event feed is verified. TradingView is excluded from news and calendar retrieval. AI agents, broker adapter and order execution remain disabled. All changes are under draft PR review; nothing has been merged.

## MNQ bot (first dedicated instrument bot)

`mission_control/mnq_bot.py` wraps the existing deterministic instrument analysis with MNQ-only settings and inputs.

- **Cache-only.** It reads an already-cached snapshot and never calls TradingView, Webull, Yahoo, or any AI provider.
- **Isolated.** The snapshot is filtered to MNQ rows before analysis, so MES or MGC data cannot leak into the MNQ context.
- **Fail-safe.** Missing, stale, or malformed data returns `blocked` with a reason. A disabled profile returns `disabled` and runs no analysis.
- **Analysis only.** Reports carry `trade_authority: none`, `trade_proposals_created: 0`, `order_execution: disabled`, and `human_approval_required: true`.
- **Not a strategy.** It describes market structure. It does not produce entry, stop, or target signals.

Open items before this bot moves beyond analysis: fill in the verified MNQ contract specification (`multiplier`, `tick_size`), define the MNQ strategy instructions, and confirm how the dashboard should display the bot's status.

Run its tests with:

```powershell
python -m pytest tests/test_mnq_bot.py -q
```

## TradingView connection

After pulling the approved update and installing dependencies, run:

```powershell
python -m mission_control.tradingview_auth
```

Approve the OAuth request in TradingView. Tokens are stored in the OS keyring. Then restart FAB787 QUANTUM. The Overview and Charts workspaces request historical bars through `/api/market-data`. This is a separate authorization from Claude Desktop. Only read-only market history is enabled; no watchlist changes, alerts, account access or order tools are allowed.

## Account statement CSV import

The Risk & Approvals workspace includes a local CSV statement preview. Select the risk profile, upload a CSV, confirm the column mappings, and provide the starting balance. The preview validates timezone-aware equity timestamps, daily results, NY/GLOBEX session results, duplicate dates/timestamps, numeric values, and required fields. The preview does not save data. Review the generated normalized JSON and explicitly use the existing save action to create an immutable account-history revision. Imports remain operator-supplied and are not broker-authenticated; no market-data provider or broker is contacted.
