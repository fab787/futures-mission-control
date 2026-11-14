"""Local versioned account-history snapshots; no broker or market-data access."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB = Path(os.environ.get(
    "FAB787_AUDIT_DB",
    Path(__file__).resolve().parent.parent / "data" / "fab787_quantum_audit.sqlite3",
))
MAX_RECORDS = 5000


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS account_history_snapshots (
        revision INTEGER PRIMARY KEY,
        risk_profile_id TEXT NOT NULL,
        snapshot_id TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        actor TEXT NOT NULL,
        source_label TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        content_sha256 TEXT NOT NULL
      );
      CREATE TRIGGER IF NOT EXISTS account_history_no_update
      BEFORE UPDATE ON account_history_snapshots
      BEGIN SELECT RAISE(ABORT, 'account history snapshots are immutable'); END;
      CREATE TRIGGER IF NOT EXISTS account_history_no_delete
      BEFORE DELETE ON account_history_snapshots
      BEGIN SELECT RAISE(ABORT, 'account history snapshots are immutable'); END;
    """)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(account_history_snapshots)")}
    if "risk_profile_id" not in columns:
        conn.execute("ALTER TABLE account_history_snapshots ADD COLUMN risk_profile_id TEXT NOT NULL DEFAULT 'legacy_unassigned'")
    conn.execute("CREATE INDEX IF NOT EXISTS account_history_profile_revision ON account_history_snapshots(risk_profile_id, revision DESC)")
    return conn


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def validate_history(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Account history must be a JSON object.")
    starting = payload.get("starting_balance")
    if not _finite(starting) or float(starting) <= 0:
        raise ValueError("starting_balance must be a positive finite number.")
    equity = payload.get("equity_observations")
    daily = payload.get("daily_results")
    sessions = payload.get("session_results")
    for name, rows in (("equity_observations", equity), ("daily_results", daily), ("session_results", sessions)):
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"{name} must contain at least one record.")
        if len(rows) > MAX_RECORDS:
            raise ValueError(f"{name} exceeds the {MAX_RECORDS}-record import limit.")
    seen_equity: set[str] = set()
    for index, row in enumerate(equity):
        if not isinstance(row, dict) or not _finite(row.get("equity")) or float(row["equity"]) <= 0:
            raise ValueError(f"Equity observation {index + 1} needs a positive finite equity value.")
        stamp = _timestamp(row.get("timestamp"))
        if stamp is None:
            raise ValueError(f"Equity observation {index + 1} needs a timezone-aware timestamp.")
        if stamp > datetime.now(timezone.utc) + timedelta(seconds=60):
            raise ValueError(f"Equity observation {index + 1} timestamp is too far in the future.")
        if row.get("observation_type") not in {"intraday", "end_of_day"}:
            raise ValueError(f"Equity observation {index + 1} observation_type must be intraday or end_of_day.")
        key = stamp.astimezone(timezone.utc).isoformat()
        if key in seen_equity:
            raise ValueError("Equity history contains duplicate timestamps.")
        seen_equity.add(key)
    seen_days: set[str] = set()
    for index, row in enumerate(daily):
        if not isinstance(row, dict) or not _date(row.get("date")) or not _finite(row.get("net_pnl")) or not isinstance(row.get("trading_day"), bool):
            raise ValueError(f"Daily result {index + 1} needs a real YYYY-MM-DD date, finite net_pnl and boolean trading_day.")
        if row["date"] in seen_days:
            raise ValueError("Daily history contains duplicate dates.")
        seen_days.add(row["date"])
    seen_sessions: set[tuple[str, str]] = set()
    for index, row in enumerate(sessions):
        if not isinstance(row, dict) or not _date(row.get("date")) or row.get("session") not in {"NY", "GLOBEX"} or not _finite(row.get("net_pnl")):
            raise ValueError(f"Session result {index + 1} needs a real date, NY/GLOBEX session and finite net_pnl.")
        key = (row["date"], row["session"])
        if key in seen_sessions:
            raise ValueError("Session history contains duplicate date/session records.")
        seen_sessions.add(key)
    return {
        "starting_balance": float(starting),
        "equity_observations": sorted(equity, key=lambda row: _timestamp(row["timestamp"])),
        "daily_results": sorted(daily, key=lambda row: row["date"]),
        "session_results": sorted(sessions, key=lambda row: (row["date"], row["session"])),
    }


def summarize_history(payload: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    equity_rows = payload["equity_observations"]
    latest = max(equity_rows, key=lambda row: _timestamp(row["timestamp"]))
    equity_values = [float(row["equity"]) for row in equity_rows]
    daily = payload["daily_results"]
    total = sum(float(row["net_pnl"]) for row in daily)
    best = max((float(row["net_pnl"]) for row in daily), default=0.0)
    latest_stamp = _timestamp(latest["timestamp"])
    age = max(0, int((datetime.now(timezone.utc) - latest_stamp.astimezone(timezone.utc)).total_seconds()))
    return {
        **metadata,
        "starting_balance": payload["starting_balance"],
        "latest_equity": float(latest["equity"]),
        "latest_equity_at": latest["timestamp"],
        "equity_observation_count": len(equity_rows),
        "peak_observed_equity": max([payload["starting_balance"], *equity_values]),
        "max_observed_peak_to_trough": round(max(
            (max([payload["starting_balance"], *equity_values[:index + 1]]) - value)
            for index, value in enumerate(equity_values)
        ), 2),
        "daily_record_count": len(daily),
        "trading_days": len({row["date"] for row in daily if row["trading_day"]}),
        "cumulative_net_profit": round(total, 2),
        "best_day_profit": round(best, 2),
        "best_day_profit_share_pct": round(best / total * 100, 2) if total > 0 and best > 0 else None,
        "session_record_count": len(payload["session_results"]),
        "history_age_seconds": age,
        "history_source": "operator_imported_unverified",
        "history_is_broker_authenticated": False,
    }


def save_history_snapshot(payload: Any, actor: Any, source_label: Any, risk_profile_id: Any, db_path: str | Path | None = None) -> dict[str, Any]:
    if not isinstance(actor, str) or not actor.strip() or len(actor.strip()) > 120:
        raise ValueError("A non-empty actor label of at most 120 characters is required.")
    if not isinstance(source_label, str) or not source_label.strip() or len(source_label.strip()) > 160:
        raise ValueError("A source label of 1–160 characters is required (for example, manual statement reconciliation).")
    if not isinstance(risk_profile_id, str) or not risk_profile_id.strip() or len(risk_profile_id.strip()) > 120:
        raise ValueError("A saved risk_profile_id is required to keep account histories separate.")
    clean = validate_history(payload)
    canonical = json.dumps(clean, sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute(
            "SELECT revision,snapshot_id,created_at,actor,source_label,content_sha256 FROM account_history_snapshots WHERE risk_profile_id=? ORDER BY revision DESC LIMIT 1",
            (risk_profile_id.strip(),)
        ).fetchone()
        if previous and previous["content_sha256"] == digest:
            metadata = dict(previous)
            metadata["duplicate_content"] = True
            return {"status": "unchanged", "revision": previous["revision"], "snapshot_id": previous["snapshot_id"],
                    "created_at": previous["created_at"], "duplicate_content": True,
                    "summary": summarize_history(clean, metadata)}
        revision = int(conn.execute("SELECT COALESCE(MAX(revision),0)+1 FROM account_history_snapshots").fetchone()[0])
        snapshot_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO account_history_snapshots(revision,risk_profile_id,snapshot_id,created_at,actor,source_label,payload_json,content_sha256) VALUES(?,?,?,?,?,?,?,?)",
            (revision, risk_profile_id.strip(), snapshot_id, created_at, actor.strip(), source_label.strip(), canonical, digest),
        )
    metadata = {"revision": revision, "risk_profile_id": risk_profile_id.strip(), "snapshot_id": snapshot_id, "created_at": created_at,
                "actor": actor.strip(), "source_label": source_label.strip(), "content_sha256": digest,
                "duplicate_content": False}
    return {"status": "saved", "revision": revision, "snapshot_id": snapshot_id, "created_at": created_at,
            "duplicate_content": False, "summary": summarize_history(clean, metadata),
            "provider_requests_triggered": 0, "broker_connections_triggered": 0, "orders_created": 0}


def get_current_history(risk_profile_id: str, db_path: str | Path | None = None, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
    if not isinstance(risk_profile_id, str) or not risk_profile_id.strip():
        return {"status": "not_configured", "revision": 0, "account_history": None, "summary": None, "provider_requests_triggered": 0, "orders_created": 0}
    if connection is None:
        with _connect(db_path) as conn:
            row = conn.execute("SELECT * FROM account_history_snapshots WHERE risk_profile_id=? ORDER BY revision DESC LIMIT 1", (risk_profile_id.strip(),)).fetchone()
    else:
        row = connection.execute("SELECT * FROM account_history_snapshots WHERE risk_profile_id=? ORDER BY revision DESC LIMIT 1", (risk_profile_id.strip(),)).fetchone()
    if row is None:
        return {"status": "not_configured", "revision": 0, "account_history": None,
                "summary": None, "provider_requests_triggered": 0, "orders_created": 0}
    payload = json.loads(row["payload_json"])
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    if digest != row["content_sha256"]:
        return {"status": "integrity_error", "revision": row["revision"], "account_history": None,
                "summary": None, "error": "Stored account-history snapshot failed its content-integrity check.",
                "provider_requests_triggered": 0, "orders_created": 0}
    metadata = {key: row[key] for key in ("revision", "risk_profile_id", "snapshot_id", "created_at", "actor", "source_label", "content_sha256")}
    return {"status": "available", "revision": row["revision"], "snapshot_id": row["snapshot_id"],
            "created_at": row["created_at"], "account_history": payload,
            "summary": summarize_history(payload, metadata), "provider_requests_triggered": 0, "orders_created": 0}


def list_history_versions(risk_profile_id: str, db_path: str | Path | None = None) -> dict[str, Any]:
    if not isinstance(risk_profile_id, str) or not risk_profile_id.strip():
        return {"versions": [], "storage": "local_sqlite_immutable_snapshots", "provider_requests_triggered": 0, "orders_created": 0}
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT revision,risk_profile_id,snapshot_id,created_at,actor,source_label,content_sha256 FROM account_history_snapshots WHERE risk_profile_id=? ORDER BY revision DESC LIMIT 50",
            (risk_profile_id.strip(),)
        ).fetchall()
    return {"versions": [dict(row) for row in rows], "storage": "local_sqlite_immutable_snapshots",
            "provider_requests_triggered": 0, "orders_created": 0}
