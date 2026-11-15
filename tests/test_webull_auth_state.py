from __future__ import annotations

import asyncio

from mcp.shared.auth import OAuthToken

from mission_control.webull import WebullTokenStorage, _extract_bar_groups, _normalize_bars


def test_reauthorization_marker_tracks_rejected_and_replaced_tokens(monkeypatch):
    values = {}

    def get_password(service, key):
        return values.get((service, key))

    def set_password(service, key, value):
        values[(service, key)] = value

    monkeypatch.setattr("mission_control.webull.keyring.get_password", get_password)
    monkeypatch.setattr("mission_control.webull.keyring.set_password", set_password)

    storage = WebullTokenStorage()
    assert storage.needs_reauthorization() is False

    storage.mark_reauthorization_required()
    assert storage.needs_reauthorization() is True

    asyncio.run(storage.set_tokens(
        OAuthToken(
            access_token="dummy-access-token",
            token_type="Bearer",
            expires_in=3600,
        )
    ))
    assert storage.needs_reauthorization() is False


def test_raw_top_level_futures_bar_arrays_are_grouped_by_contract_symbol():
    payload = [
        {"symbol": "MNQZ6", "time": "2026-11-13T20:00:00Z",
         "open": "25000", "high": "25002", "low": "24998", "close": "25001",
         "volume": "12"},
        {"symbol": "MESZ6", "time": "2026-11-13T20:00:00Z",
         "open": "7000", "high": "7002", "low": "6998", "close": "7001",
         "volume": "20"},
        {"symbol": "MNQZ6", "time": "2026-11-13T20:01:00Z",
         "open": "25001", "high": "25003", "low": "25000", "close": "25002",
         "volume": "9"},
    ]
    groups = _extract_bar_groups(payload)
    assert set(groups) == {"MNQZ6", "MESZ6"}
    assert len(_normalize_bars(groups["MNQZ6"]["result"])) == 2
    assert len(_normalize_bars(groups["MESZ6"]["result"])) == 1
