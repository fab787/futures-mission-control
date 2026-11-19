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

The isolated receiver exposes only:
- `GET /health`
- `POST /webhook/tradingview`
- `GET /inbox?limit=50` (read-only recent signal list; maximum 200)
- `GET /api/webhooks/tradingview/inbox?limit=50` (Mission Control's read-only proxy to the local inbox)

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

Mission Control also adds a **Signals** workspace with a read-only view of the latest 100 alerts. It shows action, symbol, quantity, bracket values, strategy, received time and the unverified route alias. It explicitly labels alerts as signals rather than orders or fills. The dashboard never approves or executes them.

### Explicit account-to-risk-review workflow

Mission Control's Signals workspace lets the operator select one exact active account from the local Account Registry and submit a BUY/SELL alert to `POST /api/approvals/proposals/from-webhook/{alert_id}`.

- The selected account's own saved risk profile is attached server-side; the TradingView `route_key` is informational only and never authorizes or selects an account.
- The operator must enter the actual account snapshot timestamp, current session, daily/session P&L, drawdown, open contracts and position state. Snapshots older than the Account Protection limit (120 seconds) are vetoed.
- The alert must still be within the 15-minute signal freshness window and must not already have been submitted.
- The existing Master Risk Engine re-evaluates the account-specific risk profile. A result can be vetoed or become pending human review; neither result submits an order.
- CLOSE/FLATTEN alerts remain visible for review but cannot create a new entry proposal.
- Tradovate and NinjaTrader are not connected. No broker position or account values are assumed to be synchronized.

This is intentionally operator-selected account routing, not automatic execution routing. Broker-derived account snapshots and persistent route-alias administration are future work after secure broker authentication is implemented.

### Response behavior

A valid first delivery returns HTTP 202 and `status: accepted`. A repeat delivery returns `status: duplicate`. The response always states:

- `execution_enabled: false`
- `human_approval_required: true`

No TradingView market-data tools or Yahoo services are called. No Tradovate API request is made.

## Tradovate API access: critical prop-account limitation

The public Tradovate API is **not a safe assumption for evaluation or simulated-funded prop accounts**.

- Tradovate's API documentation describes separate live, demo/simulation and market-data service hosts. Its API-access onboarding requirements include a LIVE Tradovate account with more than $1,000 equity and an API Access subscription.
- NinjaTrader's official support article for Tradovate API Access (updated September 16, 2026) explicitly says prop-firm and evaluation accounts are not eligible for this API add-on. It also states the API subscription does not provide real-time market data.
- Therefore, a Tradovate username, a Tradovate-powered prop account, or the ability to log into Tradovate does **not** establish API eligibility. A demo API endpoint also does not override the account eligibility requirements.
- The project must not ask for, store or test API credentials on the assumption that an evaluation account is eligible. Do not add a paid API subscription to the user's free-first plan without a confirmed eligible use case and explicit approval.

### Current prop-firm platform evidence (not API authorization)

| Firm | Officially documented platform facts | What remains unconfirmed |
|---|---|---|
| Phidias | Advertises native TradingView execution on its Tradovate offering, without a third-party bridge. | Whether the user's account type permits any external API, webhook relay or programmatic order routing outside that native integration. |
| Lucid | Lists Tradovate, NinjaTrader and TradingView on its CQG platform path; its rules say automated strategies and trade copiers are permitted when compliant. | Whether Lucid grants external Tradovate API access or supports a third-party app placing orders into its prop accounts. |
| BluSky | Lists Tradovate Prop and TradingView among supported platforms. | Whether any external API access is enabled or authorized for evaluation / simulated-funded accounts. |
| Legends | No authoritative API permission confirmation is recorded in this review. | Supported account-specific connection path and written permission for external API automation. |

Platform compatibility is not the same as API permission. Each firm/account type must be verified before enabling an execution adapter. Keep firm-specific policy status as unknown until supported by its current official terms or written support confirmation.

### Integration direction

1. Keep the TradingView receiver and Master Risk Engine independent of the broker.
2. Add a small broker-capability registry so each account records its actual provider/platform, account phase, allowed integration method, and verification status.
3. Prefer a firm's documented native TradingView integration where it meets the user's workflow and policy requirements; do not pretend it passes through FAB787 QUANTUM's approval engine if it does not.
4. For a firm that explicitly permits external automation, implement only its documented and supported connection method. Tradovate's standard API must not be selected for prop evaluation accounts unless Tradovate/NinjaTrader and the firm confirm eligibility in writing.
5. Keep the local operator approval and risk-veto stages mandatory. No automatic fallback to another broker/platform is allowed.

## Per-account broker capability registry

The project now records broker/platform capability separately for every registered account, with editable controls inside the Mission Control Accounts workspace:

- `GET /api/broker-capabilities` lists the known provider, platform, intended integration method, permission status and evidence reference for each account.
- `PUT /api/accounts/{account_id}/broker-capability` records the operator-reviewed capability for one exact registered account.
- New accounts default to `unknown` / `unverified`; no platform or permission is inferred from a firm name.
- Permission values are `unverified`, `confirmed` or `prohibited`. A `confirmed` status requires an evidence reference. Prohibited automation can only be recorded as `manual_only`.
- Capability records contain no credentials, do not contact any provider, and never enable execution. The API always reports `execution_enabled: false` and `orders_created: 0`.

This registry is an operator-maintained evidence ledger, not automatic verification. Only official current terms or written support confirmation should be recorded as evidence. An account with unverified or prohibited permissions is not eligible for a future automated adapter.

## Required next phases

1. Complete broker-capability registry and per-firm/account permission tracking; keep unknown permissions fail-closed.
2. Confirm the actual supported external integration path for each account type with the firm and platform provider.
3. Implement a non-ordering demo connectivity adapter only for an account/API combination explicitly documented as eligible.
4. Reconcile account state, order acknowledgments, fills, cancellations and disconnects before considering any funded-account use.
5. Keep order submission disabled until the user explicitly authorizes it and the applicable prop firms confirm the exact automation path is permitted.

## Provider and execution boundaries

- TradingView market-data retrieval remains behind the existing centralized request manager. Webhook intake is inbound HTTP and must never call the TradingView MCP.
- Yahoo Finance remains the only current news/calendar provider; no calendar or news changes are part of this work.
- No backup data providers are added.
- No paid bridge, subscription, VPS or tunnel is required for local development. Public webhook delivery from TradingView will eventually require a reachable HTTPS endpoint; free ingress options must be assessed separately for reliability, privacy and operational limits.
- Simulation first. No live order routing or autonomous execution.
