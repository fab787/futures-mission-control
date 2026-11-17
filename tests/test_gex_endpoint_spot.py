from datetime import date

import pytest

from mission_control import main
from mission_control.gex import GexUnavailable


def _client():
    import importlib
    try:
        mod = importlib.import_module("auth_client")
    except ImportError:
        mod = importlib.import_module("tests.auth_client")
    return mod.authenticated_client()


class FakeManager:
    def __init__(self):
        self._snap = {
            "cache": {"status": "hit", "age_seconds": 3},
            "instruments": [{"symbol": "MES", "latest": {"c": 6900.5}},
                            {"symbol": "MNQ", "latest": {"c": 27390.0}},
                            {"symbol": "MGC", "latest": {"c": 4100.0}}],
        }

    def peek_snapshot(self, interval):
        return self._snap if interval == "1m" else None


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(main, "_gex_provider_manager", lambda provider: FakeManager())
    monkeypatch.setattr(main, "resolve_expiry", lambda value: date(2026, 11, 18))
    captured = {}

    async def fake_levels(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(main.webull_gex_service, "get_levels", fake_levels)
    return captured


def test_etf_without_spot_reads_webull_quote(monkeypatch, wired):
    async def fake_spot(ticker, force_refresh=False):
        return {"spot": 661.75, "cache_status": "fresh", "trade_status": "OVN"}
    monkeypatch.setattr(main.webull_spot_service, "get_spot", fake_spot)

    r = _client().get("/api/gex", params={"ticker": "QQQ", "source": "webull"})
    assert r.status_code == 200
    assert wired["spot"] == 661.75
    body = r.json()
    assert body["spot_source"] == "webull_etf_quote"
    assert body["spot_status"] == "fresh"
    assert body["spot_trade_status"] == "OVN"


def test_index_without_spot_is_refused(wired):
    r = _client().get("/api/gex", params={"ticker": "NDX", "source": "webull"})
    assert r.status_code == 400
    assert "No Webull quote exists for indexes" in r.json()["detail"]


def test_explicit_spot_wins_and_is_reported_manual(monkeypatch, wired):
    async def should_not_run(ticker, force_refresh=False):
        raise AssertionError("spot service must not be called when spot= is given")
    monkeypatch.setattr(main.webull_spot_service, "get_spot", should_not_run)

    r = _client().get("/api/gex", params={"ticker": "SPY", "source": "webull", "spot": 690.0})
    assert r.status_code == 200
    assert wired["spot"] == 690.0
    assert r.json()["spot_source"] == "manual"


def test_quote_failure_returns_503(monkeypatch, wired):
    async def failing(ticker, force_refresh=False):
        raise GexUnavailable("Webull spot request failed: RuntimeError")
    monkeypatch.setattr(main.webull_spot_service, "get_spot", failing)

    r = _client().get("/api/gex", params={"ticker": "GLD", "source": "webull"})
    assert r.status_code == 503
    assert "spot request failed" in r.json()["detail"]
    assert wired == {}
