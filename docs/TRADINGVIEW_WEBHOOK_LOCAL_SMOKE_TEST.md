# Local TradingView Webhook Smoke Test (Windows)

This test verifies the local receiver only. It does not connect to TradingView, a prop firm, Tradovate, NinjaTrader, or any broker. The prices below are deliberately synthetic test values. Do not use them for a trade.

## 1. Start the isolated receiver

Open PowerShell in the repository root (the folder containing `mission_control`). Use a long random local test secret and keep it private:

```powershell
$env:FAB787_TRADINGVIEW_WEBHOOK_SECRET = "REPLACE_WITH_A_LONG_RANDOM_LOCAL_SECRET"
py -m uvicorn mission_control.tradingview_webhook:app --host 127.0.0.1 --port 8765 --workers 1
```

Leave this terminal running. Binding to `127.0.0.1` keeps the receiver local; do not change it to `0.0.0.0` for this test.

## 2. Send one synthetic test alert

Open a second PowerShell window in the same repository. Set the same local test secret in this second window, then run:

```powershell
$env:FAB787_TRADINGVIEW_WEBHOOK_SECRET = "REPLACE_WITH_THE_SAME_LONG_RANDOM_LOCAL_SECRET"
$now = (Get-Date).ToUniversalTime().ToString("o")
$payload = @{
  secret = $env:FAB787_TRADINGVIEW_WEBHOOK_SECRET
  alert_id = "local-smoke-$([guid]::NewGuid())"
  timestamp = $now
  symbol = "CME_MINI:MNQ1!"
  action = "buy"
  quantity = 1
  order_type = "limit"
  entry_price = 25000
  stop_loss = 24980
  take_profit = 25040
  strategy_id = "LOCAL-SMOKE-TEST-ONLY"
}
$body = $payload | ConvertTo-Json -Compress
Invoke-RestMethod -Uri "http://127.0.0.1:8765/webhook/tradingview" -Method Post -ContentType "application/json" -Body $body
```

Expected result: `status = accepted`, `execution_enabled = False`, and a message confirming that no broker order was submitted.

## 3. Verify the inbox and duplicate protection

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8765/inbox?limit=10" -Method Get
```

The synthetic event should appear once. To verify idempotency, resend the exact same `$body` without rebuilding `$payload`; the response should be `status = duplicate`.

## 4. Run automated tests

From the repository root:

```powershell
py -m pytest -q tests/test_tradingview_webhook.py
```

The test suite also exercises the HTTP endpoint, authentication failure, duplicate delivery, and safe inbox output.

## Important boundaries

- This local smoke test does not prove TradingView can reach the machine.
- Do not expose the Mission Control dashboard or its API to the public internet.
- A future public ingress must route only the webhook path to this isolated receiver and must be reviewed separately before use.
- Do not paste the webhook secret into chat, GitHub, screenshots, TradingView alert names, or source control.
- No Pine alert template is final until the exact selected strategy and its available alert conditions are verified.
