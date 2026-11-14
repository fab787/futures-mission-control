from datetime import datetime, timedelta, timezone

import pytest

from mission_control.account_history_csv import preview_csv, suggest_column_mapping


def sample_csv():
    now = datetime.now(timezone.utc)
    return (
        "timestamp,equity,date,daily_pnl,trading_day,session,session_pnl\n"
        f"{(now - timedelta(minutes=2)).isoformat()},50000,,,,,\n"
        f"{(now - timedelta(minutes=1)).isoformat()},50100,,,,,\n"
        f",,{now.date().isoformat()},100,true,NY,60\n"
    )


MAPPING = {
    "timestamp": "timestamp",
    "equity": "equity",
    "date": "date",
    "daily_pnl": "daily_pnl",
    "trading_day": "trading_day",
    "session": "session",
    "session_pnl": "session_pnl",
}


def test_csv_preview_builds_valid_history_without_saving():
    result = preview_csv(sample_csv(), 50000, MAPPING)
    assert result["status"] == "valid_preview"
    assert result["saved"] is False
    assert result["equity_observations"] == 2
    assert result["daily_results"] == 1
    assert result["session_results"] == 1
    assert result["account_history"]["daily_results"][0]["net_pnl"] == 100
    assert result["provider_requests_triggered"] == 0
    assert result["orders_created"] == 0


def test_csv_mapping_suggestions_are_conservative():
    headers = ["Timestamp", "Equity", "Date", "Daily PnL", "Trading Day", "Session", "Session PnL"]
    suggested = suggest_column_mapping(headers)
    assert suggested["timestamp"] == "Timestamp"
    assert suggested["equity"] == "Equity"
    assert suggested["date"] == "Date"
    assert suggested["session"] == "Session"
    assert suggested["daily_pnl"] == "Daily PnL"
    assert suggested["session_pnl"] == "Session PnL"


def test_csv_preview_rejects_naive_timestamps():
    content = sample_csv().replace("+00:00", "")
    with pytest.raises(ValueError, match="timezone-aware"):
        preview_csv(content, 50000, MAPPING)


def test_csv_preview_rejects_duplicate_daily_dates():
    content = sample_csv() + f",,{datetime.now(timezone.utc).date().isoformat()},20,true,NY,10\n"
    with pytest.raises(ValueError, match="duplicate dates"):
        preview_csv(content, 50000, MAPPING)


def test_csv_preview_rejects_incomplete_rows_and_bad_session():
    content = sample_csv().replace(",NY,60", ",GLOBEXX,60")
    with pytest.raises(ValueError, match="NY/GLOBEX"):
        preview_csv(content, 50000, MAPPING)


def test_csv_preview_rejects_missing_mapping_and_oversized_file():
    with pytest.raises(ValueError, match="Map a CSV column"):
        preview_csv(sample_csv(), 50000, {**MAPPING, "session": "missing"})
    with pytest.raises(ValueError, match="2 MB"):
        preview_csv("x" * (2 * 1024 * 1024 + 1), 50000, MAPPING)



def test_csv_preview_api_requires_profile_and_returns_unsaved_preview(monkeypatch):
    from fastapi.testclient import TestClient
    from mission_control import main

    monkeypatch.setattr(main, "get_profile", lambda profile_id: {"profile_id": profile_id} if profile_id == "profile-fixture" else None)
    client = TestClient(main.app)
    response = client.post("/api/account-history/csv-preview", json={
        "risk_profile_id": "profile-fixture",
        "csv_text": sample_csv(),
        "starting_balance": 50000,
        "column_mapping": MAPPING,
    })
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "valid_preview"
    assert result["saved"] is False
    assert result["orders_created"] == 0
    assert result["provider_requests_triggered"] == 0

    rejected = client.post("/api/account-history/csv-preview", json={
        "risk_profile_id": "unknown",
        "csv_text": sample_csv(),
        "starting_balance": 50000,
        "column_mapping": MAPPING,
    })
    assert rejected.status_code == 400
