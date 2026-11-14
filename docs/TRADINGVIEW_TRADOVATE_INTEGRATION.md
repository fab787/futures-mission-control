# TradingView → FAB787 QUANTUM → Tradovate integration

## Free-first decision

FAB787 QUANTUM will not require a paid webhook bridge subscription. The first implementation is a small, isolated FastAPI receiver inside this repository. It uses the project's existing Python/FastAPI dependencies and SQLite; it does not add a paid service or a second trading platform.

The receiver is an intake boundary only. It does **not** connect to Tradovate, authenticate a brokerage account, create an order, or execute a trade.

## GitHub project review

| Project | License / access | Fit for FAB787 QUANTUM | Decision |
|---|---|---|---|
| [Tradovate official JavaScript API examples](https://github.com/tradovate/example-api-js) | Public repository; no repository license file was found during review | Authoritative examples for OAuth, account discovery and REST order requests. Some sample code selects the first account, which is unsafe for a multi-account prop-firm setup. | Use as API documentation/reference. Write our own account-pinned Python adapter; do not copy code without confirmed reuse permission. |
| [Tradovate official API FAQ](https://github.com/tradovate/example-api-faq) | Public repository | Documents REST vs WebSocket responsibilities and rate-limit penalty behavior. | Use as technical reference. |
| [LuxAlgo Trade Relay](https://github.com/LuxAlgo/trade-relay) | MIT, self-hosted | Good examples of webhook validation, duplicate protection, a local audit trail and fail-closed risk rails. Its documented execution adapters do not currently include Tradovate; its simulator, Alpaca and Tradier execution support does not satisfy our broker requirement. | Do not adopt as the execution engine. Reuse only general design ideas; avoid duplicating our existing risk engine and audit ledger. |
| [Autonomous Futures Paper-Trading System](https://github.com/imanisworld/autonomous-futures-system) | Public, but all rights reserved | Contains a substantial Tradovate demo adapter and webhook flow, but explicitly prohibits reuse/derivative work without written permission. | Do not copy, fork into this project, or adapt its code. |

**Conclusion:** no reviewed repository is both clearly reusable under a permissive license and already a safe, ready-made Tradovate + TradingView bridge that fits our Python app and multi-account safety model. The safest free route is to implement our own small receiver and later use Tradovate's official API documentation to build an account-pinned adapter.

## Current implementation: intake only

Module: `mission_control/tradingview_webhook.py`

Run it as a separate process, not as part of the public Mission Control web server:

```powershell
$env:FAB787_TRADINGVIEW_WEBHOOK_SECRET = "<generate-a-long-random-secret>"
python -m uvicorn mission_control.tradingview_webhook:app --host 127.0.0.1 --port 8765 --workers 1
```

The receiver exposes only:
- `GET /health`
- `POST /webhook/tradingview`
- `GET /inbox?limit=50` (read-only recent signal list; maximum 200)

Do not expose the main Mission Control port to the public internet. If a public HTTPS ingress is configured later, it must forward only to this isolated receiver on port 8765. No tunnel has been configured by this change.

### Accepted alert fields

- `secret`: dedicated webhook secret; never a Tradovate password or API credential.
- `alert_id`: unique per strategy event; used for idempotency.
- `timestamp`: timezone-aware ISO-8601 alert time, preferably TradingView's `{{timenow}}`.
- `symbol`: MNQ, MES or MGC (continuous futures aliases MNQ1!, MES1!, MGC1! are normalized).
- `action`: buy, sell, close or flatten.
- `quantity`: positive whole number for buy/sell, bounded at intake to 100.
- `order_type`: market, limit, stop or stop_limit.
- `entry_price`, `stop_loss`, `take_profit`: required for buy/sell; direction is validated.
- `strategy_id`: identifies the Pine strategy/version.
- `route_key`: optional operator-defined alias only. It is not an account ID and grants no routing authority.

The receiver rejects unknown instruments, malformed fields, bad secrets, timestamps older than 15 minutes, timestamps more than 60 seconds in the future, and incorrectly oriented brackets. Each alert ID is stored once in a local SQLite inbox. The secret is removed before persistence. A read-only inbox endpoint returns a bounded list of safe alert metadata; it never exposes the raw payload or secret.

### Response behavior

A valid first delivery returns HTTP 202 and `status: accepted`. A repeat delivery returns `status: duplicate`. The response always states:

- `execution_enabled: false`
- `human_approval_required: true`

No TradingView market-data tools or Yahoo services are called. No Tradovate API request is made.

## Required next phases

1. Add a local-only Mission Control inbox view and map operator-approved route aliases to registered account IDs. Never accept a broker account ID from an alert as authority.
2. Convert a received signal into a complete risk proposal using the selected account's own risk profile and history. Missing account state, stop/target, session, or account mapping must fail closed.
3. Add a separate Tradovate demo adapter using the official API's OAuth/account/REST/WebSocket guidance. Pin every execution request to an explicitly selected account ID; never use `accounts[0]`.
4. Reconcile order acknowledgments, fills, cancellations, positions and disconnects before considering any funded-account use.
5. Keep order submission disabled until the user explicitly authorizes it and the applicable prop firms confirm automated TradingView-alert execution is permitted.

## Provider and execution boundaries

- TradingView market-data retrieval remains behind the existing centralized request manager. Webhook intake is inbound HTTP and must never call the TradingView MCP.
- Yahoo Finance remains the only current news/calendar provider; no calendar or news changes are part of this work.
- No backup data providers are added.
- No paid bridge, subscription, VPS or tunnel is required for local development. Public webhook delivery from TradingView will eventually require a reachable HTTPS endpoint; free ingress options must be assessed separately for reliability, privacy and operational limits.
- Simulation first. No live order routing or autonomous execution.
