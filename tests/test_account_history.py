from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from mission_control.account_history import get_current_history, list_history_versions, save_history_snapshot


def sample_history():
    now = datetime.now(timezone.utc)
    return {
        "starting_balance": 50000,
        "equity_observations": [
            {"timestamp": (now - timedelta(minutes=2)).isoformat(), "equity": 50000, "observation_type": "intraday"},
            {"timestamp": (now - timedelta(minutes=1)).isoformat(), "equity": 50100, "observation_type": "intraday"},
        ],
        "daily_results": [{"date": now.date().isoformat(), "net_pnl": 100, "trading_day": True}],
        "session_results": [{"date": now.date().isoformat(), "session": "NY", "net_pnl": 100}],
    }


def test_history_is_scoped_to_profile_and_revisions_are_immutable(tmp_path):
    db = tmp_path / "history.sqlite3"
    first = save_history_snapshot(sample_history(), "operator", "Account A statement", "profile-a", db)
    other_history = sample_history()
    other_history["starting_balance"] = 25000
    other = save_history_snapshot(other_history, "operator", "Account B statement", "profile-b", db)
    assert first["revision"] == 1
    assert other["revision"] == 2
    assert get_current_history("profile-a", db)["summary"]["starting_balance"] == 50000
    assert get_current_history("profile-b", db)["summary"]["starting_balance"] == 25000
    assert list_history_versions("profile-a", db)["versions"][0]["risk_profile_id"] == "profile-a"
    with sqlite3.connect(db) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE account_history_snapshots SET actor='tampered'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM account_history_snapshots")


def test_identical_history_import_is_idempotent_for_same_profile(tmp_path):
    db = tmp_path / "history.sqlite3"
    payload = sample_history()
    first = save_history_snapshot(payload, "operator", "Statement import", "profile-a", db)
    again = save_history_snapshot(payload, "operator", "Repeat import", "profile-a", db)
    assert first["revision"] == again["revision"] == 1
    assert again["status"] == "unchanged"
    assert len(list_history_versions("profile-a", db)["versions"]) == 1


def test_same_history_can_be_saved_for_a_different_profile(tmp_path):
    db = tmp_path / "history.sqlite3"
    payload = sample_history()
    save_history_snapshot(payload, "operator", "Account A", "profile-a", db)
    second = save_history_snapshot(payload, "operator", "Account B", "profile-b", db)
    assert second["status"] == "saved"
    assert get_current_history("profile-b", db)["status"] == "available"


def test_history_rejects_missing_profile_id(tmp_path):
    with pytest.raises(ValueError, match="risk_profile_id"):
        save_history_snapshot(sample_history(), "operator", "Statement", "", tmp_path / "history.sqlite3")
