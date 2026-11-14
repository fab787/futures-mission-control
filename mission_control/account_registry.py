"""Local multi-account registry; no broker credentials, provider calls, or execution."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB = Path(os.environ.get(
    "FAB787_AUDIT_DB",
    Path(__file__).resolve().parent.parent / "data" / "fab787_quantum_audit.sqlite3",
))
FIRMS = {"Phidias", "Lucid", "BluSky", "Legends"}
MODES = {"evaluation", "funded"}
STATUSES = {"active", "inactive", "completed"}


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS trading_accounts (
        account_id TEXT PRIMARY KEY,
        firm TEXT NOT NULL,
        nickname TEXT NOT NULL,
        account_mode TEXT NOT NULL CHECK(account_mode IN ('evaluation','funded')),
        status TEXT NOT NULL CHECK(status IN ('active','inactive','completed')),
        account_size REAL NOT NULL CHECK(account_size > 0),
        current_balance REAL,
        risk_profile_id TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        actor TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS trading_account_events (
        event_id INTEGER PRIMARY KEY,
        account_id TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        actor TEXT NOT NULL,
        event_type TEXT NOT NULL,
        details_json TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS trading_accounts_status_firm
        ON trading_accounts(status, firm);
      CREATE INDEX IF NOT EXISTS trading_account_events_account
        ON trading_account_events(account_id, event_id);
    """)
    return conn


def _actor(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120:
        raise ValueError("A non-empty actor label of at most 120 characters is required.")
    return value.strip()


def _positive(value: Any, field: str, optional: bool = False) -> float | None:
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value <= 0:
        raise ValueError(f"{field} must be a positive finite number.")
    return float(value)


def _record(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def create_account(payload: Any, actor: Any, profile_resolver, db_path=None) -> dict[str, Any]:
    actor = _actor(actor)
    if not isinstance(payload, dict):
        raise ValueError("Account details must be a JSON object.")
    firm = payload.get("firm")
    nickname = payload.get("nickname")
    mode = payload.get("account_mode")
    status = payload.get("status", "active")
    profile_id = payload.get("risk_profile_id")
    if firm not in FIRMS:
        raise ValueError(f"firm must be one of: {', '.join(sorted(FIRMS))}.")
    if not isinstance(nickname, str) or not nickname.strip() or len(nickname.strip()) > 100:
        raise ValueError("A unique account nickname is required (maximum 100 characters).")
    if mode not in MODES:
        raise ValueError("account_mode must be evaluation or funded.")
    if status not in STATUSES:
        raise ValueError("status must be active, inactive, or completed.")
    size = _positive(payload.get("account_size"), "account_size")
    balance = _positive(payload.get("current_balance"), "current_balance", optional=True)
    if not isinstance(profile_id, str) or not profile_id.strip() or len(profile_id) > 120:
        raise ValueError("A valid risk_profile_id is required; every account must have its own linked profile.")
    profile = profile_resolver(profile_id)
    if profile is None:
        raise ValueError("The linked risk profile does not exist.")
    details = profile.get("profile", {})
    if details.get("prop_firm") != firm or details.get("account_mode") != mode:
        raise ValueError("The linked risk profile firm and account mode must match this account.")
    now = datetime.now(timezone.utc).isoformat()
    account_id = str(uuid.uuid4())
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        linked = conn.execute("SELECT 1 FROM trading_accounts WHERE risk_profile_id=?", (profile_id.strip(),)).fetchone()
        if linked:
            raise ValueError("Each registered account must use a distinct risk profile; this profile is already linked.")
        duplicate = conn.execute(
            "SELECT 1 FROM trading_accounts WHERE lower(nickname)=lower(?) AND firm=?",
            (nickname.strip(), firm),
        ).fetchone()
        if duplicate:
            raise ValueError("An account with this nickname already exists for this firm.")
        conn.execute(
            """INSERT INTO trading_accounts
            (account_id,firm,nickname,account_mode,status,account_size,current_balance,
             risk_profile_id,created_at,updated_at,actor)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (account_id, firm, nickname.strip(), mode, status, size, balance,
             profile_id.strip(), now, now, actor),
        )
        conn.execute(
            "INSERT INTO trading_account_events(account_id,occurred_at,actor,event_type,details_json) VALUES(?,?,?,?,?)",
            (account_id, now, actor, "account_created",
             f'{{"firm":"{firm}","nickname":"{nickname.strip()}","risk_profile_id":"{profile_id.strip()}"}}'),
        )
        row = conn.execute("SELECT * FROM trading_accounts WHERE account_id=?", (account_id,)).fetchone()
    return {"status": "created", "account": _record(row), "provider_requests_triggered": 0,
            "broker_connections_triggered": 0, "orders_created": 0, "execution": "disabled"}


def list_accounts(db_path=None, include_inactive: bool = True) -> dict[str, Any]:
    with _connect(db_path) as conn:
        if include_inactive:
            rows = conn.execute("SELECT * FROM trading_accounts ORDER BY firm,nickname").fetchall()
        else:
            rows = conn.execute("SELECT * FROM trading_accounts WHERE status='active' ORDER BY firm,nickname").fetchall()
        events = conn.execute(
            "SELECT account_id,COUNT(*) AS event_count,MAX(occurred_at) AS last_event_at "
            "FROM trading_account_events GROUP BY account_id"
        ).fetchall()
    event_map = {row["account_id"]: row for row in events}
    accounts = []
    for row in rows:
        item = _record(row)
        event = event_map.get(row["account_id"])
        item["event_count"] = event["event_count"] if event else 0
        item["last_event_at"] = event["last_event_at"] if event else None
        accounts.append(item)
    return {"accounts": accounts, "account_count": len(accounts),
            "provider_requests_triggered": 0, "broker_connections_triggered": 0,
            "execution": "disabled"}


def update_account(account_id: str, payload: Any, actor: Any, db_path=None) -> dict[str, Any]:
    actor = _actor(actor)
    if not isinstance(payload, dict) or not payload:
        raise ValueError("Provide at least one supported account field to update.")
    allowed = {"nickname", "status", "current_balance"}
    if set(payload) - allowed:
        raise ValueError("Only nickname, status, and current_balance can be updated; firm, mode, size, and profile linkage are fixed.")
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM trading_accounts WHERE account_id=?", (account_id,)).fetchone()
        if row is None:
            raise KeyError("Account not found.")
        updates = dict(_record(row))
        if "nickname" in payload:
            value = payload["nickname"]
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > 100:
                raise ValueError("nickname must be a non-empty string of at most 100 characters.")
            duplicate = conn.execute(
                "SELECT 1 FROM trading_accounts WHERE firm=? AND lower(nickname)=lower(?) AND account_id<>?",
                (row["firm"], value.strip(), account_id),
            ).fetchone()
            if duplicate:
                raise ValueError("An account with this nickname already exists for this firm.")
            updates["nickname"] = value.strip()
        if "status" in payload:
            if payload["status"] not in STATUSES:
                raise ValueError("status must be active, inactive, or completed.")
            updates["status"] = payload["status"]
        if "current_balance" in payload:
            updates["current_balance"] = _positive(payload["current_balance"], "current_balance", optional=True)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE trading_accounts SET nickname=?,status=?,current_balance=?,updated_at=?,actor=? WHERE account_id=?",
            (updates["nickname"], updates["status"], updates["current_balance"], now, actor, account_id),
        )
        conn.execute(
            "INSERT INTO trading_account_events(account_id,occurred_at,actor,event_type,details_json) VALUES(?,?,?,?,?)",
            (account_id, now, actor, "account_updated",
             __import__("json").dumps({key: updates[key] for key in payload}, sort_keys=True)),
        )
        updated = conn.execute("SELECT * FROM trading_accounts WHERE account_id=?", (account_id,)).fetchone()
    return {"status": "updated", "account": _record(updated), "provider_requests_triggered": 0,
            "broker_connections_triggered": 0, "orders_created": 0, "execution": "disabled"}


def list_account_events(account_id: str, db_path=None) -> dict[str, Any]:
    with _connect(db_path) as conn:
        exists = conn.execute("SELECT 1 FROM trading_accounts WHERE account_id=?", (account_id,)).fetchone()
        if exists is None:
            raise KeyError("Account not found.")
        rows = conn.execute(
            "SELECT event_id,account_id,occurred_at,actor,event_type,details_json "
            "FROM trading_account_events WHERE account_id=? ORDER BY event_id",
            (account_id,),
        ).fetchall()
    return {"account_id": account_id, "events": [_record(row) for row in rows],
            "provider_requests_triggered": 0, "execution": "disabled"}
