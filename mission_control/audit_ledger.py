"""Local append-only decision and audit ledger; no order execution capability."""
from __future__ import annotations
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mission_control.master_risk_engine import evaluate_master_risk
from mission_control.account_history import get_current_history

DEFAULT_DB = (Path(os.environ["FAB787_AUDIT_DB"]) if "FAB787_AUDIT_DB" in os.environ else Path(__file__).resolve().parent.parent / "data" / "fab787_quantum_audit.sqlite3")

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS proposals (
        proposal_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        risk_json TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('vetoed','pending','approved','rejected'))
      );
      CREATE TABLE IF NOT EXISTS audit_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        proposal_id TEXT,
        actor TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        details_json TEXT NOT NULL,
        FOREIGN KEY(proposal_id) REFERENCES proposals(proposal_id)
      );
      CREATE TRIGGER IF NOT EXISTS audit_events_no_update
      BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
      CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
      BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
    """)
    return conn

@contextmanager
def _session(db_path: str | Path | None = None):
    conn = _connect(db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()

def _event(conn: sqlite3.Connection, event_type: str, proposal_id: str | None,
           actor: str, details: dict[str, Any]) -> None:
    conn.execute(
      "INSERT INTO audit_events(event_type,proposal_id,actor,occurred_at,details_json) VALUES(?,?,?,?,?)",
      (event_type, proposal_id, actor, _now(), json.dumps(details, sort_keys=True, separators=(",", ":"))),
    )

def _clean_actor(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120:
        raise ValueError("A non-empty actor label of at most 120 characters is required.")
    return value.strip()

def create_proposal(payload: Any, actor: str, db_path: str | Path | None = None) -> dict[str, Any]:
    actor = _clean_actor(actor)
    payload = dict(payload)
    history_record = get_current_history(db_path)
    payload["account_history"] = history_record.get("account_history") if history_record.get("status") == "available" else None
    payload["account_history_revision"] = history_record.get("revision", 0)
    if history_record.get("status") == "available" and isinstance(payload.get("account_state"), dict):
        payload["account_state"] = dict(payload["account_state"])
        payload["account_state"]["cumulative_net_profit"] = history_record["summary"]["cumulative_net_profit"]
    risk = evaluate_master_risk(payload, require_saved_profile=True)
    stored_payload = dict(payload)
    stored_payload.pop("account_history", None)
    proposal_id = str(uuid.uuid4())
    status = "vetoed" if risk["veto"] else "pending"
    with _session(db_path) as conn:
        conn.execute(
          "INSERT INTO proposals(proposal_id,created_at,payload_json,risk_json,status) VALUES(?,?,?,?,?)",
          (proposal_id,_now(),json.dumps(stored_payload,sort_keys=True),json.dumps(risk,sort_keys=True),status),
        )
        _event(conn, "proposal_vetoed" if risk["veto"] else "proposal_submitted",
               proposal_id, actor, {"risk_status":risk["status"],"blockers":risk["blockers"],"account_history_revision":payload.get("account_history_revision",0)})
    return {"proposal_id":proposal_id,"status":status,"risk_review":risk,
            "execution":"disabled","orders_created":0}

def decide_proposal(proposal_id: str, decision: str, actor: str, reason: str,
                    db_path: str | Path | None = None) -> dict[str, Any]:
    actor = _clean_actor(actor)
    if not isinstance(decision, str) or decision not in {"approve","reject"}:
        raise ValueError("Decision must be approve or reject.")
    if not isinstance(reason,str) or not reason.strip() or len(reason.strip())>1000:
        raise ValueError("A decision reason of 1–1000 characters is required.")
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row=conn.execute("SELECT * FROM proposals WHERE proposal_id=?",(proposal_id,)).fetchone()
        if row is None:
            raise KeyError("Proposal not found.")
        if row["status"] != "pending":
            raise ValueError(f"Proposal is already finalized: {row['status']}.")
        payload=json.loads(row["payload_json"])
        history_record = get_current_history(db_path, conn)
        payload["account_history"] = history_record.get("account_history") if history_record.get("status") == "available" else None
        payload["account_history_revision"] = history_record.get("revision", 0)
        if history_record.get("status") == "available" and isinstance(payload.get("account_state"), dict):
            payload["account_state"] = dict(payload["account_state"])
            payload["account_state"]["cumulative_net_profit"] = history_record["summary"]["cumulative_net_profit"]
        fresh_risk=evaluate_master_risk(payload, require_saved_profile=True)
        if fresh_risk["veto"]:
            conn.execute("UPDATE proposals SET status='vetoed',risk_json=? WHERE proposal_id=?",
                         (json.dumps(fresh_risk,sort_keys=True),proposal_id))
            _event(conn,"approval_blocked_by_risk_engine",proposal_id,actor,
                   {"blockers":fresh_risk["blockers"],"attempted_decision":decision,"account_history_revision":payload.get("account_history_revision",0)})
            return {"proposal_id":proposal_id,"status":"vetoed","risk_review":fresh_risk,
                    "decision_recorded":False,"execution":"disabled","orders_created":0}
        status="approved" if decision=="approve" else "rejected"
        conn.execute("UPDATE proposals SET status=?,risk_json=? WHERE proposal_id=?",
                     (status,json.dumps(fresh_risk,sort_keys=True),proposal_id))
        _event(conn,"human_approval_recorded" if decision=="approve" else "human_rejection_recorded",
               proposal_id,actor,{"decision":decision,"reason":reason.strip(),
                                  "risk_status":fresh_risk["status"],"account_history_revision":payload.get("account_history_revision",0)})
        return {"proposal_id":proposal_id,"status":status,"decision_recorded":True,
                "risk_review":fresh_risk,"execution":"disabled","orders_created":0,
                "note":"This records a human decision only. No order is created or transmitted."}

def list_proposals(db_path: str | Path | None = None) -> dict[str, Any]:
    with _connect(db_path) as conn:
        rows=conn.execute("SELECT proposal_id,created_at,status,risk_json,payload_json FROM proposals ORDER BY created_at DESC").fetchall()
    proposals=[]
    for r in rows:
        payload=json.loads(r["payload_json"])
        proposal=payload.get("proposal",{})
        proposals.append({"proposal_id":r["proposal_id"],"created_at":r["created_at"],
            "status":r["status"],"risk_review":json.loads(r["risk_json"]),
            "proposal":{"symbol":proposal.get("symbol"),"side":proposal.get("side"),
                "quantity":proposal.get("quantity"),"entry_price":proposal.get("entry_price"),
                "stop_price":proposal.get("stop_price"),"target_price":proposal.get("target_price"),
                "session":proposal.get("session")},"risk_profile_id":payload.get("risk_profile_id"),
            "account_history_revision":payload.get("account_history_revision",0)})
    return {"proposals":proposals,"execution":"disabled"}

def list_audit_events(proposal_id: str | None = None, db_path: str | Path | None = None) -> dict[str, Any]:
    with _connect(db_path) as conn:
        if proposal_id:
            rows=conn.execute("SELECT * FROM audit_events WHERE proposal_id=? ORDER BY event_id",(proposal_id,)).fetchall()
        else:
            rows=conn.execute("SELECT * FROM audit_events ORDER BY event_id").fetchall()
    return {"events":[{"event_id":r["event_id"],"event_type":r["event_type"],
            "proposal_id":r["proposal_id"],"actor":r["actor"],"occurred_at":r["occurred_at"],
            "details":json.loads(r["details_json"])} for r in rows],
            "storage":"local_sqlite_append_only_events","execution":"disabled"}
