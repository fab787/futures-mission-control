# System Architecture

## High-level flow

TradingView MCP (OAuth 2.1 / Streamable HTTP) -> read-only adapter -> schema normalization and freshness checks -> instrument workspaces and, later, analysis agents. No market-data adapter may place orders.

The Master Risk Engine remains a separate control boundary. No AI agent may change risk limits, approve its own proposal, or submit orders.

## Workspaces

- Overview: instrument watchlist, source health and market snapshot.
- Charts: authenticated TradingView launch plus locally rendered historical bars when the MCP adapter is authorized.
- GEX: on-demand Cboe delayed NDX options estimates mapped to MNQ/NQ futures levels; no TradingView request is made by this workspace.
- News & Events: cached Yahoo Finance ticker headlines plus a link to Yahoo Finance's economic calendar; structured calendar extraction remains unverified.
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

The TradingView adapter allow-lists only `get_ohlcv` and `get_symbol_data_batch`. News and economic-calendar capabilities are intentionally excluded from TradingView. Tool access is not a blanket permission: watchlist mutation, alerts, account actions and all write tools are excluded. The current market snapshot requests 120 one-minute bars for configured MNQ, MES and MGC continuous futures symbols. This is historical-bar polling, not a streaming quote subscription. Symbol coverage, exchange permissions, delay and returned payload shape must be verified against the user's TradingView account.

Endpoints:
- `GET /api/tradingview/status`: local OAuth authorization state.
- `GET /api/market-data`: on-demand read-only one-minute history for MNQ/MES/MGC.
- `GET /api/gex`: on-demand Cboe delayed NDX options GEX, mapped to MNQ/NQ using a cache-only MNQ snapshot or manual price.
- `GET /api/integrations`: integration status and permitted capabilities.

TradingView MCP is beta and rate-limited. Do not poll aggressively. The initial UI should refresh manually or at a conservative interval and show the source and timestamp. If a symbol cannot be resolved or returned data cannot be normalized, display unavailable rather than fabricating a price.

## News and economic calendar data path

Yahoo Finance is the sole news/calendar provider in this implementation phase.
Ticker headlines use one combined Yahoo Finance RSS request for MNQ=F, MES=F,
MGC=F and the QQQ, SPY and GLD reference proxies. The result is cached for ten
minutes, concurrent refreshes are coalesced, and failures trigger a fifteen-minute
cooldown with stale-data fallback. The dashboard does not poll this feed on a
timer; it loads once on startup and checks the cached API when the user asks.
Headline publisher, published timestamp, received timestamp and source URL are
preserved. News timestamps are rendered in America/New_York.

Yahoo Finance's economic calendar is currently exposed as a page link only.
The page can be opened from the News & Events workspace, but a stable structured
event payload has not yet been verified. The dashboard therefore does not
fabricate calendar rows or silently infer release times. Yahoo's page currently
displays event times in UTC; New York conversion and in-dashboard event rows
remain blocked until the underlying Yahoo event fields and timezone semantics
are verified. This is a deliberate limitation, not a second-provider fallback.

Neither Yahoo news nor the Yahoo calendar path calls TradingView. TradingView's
tool allow-list is restricted to historical market data and symbol data.

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

## Initial instrument-agent implementation

The first instrument-agent slice is deterministic and cache-only. MNQ, MES and
MGC each receive an isolated report built from the already-cached 1-minute
market snapshot. The `GET /api/agents/instruments` endpoint uses only
`market_data_manager.peek_snapshot()`; it never calls the snapshot loader,
TradingView MCP, Yahoo Finance, Claude, or ChatGPT.

Reports summarize the last 20-bar direction, recent 20-bar high/low, ATR-style
14-bar range and relative volume when available. Each report includes source,
latest-bar timestamp, data age and cache status. Missing timestamps, fewer than
20 valid OHLC bars, stale bars older than 15 minutes, or a stale cache block
the report instead of allowing stale context to appear current.

This is a descriptive baseline, not a predictive model, trade signal, strategy,
or risk approval. Instrument agents have no risk-setting, account, or order
authority. The next layer can add shared specialists and independent AI
reviews only after stable report contracts and provider boundaries are tested.

## Shared specialist agents

- Opportunity Scout: identifies and ranks candidate situations; does not authorize trades.
- News & Catalyst Intelligence: reports scheduled and unscheduled catalysts with source and timestamp.
- Sentiment Analyst: summarizes sourced sentiment and limitations.
- Market Structure Analyst: evaluates structure using validated market data.
- Account Protection Analyst: monitors account-level constraints and emerging risk.









### Account Protection Analyst

The Account Protection Analyst is exposed at
`POST /api/agents/account-protection`. It evaluates an explicitly supplied
risk-limit configuration and point-in-time account snapshot. It does not
connect to a broker, read positions from an account, persist account state,
change settings, or continuously monitor an account. No default account size
or prop-firm rules are assumed.

Required limits are maximum daily loss, maximum session loss, maximum
high-water drawdown, and maximum open contracts. Required state includes
daily/session P&L, drawdown from high water, open contract count, a
verified flat/open position state, and a timezone-aware `snapshot_at` timestamp.
Snapshots older than 120 seconds or more than 60 seconds in the future are
blocked; timestamps without a timezone are invalid. Optional profit-target and profit-protection
settings are supported. When profit protection is configured, the caller must
also provide the day's realized high-water P&L so a giveback rule can be
evaluated correctly. A configured cap is treated as the maximum permitted
value; exceeding it is a breach.

The analyst reports limit utilization, warnings at 80%, breaches, profit-target
status, and data-validation failures. Missing or invalid limits/state, unknown
position state, and inconsistent position snapshots fail closed. Reaching a
configured profit target blocks new proposals in this assessment output.
A breach is a veto condition for new proposals, not an instruction to flatten
an existing position. The independent Master Risk Engine remains the final
risk veto and human approval remains mandatory.

### Sentiment Analyst

The Sentiment Analyst exposes a provider-independent review contract through
`GET /api/agents/sentiment` and a comparison endpoint at
`POST /api/agents/sentiment/compare`. Claude and ChatGPT are represented as
separate analysis resources, but neither is automatically connected or called
by this implementation. Structured reviews can be submitted by an external
workflow; the comparison endpoint does not persist them or trigger market/news
provider requests.

Each review must identify its provider, model label, timezone-aware analysis
timestamp, cited HTTPS source URLs and instrument-specific findings for MNQ,
MES or MGC. Reviews older than 15 minutes, malformed timestamps, unknown
symbols/stances, missing citations and duplicate provider submissions are
rejected. The response preserves each provider's view and source attribution.
It reports agreement, disagreement, a single-provider result, or unavailable;
it never averages away disagreement or treats model consensus as proof.

This phase does not assume Claude or OpenAI API credentials, paid API access,
or an always-on external service. The boundary is ready for separately
authorized adapters later. The analyst has no market-data, risk, or trade
authority and generates no sentiment-based orders or trade proposals.

### Opportunity Scout

The Opportunity Scout endpoint, `GET /api/agents/opportunity-scout`, combines
the existing instrument-agent reports, Market Structure results and cache-only
News & Catalyst context. It reads the existing 1-minute TradingView snapshot
once through `peek_snapshot()` and Yahoo headlines once through
`peek_news()`. It does not call either provider's refresh method.

Eligible instruments receive a transparent 0-80 deterministic confluence
score: directional agreement (0-30), confirmed swing agreement (0-25),
relative-volume context (0-15), and proximity to a confirmed support/resistance
level within one ATR (0-10). Missing evidence earns no points. The score is
not a probability, forecast, expected return, win rate, or backtest result.
Ranks compare descriptive context across the enabled watchlist; they do not
identify actionable entries.

The Scout never generates entry/exit prices, stops, targets, contract sizing,
or trade proposals. It has no risk or trade authority. Any future proposal
workflow must remain separate, pass the independent Master Risk Engine, and
require explicit human approval.

### News & Catalyst Intelligence

The specialist reads only the existing Yahoo Finance RSS cache through
`yahoo_news_service.peek_news()`. Its `GET /api/agents/news-catalyst`
endpoint never calls `get_news()` and cannot initiate an RSS request. It
classifies headline text into broad catalyst themes (monetary policy,
macro-economic data, geopolitical/trade, equity index, technology earnings,
precious metals and energy) and applies simple keyword-based triage labels.
The labels do not infer market direction, surprise, impact or a trade setup.

The combined Yahoo feed does not reliably map each story to an individual
futures contract. The specialist therefore preserves the full watchlist feed
scope and explicitly marks per-headline instrument mapping unavailable. Stale
headlines remain visibly stale context. The Yahoo economic calendar remains
page-link-only; the specialist creates no event rows or release times.

### Market Structure Analyst

The first shared specialist consumes the existing cache-only instrument reports
and the same cached 1-minute OHLCV snapshot. Its endpoint,
`GET /api/agents/market-structure`, uses only `peek_snapshot()`; it never
refreshes TradingView or any other provider. It is blocked per instrument when
the base report is not ready or fewer than 50 valid bars are available.

For eligible instruments it reports EMA-20/EMA-50 alignment, confirmed swing
highs and lows (two bars on each side to confirm), nearest confirmed support
and resistance, and a basic higher-high/higher-low versus lower-high/lower-low
description. Unconfirmed pivots are excluded. These are descriptive levels,
not forecasts, entries, stop placements, or trade approvals. The specialist has
no risk or trade authority and reports zero provider requests.

## Decision and risk layers

The Chief Decision Agent compares instrument and specialist reports, highlights disagreement, and drafts proposals with rationale and invalidation conditions. It cannot override the Master Risk Engine.

The risk engine validates permissions, account state, instrument/session limits, exposure, proposed size, stop distance, loss limits and data-health gates. Missing or stale critical inputs must fail closed. Numerical limits must be explicitly configured before simulated trading is enabled.

## Other integration boundaries

Yahoo Finance/news is supplementary context, not a guaranteed real-time futures feed. Every news item must retain publisher, published time, received time and source URL. Do not fabricate headlines or silently substitute stale data.

TradingView chart display and market-history reads are separate from Yahoo news and calendar retrieval. TradingView enables only a narrow set of read-only market-data calls; Yahoo handles the news RSS feed and calendar page link. No account or order capability is enabled.

## Expansion

The UI reserves workspaces for additional instruments, analytics, strategy research, prop-firm account profiles, notifications, research archive and additional data providers. New modules should use adapters and stable schemas rather than tightly coupling provider code to the dashboard.

## Observability

Every data item and agent report should carry source, instrument, event time, received time and freshness status. Record decisions, risk checks, approvals, rejections and failures in an auditable event log.


## Chief Decision Agent

The Chief Decision Agent coordinates the existing instrument, Market Structure,
Opportunity Scout, News & Catalyst, Sentiment, and Account Protection reports.
Its `POST /api/agents/chief-decision` endpoint reads the existing market and
Yahoo caches only; caller-supplied sentiment reviews and account snapshots are
validated without external provider or broker calls. Missing, stale, invalid,
or contradictory market/account inputs block downstream risk review. Model
disagreement and unavailable news are surfaced as caveats rather than silently
converted into consensus. The agent produces a context and readiness summary
only: it does not generate trade setups, alter settings, or authorize orders.
Every possible proposal remains subject to the independent Master Risk Engine
and explicit human approval.


## Independent Master Risk Engine

The Master Risk Engine is exposed at `POST /api/risk/master` as a pure,
independent veto layer. It validates a fresh caller-supplied account snapshot,
Account Protection status, explicit per-trade risk and account contract limits,
permitted NY/Globex session, instrument identity, proposed quantity, stop and
target orientation, and estimated stop-distance risk. Missing, stale,
contradictory, warning, breached, or invalid account/risk inputs veto the
proposal. Contract multipliers used for estimates are MNQ $2, MES $5 and MGC
$10 per full point. Stop-risk estimates exclude fees, slippage and gaps.

A non-veto result means only eligible for separate human review. It is never
trade approval or permission to submit an order. The endpoint has no provider,
broker, persistence, configuration-write, position-management, or execution
capability. The engine may veto; it cannot authorize itself or override the
mandatory human approval boundary.
