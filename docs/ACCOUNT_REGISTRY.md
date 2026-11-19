# FAB787 QUANTUM Multi-Account Registry

## Purpose

The local registry separates each evaluation or funded account, including multiple accounts held at the same prop firm. Current supported firms are Phidias, Lucid, BluSky, and Legends. A separate personal Webull account is intentionally deferred.

## Account identity and isolation

Each account has its own generated account ID, firm, operator nickname, account mode (evaluation/funded), lifecycle status (active/inactive/completed), account size, optional operator-entered current balance, and linked risk-profile ID.

Every account must link to a distinct saved risk profile. The profile's firm and evaluation/funded mode must match the registered account. The one-to-one profile constraint preserves the existing profile-scoped account-history ledger and prevents one account's history from being silently reused for another account.

Firm, account mode, account size, and risk-profile linkage are fixed after registration. Nickname, lifecycle status, and operator-entered balance can be updated. Each create/update action adds a local account event.

## API

- `GET /api/accounts?include_inactive=true`: list accounts; set false to show active accounts only.
- `POST /api/accounts`: register an account. Required fields: `firm`, `nickname`, `account_mode`, `account_size`, and `risk_profile_id`. Optional fields: `status`, `current_balance`, and `actor`.
- `PATCH /api/accounts/{account_id}`: update only `nickname`, `status`, and/or `current_balance`.
- `GET /api/accounts/{account_id}/events`: inspect that account's local change history.

## Safety and limitations

- Local SQLite only; no prop-firm login, broker connection, account scraping, or credentials.
- No TradingView, Yahoo Finance, Cboe, or other provider calls.
- Balances and account details are operator-entered and are not authenticated against a firm.
- No orders, execution, or autonomous trading.
- Account IDs and actor labels are local identifiers, not authentication. Keep the application bound to localhost and do not expose these endpoints to an untrusted network without adding authentication.
- Synthetic fixtures are used in tests; no real account information is required to develop or validate this feature.
