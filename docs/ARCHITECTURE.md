# System Architecture

## High-level flow

Market/data adapters -> schema validation and freshness checks -> isolated instrument agents and shared specialists -> Chief Decision Agent -> independent Master Risk Engine -> human approval queue -> paper-trading adapter (only after explicit authorization and verification).

The Master Risk Engine is a separate control boundary. It can reject any proposal. No AI agent may change risk limits, approve its own proposal, or submit orders.

## Workspaces

- Overview: instrument watchlist, service health and market snapshot.
- Charts: TradingView hosted chart embed with instrument and timeframe selection.
- News & Events: source-linked headlines and scheduled catalyst calendar.
- AI Desk: instrument agents, shared specialists, Chief Decision Agent and audit stream.
- Risk & Approvals: deterministic risk checks, veto state and human review queue.
- Integrations: provider capabilities, connection state and adapter health.
- Expansion: reserved modules and future instrument/plugin bays.

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

## Integration boundaries

The TradingView chart in the dashboard is a hosted third-party embed. It is not a connection to the user's Claude Desktop TradingView MCP and does not feed the local API or AI agents. Quote access and delay are controlled by TradingView.

Claude Desktop MCP servers are not automatically available to a standalone FastAPI application. A separate local adapter and capability tests are required before MCP data can be consumed. Treat chart display, quote/history reads, study values, news, account reads and simulated order submission as separate capabilities.

Yahoo Finance/news is supplementary context, not a guaranteed real-time futures feed. Every news item must retain publisher, published time, received time and source URL. Do not fabricate headlines or silently substitute stale data.

## Expansion

The UI reserves workspaces for additional instruments, analytics, strategy research, prop-firm account profiles, notifications, research archive and additional data providers. New modules should use adapters and stable schemas rather than tightly coupling provider code to the dashboard.

## Observability

Every data item and agent report should carry source, instrument, event time, received time and freshness status. Record decisions, risk checks, approvals, rejections and failures in an auditable event log.
