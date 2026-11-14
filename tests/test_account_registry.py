from mission_control.account_registry import (
    create_account, list_account_events, list_accounts, update_account,
)


def resolver(profile_id):
    return {
        "profile_id": profile_id,
        "profile": {"prop_firm": "Lucid", "account_mode": "evaluation"},
    }


def test_multiple_accounts_are_isolated_and_profile_link_is_unique(tmp_path):
    db = tmp_path / "registry.sqlite3"
    first = create_account({
        "firm": "Lucid", "nickname": "Lucid Eval A", "account_mode": "evaluation",
        "account_size": 50000, "risk_profile_id": "profile-a",
    }, "operator", resolver, db)
    second = create_account({
        "firm": "Lucid", "nickname": "Lucid Eval B", "account_mode": "evaluation",
        "account_size": 25000, "risk_profile_id": "profile-b",
    }, "operator", resolver, db)
    assert first["account"]["account_id"] != second["account"]["account_id"]
    assert list_accounts(db)["account_count"] == 2


def test_registry_rejects_reused_profile_and_mismatched_firm_mode(tmp_path):
    db = tmp_path / "registry.sqlite3"
    payload = {
        "firm": "Lucid", "nickname": "Account A", "account_mode": "evaluation",
        "account_size": 50000, "risk_profile_id": "same-profile",
    }
    create_account(payload, "operator", resolver, db)
    payload["nickname"] = "Account B"
    import pytest
    with pytest.raises(ValueError, match="UNIQUE"):
        create_account(payload, "operator", resolver, db)

    with pytest.raises(ValueError, match="must match"):
        create_account({**payload, "firm": "Phidias", "risk_profile_id": "another"},
                       "operator", resolver, db)


def test_account_updates_are_limited_and_audited(tmp_path):
    db = tmp_path / "registry.sqlite3"
    created = create_account({
        "firm": "Lucid", "nickname": "Account A", "account_mode": "evaluation",
        "account_size": 50000, "risk_profile_id": "profile-a",
    }, "operator", resolver, db)
    account_id = created["account"]["account_id"]
    updated = update_account(account_id, {"status": "completed", "current_balance": 51250},
                             "operator", db)
    assert updated["account"]["status"] == "completed"
    assert updated["account"]["current_balance"] == 51250
    assert len(list_account_events(account_id, db)["events"]) == 2
    import pytest
    with pytest.raises(ValueError, match="fixed"):
        update_account(account_id, {"risk_profile_id": "profile-b"}, "operator", db)


def test_account_duplicate_nickname_and_unknown_id_are_rejected(tmp_path):
    db = tmp_path / "registry.sqlite3"
    payload = {
        "firm": "Lucid", "nickname": "Same", "account_mode": "evaluation",
        "account_size": 50000, "risk_profile_id": "profile-a",
    }
    create_account(payload, "operator", resolver, db)
    import pytest
    with pytest.raises(ValueError, match="nickname already exists"):
        create_account({**payload, "risk_profile_id": "profile-b"}, "operator", resolver, db)
    with pytest.raises(KeyError):
        update_account("missing", {"status": "inactive"}, "operator", db)
