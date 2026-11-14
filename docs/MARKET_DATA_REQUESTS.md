# Centralized Market Data Request Policy

FAB787 QUANTUM must route every TradingView market-data request through
`mission_control.market_data_manager.market_data_manager`. Dashboard panels,
instrument agents, GEX, and future analysis modules must not call the TradingView
adapter directly.

## Current safeguards

- One process-wide cache per requested timeframe.
- Successful snapshots are cached for 5 minutes.
- Empty/unavailable snapshots are cached for 30 seconds to avoid rapid retry loops.
- Concurrent requests for the same timeframe share one in-flight refresh.
- A single global upstream gate serializes refreshes across different timeframes, preventing overlapping TradingView MCP sessions when several charts are requested together.
- TradingView instrument requests remain sequential to protect OAuth refresh state.
- Quota detection covers MCP error payloads and transport-level HTTP 429/rate-limit exceptions; a detected response starts a 15-minute cooldown.
- During cooldown, a previous good snapshot is served as stale with its age and
  cooldown countdown; if no good snapshot exists, the API returns HTTP 429.
- A failed refresh does not replace a previous good snapshot with empty data.
- There is no force-refresh parameter that bypasses the cooldown.
- Cache state and counters are returned in the market-data response for visibility.

## Dashboard polling

The Overview's background market-data check is reduced to once every 5 minutes.
Opening the Charts workspace reuses the browser's existing timeframe snapshot and only requests data when that timeframe has not been loaded in the current page. Changing timeframe or explicitly reloading may request a snapshot, but the server cache prevents an upstream call while that timeframe remains fresh. Changing the chart symbol does not request new market data because each snapshot already contains all three configured instruments.

## GEX provider isolation

The GEX workspace uses Cboe's delayed NDX options-chain endpoint only. It does
not call TradingView. The raw options chain is cached for 15 minutes and is
requested only when a user explicitly loads GEX; changing the MNQ/NQ mapping
price recalculates levels from the cached chain. A five-minute failure cooldown
and stale-data labeling prevent repeated failed Cboe requests.

GEX reads the existing 1-minute MNQ snapshot through
`market_data_manager.peek_snapshot()`, which is cache-only and never invokes
the TradingView loader. If no fresh MNQ snapshot exists, the user must enter a
futures price manually. The endpoint must never call `get_snapshot()` merely
to obtain a mapping price.

## Important deployment note

The cache and cooldown are in-process memory. Run a single FAB787 QUANTUM API
worker for this initial version. Multiple workers would each have independent
caches and could multiply provider requests. A shared persistent coordinator
would be required before enabling multi-worker deployment.

## Adding future consumers

A new consumer should call the API or the shared request manager. It must not
call `call_readonly_tool` or `market_snapshot` directly. GEX calculations
should consume the already cached futures snapshot where appropriate and use
their own non-TradingView Cboe delayed-options adapter; GEX must not create
additional TradingView requests.

## Verification

The request-manager tests cover cache hits, expiry, concurrent request
coalescing, cross-timeframe upstream serialization, stale fallback after failures,
and quota cooldown behavior.
Run the complete test suite before merging this branch.
