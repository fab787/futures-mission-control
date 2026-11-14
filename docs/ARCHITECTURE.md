# System Architecture

## High-level flow

TradingView MCP (OAuth 2.1 / Streamable HTTP) -> read-only adapter -> schema normalization and freshness checks -> instrument workspaces and, later, analysis agents. No market-data adapter may place orders.

The Master Risk Engine remains a separate control boundary. No AI agent may change risk limits, approve its own proposal, or submit orders.

## Workspaces

- Overview: instrument watchlist, source health and market snapshot.
- Charts: authenticated TradingView launch plus locally rendered historical bars when the MCP adapter is authorized.
- GEX: on-demand Cboe delayed NDX options estimates mapped to MNQ/NQ futures levels; no TradingView request is made by this workspace.
- News & Events: source-linked headlines and scheduled catalyst calendar (adapter wiring is a later step).
- AI Desk: instrument agents, shared specialists, Chief Decision Agent and audit stream.
- Risk & Approvals: deterministic risk checks, veto state and human review queue.
- Integrations: provider capabilities, OAuth state and adapter health.
- Expansion: reserved modules and future instrument/plugin bays.

## TradingView direct MCP integration

FAB787 QUANTUM connects independently to the official endpoint `https://mcp.tradingview.com/mcp` using Streamable HTTP and OAuth 2.1. It does not reuse Claude Desktop's OAuth session or credentials. The one-time authorization command is:

```powershell
python -m mission_control.tradingview_auth
```

The authorization flow opens TradingView sign-in in the browser and stores OAuth tokens and client registration in the operating system keyring. No token is written to the repository, dashboard HTML, or `.env`. If keyring storage is unavailable, authorization fails rather than falling back to plaintext files.

The initial adapter allow-lists only read-only tools: `get_ohlcv`, `get_symbol_data_batch`, `get_news`, and `get_economic_calendar`. Tool access is not a blanket permission: watchlist mutation, alerts, account actions and all write tools are excluded. The current market snapshot requests 120 one-minute bars for configured MNQ, MES and MGC continuous futures symbols. This is historical-bar polling, not a streaming quote subscription. Symbol coverage, exchange permissions, delay and returned payload shape must be verified against the user's TradingView account.

Endpoints:
- `GET /api/tradingview/status`: local OAuth authorization state.
- `GET /api/market-data`: on-demand read-only one-minute history for MNQ/MES/MGC.
- `GET /api/integrations`: integration status and permitted capabilities.

TradingView MCP is beta and rate-limited. Do not poll aggressively. The initial UI should refresh manually or at a conservative interval and show the source and timestamp. If a symbol cannot be resolved or returned data cannot be normalized, display unavailable rather than fabricating a price.

## GEX data path

The GEX workspace uses the Cboe delayed NDX options-chain endpoint as its
current and only options-data provider. It is on-demand, not polled at dashboard
startup. Raw options-chain data is cached for 15 minutes; repeated calculations
with a different MNQ/NQ mapping price reuse the cached chain. Failed refreshes
use clearly labeled stale data when available and apply a five-minute retry
cooldown.

The futures mapping price is taken from the already cached 1-minute MNQ
snapshot only through the cache-only `peek_snapshot()` accessor. That accessor
never calls the TradingView adapter. If no fresh MNQ snapshot exists, the user
must supply a manual MNQ/NQ price. GEX must never call the normal market-data
loader to obtain a mapping price.

The initial implementation supports NDX options mapped to MNQ/NQ. It labels
Cboe's approximately 15-minute delay, prior-session open-interest limitations,
and the calls-positive / puts-negative dealer-positioning assumption. Outputs
are estimates for context, not trade signals. No MES or MGC GEX levels are
claimed by this integration.

## Instrument agents

Each instrument agent has isolated configuration and context:
- MNQ Agent
- MES Agent
- MGC Agent

A selected ticker changes the detailed analysis view, not another instrument's identity or risk configuration. The overview continues to show all enabled instruments.

## Shared specialist agents

- Opportunity Scout: identifies and ranks candidate situations; does not authorize trades.
- News & Catalyst Intelligence: reports scheduled and unscheduled catalysts with source and timestamp.
- Sentiment Analyst: summarizes sourced sentiment and limitations.
- Market Structure Analyst: evaluates structure using validated market data.
- Account Protection Analyst: monitors account-level constraints and emerging risk.

## Decision and risk layers

The Chief Decision Agent compares instrument and specialist reports, highlights disagreement, and drafts proposals with rationale and invalidation conditions. It cannot override the Master Risk Engine.

The risk engine validates permissions, account state, instrument/session limits, exposure, proposed size, stop distance, loss limits and data-health gates. Missing or stale critical inputs must fail closed. Numerical limits must be explicitly configured before simulated trading is enabled.

## Other integration boundaries

Yahoo Finance/news is supplementary context, not a guaranteed real-time futures feed. Every news item must retain publisher, published time, received time and source URL. Do not fabricate headlines or silently substitute stale data.

TradingView chart display, market-history reads, news reads, calendar reads, account reads and simulated order submission are separate capabilities. This adapter enables only a narrow set of read-only market-data calls. No account or order capability is enabled.

## Expansion

The UI reserves workspaces for additional instruments, analytics, strategy research, prop-firm account profiles, notifications, research archive and additional data providers. New modules should use adapters and stable schemas rather than tightly coupling provider code to the dashboard.

## Observability

Every data item and agent report should carry source, instrument, event time, received time and freshness status. Record decisions, risk checks, approvals, rejections and failures in an auditable event log.
