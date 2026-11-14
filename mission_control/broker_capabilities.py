"""Per-account broker/platform capability registry; records permissions, never executes."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB = Path(os.environ.get(
    "FAB787_AUDIT_DB",
    Path(__file__).resolve().parent.parent / "data" / "fab787_quantum_audit.sqlite3",
))
PROVIDERS = {"tradovate", "cqg", "rithmic", "other", "unknown"}
PLATFORMS = {"tradovate", "tradingview", "ninjatrader", "other", "unknown"}
METHODS = {"native_platform", "official_api", "local_desktop_adapter", "manual_only", "unknown"}
PERMISSIONS = {"unverified", "confirmed", "prohibited"}


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS account_broker_capabilities (
        account_id TEXT PRIMARY KEY,
        connection_provider TEXT NOT NULL,
        platform TEXT NOT NULL,
        integration_method TEXT NOT NULL,
        permission_status TEXT NOT NULL,
        evidence_reference TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL,
        FOREIGN KEY(account_id) REFERENCES trading_accounts(account_id)
      );
    """)
    return conn


def _text(value: Any, field: str, maximum: int, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text.")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{field} is required.")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters.")
    return value


def upsert_broker_capability(account_id: str, payload: Any, actor: str = "local_operator",
                             db_path: str | Path | None = None) -> dict[str, Any]:
    account_id = _text(account_id, "account_id", 120, True)
    if not isinstance(payload, dict):
        raise ValueError("Capability details must be a JSON object.")
    allowed = {"connection_provider", "platform", "integration_method",
               "permission_status", "evidence_reference", "notes"}
    if set(payload) - allowed:
        raise ValueError("Unsupported broker capability field.")
    provider = payload.get("connection_provider", "unknown")
    platform = payload.get("platform", "unknown")
    method = payload.get("integration_method", "unknown")
    permission = payload.get("permission_status", "unverified")
    if provider not in PROVIDERS or platform not in PLATFORMS or method not in METHODS or permission not in PERMISSIONS:
        raise ValueError("Unsupported provider, platform, integration method, or permission status.")
    evidence = _text(payload.get("evidence_reference", ""), "evidence_reference", 1000)
    notes = _text(payload.get("notes", ""), "notes", 2000)
    actor = _text(actor, "actor", 120, True)
    if permission == "confirmed" and not evidence:
        raise ValueError("Confirmed permission requires an evidence reference, such as an official policy URL or written support confirmation.")
    if permission == "prohibited" and method != "manual_only":
        raise ValueError("Prohibited automation must use manual_only; no automated integration is allowed.")
    now = datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        account = conn.execute(
            "SELECT account_id,firm,nickname,account_mode,status FROM trading_accounts WHERE account_id=?",
            (account_id,),
        ).fetchone()
        if account is None:
            raise KeyError("Registered account not found.")
        conn.execute(
            """INSERT INTO account_broker_capabilities
               (account_id,connection_provider,platform,integration_method,permission_status,
                evidence_reference,notes,updated_at,updated_by)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(account_id) DO UPDATE SET
                connection_provider=excluded.connection_provider, platform=excluded.platform,
                integration_method=excluded.integration_method, permission_status=excluded.permission_status,
                evidence_reference=excluded.evidence_reference, notes=excluded.notes,
                updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
            (account_id, provider, platform, method, permission, evidence, notes, now, actor),
        )
        row = conn.execute(
            "SELECT * FROM account_broker_capabilities WHERE account_id=?", (account_id,)
        ).fetchone()
    result = dict(row)
    result["account"] = dict(account)
    result["execution_enabled"] = False
    result["orders_created"] = 0
    return result


def list_broker_capabilities(db_path: str | Path | None = None) -> dict[str, Any]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT a.account_id,a.firm,a.nickname,a.account_mode,a.status AS account_status,
                      c.connection_provider,c.platform,c.integration_method,c.permission_status,
                      c.evidence_reference,c.notes,c.updated_at,c.updated_by
               FROM trading_accounts a
               LEFT JOIN account_broker_capabilities c ON c.account_id=a.account_id
               ORDER BY a.firm,a.nickname"""
        ).fetchall()
    records = []
    for row in rows:
        item = dict(row)
        if item["permission_status"] is None:
            item.update({
                "connection_provider": "unknown", "platform": "unknown",
                "integration_method": "unknown", "permission_status": "unverified",
                "evidence_reference": "", "notes": "",
                "updated_at": None, "updated_by": None,
            })
        item["execution_enabled"] = False
        records.append(item)
    return {"capabilities": records, "count": len(records),
            "execution_enabled": False, "orders_created": 0}
