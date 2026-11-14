"""Preview and validate operator-provided account statement CSV files.

This module is deliberately local-only: it has no broker, market-data, or provider
connections and never writes to the account-history ledger.
"""
from __future__ import annotations

import csv
import io
import math
import re
from typing import Any

from mission_control.account_history import MAX_RECORDS, validate_history

MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_CSV_ROWS = MAX_RECORDS
REQUIRED_MAPPINGS = {
    "timestamp": "equity timestamp (ISO 8601 with timezone)",
    "equity": "equity value",
    "date": "statement date (YYYY-MM-DD)",
    "daily_pnl": "daily net P&L",
    "trading_day": "trading-day flag",
    "session": "session (NY or GLOBEX)",
    "session_pnl": "session net P&L",
}


def _number(value: str, field: str, row_number: int) -> float:
    cleaned = value.strip().replace(",", "").replace("$", "")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    try:
        result = float(cleaned)
    except ValueError as exc:
        raise ValueError(f"CSV row {row_number}: {field} must be a number.") from exc
    if not math.isfinite(result):
        raise ValueError(f"CSV row {row_number}: {field} must be finite.")
    return result


def _boolean(value: str, row_number: int) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "yes", "y", "1"}:
        return True
    if normalized in {"false", "no", "n", "0"}:
        return False
    raise ValueError(f"CSV row {row_number}: trading_day must be true/false, yes/no, or 1/0.")


def preview_csv(
    csv_text: Any,
    starting_balance: Any,
    column_mapping: Any,
) -> dict[str, Any]:
    """Parse a mapped CSV and return a fully validated history preview, without saving."""
    if not isinstance(csv_text, str) or not csv_text.strip():
        raise ValueError("Choose a non-empty CSV statement file.")
    if len(csv_text.encode("utf-8")) > MAX_CSV_BYTES:
        raise ValueError("CSV file exceeds the 2 MB import limit.")
    if not isinstance(column_mapping, dict):
        raise ValueError("A CSV column mapping is required.")
    try:
        balance = float(starting_balance)
    except (TypeError, ValueError) as exc:
        raise ValueError("Enter a valid positive starting balance.") from exc
    if not math.isfinite(balance) or balance <= 0:
        raise ValueError("Starting balance must be a positive finite number.")

    reader = csv.DictReader(io.StringIO(csv_text))
    headers = reader.fieldnames or []
    if not headers:
        raise ValueError("CSV needs a header row.")
    if len(headers) != len(set(headers)):
        raise ValueError("CSV contains duplicate column names; rename them before importing.")
    if any(not isinstance(header, str) or not header.strip() for header in headers):
        raise ValueError("CSV contains an empty column name.")
    if len(headers) > 100:
        raise ValueError("CSV has too many columns (maximum 100).")

    mapping: dict[str, str] = {}
    for key, description in REQUIRED_MAPPINGS.items():
        column = column_mapping.get(key)
        if not isinstance(column, str) or column not in headers:
            raise ValueError(f"Map a CSV column for {description}.")
        mapping[key] = column

    history: dict[str, Any] = {
        "starting_balance": balance,
        "equity_observations": [],
        "daily_results": [],
        "session_results": [],
    }
    source_rows = 0
    for row_number, row in enumerate(reader, start=2):
        if row_number > MAX_CSV_ROWS + 1:
            raise ValueError(f"CSV exceeds the {MAX_CSV_ROWS}-row import limit.")
        if row is None or None in row:
            raise ValueError(f"CSV row {row_number}: row has more values than the header.")
        if not any((value or "").strip() for value in row.values()):
            continue
        source_rows += 1
        timestamp = (row.get(mapping["timestamp"]) or "").strip()
        equity = (row.get(mapping["equity"]) or "").strip()
        if timestamp or equity:
            if not timestamp or not equity:
                raise ValueError(f"CSV row {row_number}: equity timestamp and equity must both be present.")
            history["equity_observations"].append({
                "timestamp": timestamp,
                "equity": _number(equity, "equity", row_number),
                "observation_type": "intraday",
            })

        date_value = (row.get(mapping["date"]) or "").strip()
        daily_pnl = (row.get(mapping["daily_pnl"]) or "").strip()
        trading_day = (row.get(mapping["trading_day"]) or "").strip()
        if date_value or daily_pnl or trading_day:
            if not date_value or not daily_pnl or not trading_day:
                raise ValueError(f"CSV row {row_number}: daily date, net P&L and trading-day flag must all be present.")
            history["daily_results"].append({
                "date": date_value,
                "net_pnl": _number(daily_pnl, "daily net P&L", row_number),
                "trading_day": _boolean(trading_day, row_number),
            })

        session = (row.get(mapping["session"]) or "").strip().upper()
        session_pnl = (row.get(mapping["session_pnl"]) or "").strip()
        if session or session_pnl:
            if not date_value or not session or not session_pnl:
                raise ValueError(f"CSV row {row_number}: session date, NY/GLOBEX session and session net P&L must all be present.")
            history["session_results"].append({
                "date": date_value,
                "session": session,
                "net_pnl": _number(session_pnl, "session net P&L", row_number),
            })

    if source_rows == 0:
        raise ValueError("CSV contains no data rows.")
    validated = validate_history(history)
    return {
        "status": "valid_preview",
        "source_rows": source_rows,
        "equity_observations": len(validated["equity_observations"]),
        "daily_results": len(validated["daily_results"]),
        "session_results": len(validated["session_results"]),
        "account_history": validated,
        "saved": False,
        "provider_requests_triggered": 0,
        "broker_connections_triggered": 0,
        "orders_created": 0,
        "message": "Preview validated only. Nothing has been saved; confirm the preview before using the existing Save action.",
    }


def suggest_column_mapping(headers: list[str]) -> dict[str, str | None]:
    """Suggest exact, conservative header matches; the operator must review each mapping."""
    aliases = {
        "timestamp": {"timestamp", "equitytimestamp", "datetime", "datetimeutc"},
        "equity": {"equity", "accountequity", "balanceequity", "endingequity"},
        "date": {"date", "tradedate", "statementdate"},
        "daily_pnl": {"dailypnl", "daypnl", "netpnl", "dailynetpnl"},
        "trading_day": {"tradingday", "istradingday", "tradeday"},
        "session": {"session", "tradingsession"},
        "session_pnl": {"sessionpnl", "sessionnetpnl"},
    }
    normalized = {re.sub(r"[^a-z0-9]", "", header.strip().lower()): header for header in headers}
    return {
        key: next((normalized[name] for name in names if name in normalized), None)
        for key, names in aliases.items()
    }
