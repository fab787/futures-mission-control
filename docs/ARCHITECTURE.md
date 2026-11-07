# System Architecture

## High-level flow

Market/data adapters -> data validation and freshness checks -> instrument agents and shared specialists -> Chief Decision Agent -> Master Risk Engine -> human approval queue -> paper-trading adapter (only after verified integration).

The Master Risk Engine is a separate control boundary. It can reject any proposal. No AI agent may change risk limits, approve its own proposal, or submit orders.

## Instrument agents

Each instrument agent has an isolated configuration and context:
- MNQ Agent
- MES Agent
- MGC Agent

A selected ticker changes the detailed analysis view, not the identity or risk configuration of another instrument. The overview continues to show all enabled instruments.

## Shared specialist agents

- Opportunity Scout: identifies and ranks candidate situations; does not authorize trades.
- News & Catalyst Intelligence: reports scheduled and unscheduled catalysts with source and timestamp.
- Sentiment Analyst: summarizes sourced sentiment and its limitations.
- Market Structure Analyst: evaluates structure using available, validated market data.
- Account Protection Analyst: monitors account-level constraints and emerging risk.

## Decision layer

The Chief Decision Agent compares the instrument and specialist reports, highlights disagreement, and drafts a trade proposal with rationale and invalidation conditions. It cannot override the Master Risk Engine.

## Master Risk Engine

The risk engine is deterministic wherever practical. It validates permissions, account state, instrument limits, session limits, exposure, proposed size, stop distance, loss limits, and data-health gates. Initial numerical limits remain configurable and must be explicitly chosen before simulated trading is enabled.

## Data and integration boundaries

TradingView is the preferred chart/market-data source. Yahoo Finance is supplementary for relevant market context, news, and reference information; it must not be treated as a guaranteed real-time futures feed.

Local MCP servers are not automatically available to a standalone web application. Each integration requires a verified transport/adapter and explicit capability tests. Data access, chart interaction, paper-account reads, and simulated order submission are distinct capabilities and must be tested separately.

## Observability

Every data item and agent report should carry source, instrument, event time, received time, and freshness status. Record decisions, risk checks, approvals, rejections, and failures in an auditable event log.
