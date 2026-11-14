# Proactive Trade Signal Contract

FAB787 QUANTUM supports a three-stage, indicator-owned signal lifecycle. The webhook receiver transports events; it does not calculate or invent indicator conditions.

## Events
- `approaching_buy`: early warning that the selected strategy reports a developing long setup. Informational only; no entry proposal can be created.
- `approaching_sell`: early warning that the selected strategy reports a developing short setup. Informational only; no entry proposal can be created.
- `buy` / `sell`: the strategy's actual entry trigger. The strategy supplies its own entry, stop-loss, take-profit, quantity, and order type. These enter the existing account-specific Master Risk review; a human still decides whether to execute manually.
- `invalidated`: the strategy says the developing setup is no longer valid. The dashboard visibly cancels the earlier warning.
- `close` / `flatten`: exit/position-management notifications; review-only in this phase.

## Required fields
Every event includes `secret`, unique `alert_id`, timezone-aware `timestamp`, supported `symbol` (MNQ/MES/MGC), `action`, and `strategy_id`.

For `buy` and `sell`, additionally provide `quantity`, `entry_price`, `stop_loss`, and `take_profit`. BUY bracket orientation is stop < entry < target; SELL is target < entry < stop.

For early warnings and invalidation events, order quantity and bracket prices are omitted. Use a unique alert ID for each event so an early warning and its later trigger are recorded independently.

## Safety / truthfulness
- An early warning is not an entry trigger.
- Entry, stop, and target values must come from the selected Pine strategy; FAB787 does not fabricate them.
- Stale alerts are rejected. Duplicate alert IDs are idempotent.
- A triggered entry still requires a fresh operator-entered account snapshot, the independent Master Risk Engine, and explicit human review.
- No broker orders are submitted. Execution remains disabled.
- Actual early warnings appear only after the chosen TradingView strategy emits these event types.
- Strategy selection/version and exact alert expressions must be verified before producing a final TradingView alert template.
