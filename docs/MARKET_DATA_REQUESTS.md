# Centralized Market Data Request Policy

Mission Control must route every TradingView market-data request through
`mission_control.market_data_manager.market_data_manager`. Dashboard panels,
instrument agents, GEX, and future analysis modules must not call the TradingView
adapter directly.

## Current safeguards

- One process-wide cache per requested timeframe.
- Successful snapshots are cached for 5 minutes.
- Empty/unavailable snapshots are cached for 30 seconds to avoid rapid retry loops.
- Concurrent requests for the same timeframe share one in-flight refresh.
- TradingView instrument requests remain sequential to protect OAuth refresh state.
- A detected TradingView quota/rate-limit response starts a 15-minute cooldown.
- During cooldown, a previous good snapshot is served as stale with its age and
  cooldown countdown; if no good snapshot exists, the API returns HTTP 429.
- A failed refresh does not replace a previous good snapshot with empty data.
- There is no force-refresh parameter that bypasses the cooldown.
- Cache state and counters are returned in the market-data response for visibility.

## Dashboard polling

The Overview's background market-data check is reduced to once every 5 minutes.
Opening the Charts workspace or changing timeframe can request another snapshot,
but the server-side cache and single-flight lock prevent redundant TradingView
calls while a timeframe is still fresh.

## Important deployment note

The cache and cooldown are in-process memory. Run a single Mission Control API
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
coalescing, stale fallback after failures, and quota cooldown behavior.
Run the complete test suite before merging this branch.
