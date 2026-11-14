"""Isolated TradingView webhook intake. This service never submits broker orders."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request

ROOT = Path(__file__).resolve().parent.parent
MAX_BODY_BYTES = 16_384
MAX_SIGNAL_AGE = timedelta(minutes=15)
FUTURE_CLOCK_TOLERANCE = timedelta(seconds=60)

SYMBOL_ALIASES = {
    "MNQ": "MNQ", "MNQ1!": "MNQ",
    "MES": "MES", "MES1!": "MES",
    "MGC": "MGC", "MGC1!": "MGC",
}
ALLOWED_ACTIONS = {"buy", "sell", "close", "flatten"}
ALLOWED_ORDER_TYPES = {"market", "limit", "stop", "stop_limit"}


class WebhookValidationError(ValueError):
    """A TradingView alert is malformed, stale, or outside the supported scope."""


@dataclass(frozen=True)
class TradingViewSignal:
    alert_id: str
    event_timestamp: str
    symbol: str
    action: str
    quantity: int | None
    order_type: str
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None
    strategy_id: str
    route_key: str | None
    raw_payload: str


def _aware_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise WebhookValidationError("timestamp must be a timezone-aware ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise WebhookValidationError("timestamp must be a valid ISO-8601 value") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WebhookValidationError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _positive_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise WebhookValidationError(f"{field} must be a positive number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise WebhookValidationError(f"{field} must be a positive number") from exc
    if not (number > 0 and number < float("inf")):
        raise WebhookValidationError(f"{field} must be a positive finite number")
    return number


def _symbol_root(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WebhookValidationError("symbol is required")
    raw = value.strip().upper().split(":")[-1]
    root = SYMBOL_ALIASES.get(raw)
    if root is None:
        raise WebhookValidationError("symbol is not enabled; allowed roots are MNQ, MES, and MGC")
    return root


def validate_signal(
    payload: dict[str, Any],
    expected_secret: str,
    *,
    now: datetime | None = None,
) -> TradingViewSignal:
    """Validate a webhook payload without accessing accounts, providers, or brokers."""
    supplied_secret = payload.get("secret")
    if not isinstance(supplied_secret, str) or not expected_secret:
        raise WebhookValidationError("webhook authentication failed")
    if not hmac.compare_digest(supplied_secret, expected_secret):
        raise WebhookValidationError("webhook authentication failed")

    alert_id = payload.get("alert_id")
    if not isinstance(alert_id, str) or not alert_id.strip() or len(alert_id) > 160:
        raise WebhookValidationError("alert_id must be a non-empty string of at most 160 characters")

    event_time = _aware_timestamp(payload.get("timestamp"))
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise WebhookValidationError("server clock must be timezone-aware")
    current_time = current_time.astimezone(timezone.utc)
    if event_time < current_time - MAX_SIGNAL_AGE:
        raise WebhookValidationError("signal is stale")
    if event_time > current_time + FUTURE_CLOCK_TOLERANCE:
        raise WebhookValidationError("signal timestamp is in the future")

    symbol = _symbol_root(payload.get("symbol"))
    action = payload.get("action")
    if not isinstance(action, str) or action.strip().lower() not in ALLOWED_ACTIONS:
        raise WebhookValidationError("action must be buy, sell, close, or flatten")
    action = action.strip().lower()

    quantity: int | None = None
    if action in {"buy", "sell"}:
        raw_quantity = payload.get("quantity")
        if isinstance(raw_quantity, bool):
            raise WebhookValidationError("quantity must be a positive whole number")
        try:
            numeric_quantity = float(raw_quantity)
        except (TypeError, ValueError) as exc:
            raise WebhookValidationError("quantity must be a positive whole number") from exc
        if not numeric_quantity.is_integer() or numeric_quantity < 1 or numeric_quantity > 100:
            raise WebhookValidationError("quantity must be a whole number between 1 and 100")
        quantity = int(numeric_quantity)

    order_type = str(payload.get("order_type", "market")).strip().lower()
    if order_type not in ALLOWED_ORDER_TYPES:
        raise WebhookValidationError("unsupported order_type")

    entry_price = stop_loss = take_profit = None
    if action in {"buy", "sell"}:
        entry_price = _positive_number(payload.get("entry_price"), "entry_price")
        stop_loss = _positive_number(payload.get("stop_loss"), "stop_loss")
        take_profit = _positive_number(payload.get("take_profit"), "take_profit")
        if action == "buy" and not stop_loss < entry_price < take_profit:
            raise WebhookValidationError("BUY requires stop_loss < entry_price < take_profit")
        if action == "sell" and not take_profit < entry_price < stop_loss:
            raise WebhookValidationError("SELL requires take_profit < entry_price < stop_loss")

    strategy_id = payload.get("strategy_id", "unspecified")
    if not isinstance(strategy_id, str) or not strategy_id.strip() or len(strategy_id) > 100:
        raise WebhookValidationError("strategy_id must be a non-empty string of at most 100 characters")

    # route_key is only an operator-configured alias. It is NOT an account ID
    # and is never trusted as authorization to route an order.
    route_key = payload.get("route_key")
    if route_key is not None:
        if not isinstance(route_key, str) or not route_key.strip() or len(route_key) > 100:
            raise WebhookValidationError("route_key must be a non-empty string of at most 100 characters")

    safe_payload = {key: value for key, value in payload.items() if key != "secret"}
    return TradingViewSignal(
        alert_id=alert_id.strip(),
        event_timestamp=event_time.isoformat(),
        symbol=symbol,
        action=action,
        quantity=quantity,
        order_type=order_type,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        strategy_id=strategy_id.strip(),
        route_key=route_key.strip() if isinstance(route_key, str) else None,
        raw_payload=json.dumps(safe_payload, sort_keys=True, separators=(",", ":")),
    )


def store_signal(signal: TradingViewSignal, db_path: str | Path | None = None) -> bool:
    """Persist one signal once. Returns False for a duplicate alert_id."""
    path = Path(db_path or os.getenv(
        "FAB787_WEBHOOK_DB", str(ROOT / "data" / "fab787_tradingview_webhooks.sqlite3")
    ))
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tradingview_webhook_inbox (
                alert_id TEXT PRIMARY KEY,
                received_at TEXT NOT NULL,
                event_timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                quantity INTEGER,
                order_type TEXT NOT NULL,
                entry_price REAL,
                stop_loss REAL,
                take_profit REAL,
                strategy_id TEXT NOT NULL,
                route_key TEXT,
                raw_payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'received'
            )
            """
        )
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO tradingview_webhook_inbox (
                alert_id, received_at, event_timestamp, symbol, action, quantity,
                order_type, entry_price, stop_loss, take_profit, strategy_id,
                route_key, raw_payload, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'received')
            """,
            (
                signal.alert_id,
                datetime.now(timezone.utc).isoformat(),
                signal.event_timestamp,
                signal.symbol,
                signal.action,
                signal.quantity,
                signal.order_type,
                signal.entry_price,
                signal.stop_loss,
                signal.take_profit,
                signal.strategy_id,
                signal.route_key,
                signal.raw_payload,
            ),
        )
        connection.commit()
        return cursor.rowcount == 1
    finally:
        connection.close()


app = FastAPI(
    title="FAB787 QUANTUM TradingView Webhook Receiver",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "isolated-tradingview-webhook-receiver",
        "execution_enabled": False,
    }


@app.post("/webhook/tradingview", status_code=202)
async def receive_tradingview_webhook(request: Request) -> dict[str, Any]:
    """Accept, validate, and durably record an alert; never place an order."""
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="webhook payload too large")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="webhook payload too large")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="webhook body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="webhook body must be a JSON object")

    expected_secret = os.getenv("FAB787_TRADINGVIEW_WEBHOOK_SECRET", "")
    if not expected_secret:
        raise HTTPException(status_code=503, detail="webhook receiver is not configured")
    try:
        signal = validate_signal(payload, expected_secret)
        inserted = await asyncio.to_thread(store_signal, signal)
    except WebhookValidationError as exc:
        status_code = 401 if "authentication failed" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail="webhook inbox storage unavailable") from exc

    return {
        "status": "accepted" if inserted else "duplicate",
        "alert_id": signal.alert_id,
        "execution_enabled": False,
        "human_approval_required": True,
        "message": "Signal recorded for review; no broker order was submitted.",
    }
