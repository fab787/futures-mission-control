from __future__ import annotations

from mission_control.account_registry import _connect as account_connect
from mission_control.broker_capabilities import (
    list_broker_capabilities,
    upsert_broker_capability,
)


def _seed_account(db_path, account_id: str = "account-1") -> None:
    with account_connect(db_path) as conn:
        conn.execute(
            """INSERT INTO trading_accounts
               (account_id,firm,nickname,account_mode,status,account_size,current_balance,
                risk_profile_id,created_at,updated_at,actor)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (account_id, "Phidias", "Phidias Eval", "evaluation", "active",
             50000, None, "profile-1", "2026-11-14T12:00:00+00:00",
             "2026-11-14T12:00:00+00:00", "test"),
        )


def test_capability_registry_defaults_to_unknown_and_never_enables_execution(tmp_path) -> None:
    db = tmp_path / "capabilities.sqlite3"
    _seed_account(db)
    result = list_broker_capabilities(db)
    assert result["count"] == 1
    capability = result["capabilities"][0]
    assert capability["account_id"] == "account-1"
    assert capability["connection_provider"] == "unknown"
    assert capability["platform"] == "unknown"
    assert capability["permission_status"] == "unverified"
    assert capability["execution_enabled"] is False
    assert result["execution_enabled"] is False
    assert result["orders_created"] == 0


def test_capability_update_requires_registered_account(tmp_path) -> None:
    try:
        upsert_broker_capability(
            "missing-account",
            {"connection_provider": "tradovate", "platform": "tradingview"},
            db_path=tmp_path / "capabilities.sqlite3",
        )
    except KeyError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("Unregistered account must be rejected")


def test_confirmed_permission_requires_evidence_reference(tmp_path) -> None:
    db = tmp_path / "capabilities.sqlite3"
    _seed_account(db)
    try:
        upsert_broker_capability(
            "account-1",
            {"connection_provider": "tradovate", "platform": "tradingview",
             "integration_method": "official_api", "permission_status": "confirmed"},
            db_path=db,
        )
    except ValueError as exc:
        assert "evidence reference" in str(exc)
    else:
        raise AssertionError("Confirmed permission without evidence must be rejected")


def test_capability_update_is_account_scoped_and_non_executing(tmp_path) -> None:
    db = tmp_path / "capabilities.sqlite3"
    _seed_account(db)
    result = upsert_broker_capability(
        "account-1",
        {
            "connection_provider": "cqg",
            "platform": "tradovate",
            "integration_method": "manual_only",
            "permission_status": "unverified",
            "notes": "Account provider confirmed; external API permission not checked.",
        },
        actor="operator",
        db_path=db,
    )
    assert result["account"]["firm"] == "Phidias"
    assert result["connection_provider"] == "cqg"
    assert result["permission_status"] == "unverified"
    assert result["execution_enabled"] is False
    assert result["orders_created"] == 0
    listed = list_broker_capabilities(db)["capabilities"][0]
    assert listed["account_id"] == "account-1"
    assert listed["notes"].startswith("Account provider confirmed")


def test_prohibited_automation_must_remain_manual_only(tmp_path) -> None:
    db = tmp_path / "capabilities.sqlite3"
    _seed_account(db)
    try:
        upsert_broker_capability(
            "account-1",
            {"connection_provider": "tradovate", "platform": "tradovate",
             "integration_method": "official_api", "permission_status": "prohibited",
             "evidence_reference": "https://example.invalid/official-policy"},
            db_path=db,
        )
    except ValueError as exc:
        assert "manual_only" in str(exc)
    else:
        raise AssertionError("Prohibited automation must not be configured as API")
