from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from mission_control.tradingview_webhook import (
    TradingViewSignal,
    WebhookValidationError,
    list_signals,
    store_signal,
    validate_signal,
)


SECRET = "test-only-webhook-secret"


def _payload(**overrides):
    payload = {
        "secret": SECRET,
        "alert_id": "strategy-entry-2026-11-14T15:30:00Z",
        "timestamp": "2026-11-14T15:30:00Z",
        "symbol": "CME_MINI:MNQ1!",
        "action": "buy",
        "quantity": 1,
        "order_type": "market",
        "entry_price": 25000,
        "stop_loss": 24980,
        "take_profit": 25040,
        "strategy_id": "HVN-LVN-01",
        "route_key": "lucid-eval-a",
    }
    payload.update(overrides)
    return payload


def _now():
    return datetime(2026, 11, 14, 15, 30, 10, tzinfo=timezone.utc)


def test_valid_buy_normalizes_symbol_and_keeps_route_as_untrusted_alias():
    signal = validate_signal(_payload(), SECRET, now=_now())

    assert signal.symbol == "MNQ"
    assert signal.action == "buy"
    assert signal.quantity == 1
    assert signal.route_key == "lucid-eval-a"
    assert "secret" not in json.loads(signal.raw_payload)


def test_valid_sell_requires_correct_stop_and_target_orientation():
    signal = validate_signal(
        _payload(action="sell", entry_price=25000, stop_loss=25020, take_profit=24960),
        SECRET,
        now=_now(),
    )
    assert signal.action == "sell"


@pytest.mark.parametrize(
    "payload",
    [
        _payload(secret="wrong"),
        _payload(timestamp="2026-11-14T15:30:00"),
        _payload(timestamp="2026-11-14T15:00:00Z"),
        _payload(symbol="ES1!"),
        _payload(action="hold"),
        _payload(quantity=1.5),
        _payload(quantity=0),
        _payload(entry_price=25000, stop_loss=25010, take_profit=25040),
        _payload(alert_id=""),
    ],
)
def test_invalid_or_unsafe_alerts_are_rejected(payload):
    with pytest.raises(WebhookValidationError):
        validate_signal(payload, SECRET, now=_now())


def test_future_timestamp_beyond_tolerance_is_rejected():
    future = (_now() + timedelta(minutes=2)).isoformat()
    with pytest.raises(WebhookValidationError, match="future"):
        validate_signal(_payload(timestamp=future), SECRET, now=_now())


@pytest.mark.parametrize("action", ["approaching_buy", "approaching_sell", "invalidated"])
def test_early_warning_and_invalidation_events_need_no_order_bracket(action):
    signal = validate_signal(
        _payload(action=action, quantity=None, entry_price=None, stop_loss=None, take_profit=None),
        SECRET,
        now=_now(),
    )
    assert signal.action == action
    assert signal.quantity is None
    assert signal.entry_price is None
    assert signal.stop_loss is None
    assert signal.take_profit is None


def test_close_signal_does_not_require_entry_bracket_or_quantity():
    signal = validate_signal(
        _payload(action="close", quantity=None, entry_price=None, stop_loss=None, take_profit=None),
        SECRET,
        now=_now(),
    )
    assert signal.action == "close"
    assert signal.quantity is None
    assert signal.entry_price is None


def test_store_is_idempotent_and_never_persists_webhook_secret(tmp_path):
    signal = validate_signal(_payload(), SECRET, now=_now())
    db_path = tmp_path / "inbox.sqlite3"

    assert store_signal(signal, db_path) is True
    assert store_signal(signal, db_path) is False

    import sqlite3

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT alert_id, raw_payload, status FROM tradingview_webhook_inbox"
        ).fetchone()

    assert row is not None
    assert row[0] == signal.alert_id
    assert SECRET not in row[1]
    assert row[2] == "received"


def test_signal_is_an_immutable_data_record():
    signal = validate_signal(_payload(), SECRET, now=_now())
    assert isinstance(signal, TradingViewSignal)
    with pytest.raises((AttributeError, TypeError)):
        signal.action = "sell"  # type: ignore[misc]



def test_inbox_lists_safe_recent_records_without_raw_payload_or_secret(tmp_path):
    signal = validate_signal(_payload(), SECRET, now=_now())
    db_path = tmp_path / "inbox.sqlite3"
    assert store_signal(signal, db_path) is True

    result = list_signals(db_path=db_path)

    assert result["count"] == 1
    row = result["signals"][0]
    assert row["alert_id"] == signal.alert_id
    assert row["symbol"] == "MNQ"
    assert row["status"] == "received"
    assert "raw_payload" not in row
    assert "secret" not in row


def test_inbox_limit_is_bounded(tmp_path):
    with pytest.raises(ValueError, match="between 1 and 200"):
        list_signals(limit=201, db_path=tmp_path / "inbox.sqlite3")


def test_empty_inbox_is_safe_when_database_does_not_exist(tmp_path):
    result = list_signals(db_path=tmp_path / "missing.sqlite3")
    assert result["signals"] == []
    assert result["execution_enabled"] is False
