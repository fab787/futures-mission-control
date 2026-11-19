from datetime import date

import pytest
import mission_control.main as main


def _client():
    import importlib
    try:
        mod = importlib.import_module("auth_client")
    except ImportError:
        mod = importlib.import_module("tests.auth_client")
    return mod.authenticated_client()


class FakeManager:
    def __init__(self, instruments, status="hit"):
        self._snap = {"cache": {"status": status, "age_seconds": 3}, "instruments": instruments}

    def peek_snapshot(self, interval):
        return self._snap if interval == "1m" else None


def inst(symbol, close):
    return {"symbol": symbol, "latest": {"c": close}}


ALL = [inst("MNQ", 27390.0), inst("MES", 6900.5), inst("MGC", 4100.0)]


@pytest.mark.parametrize(
    "ticker,symbol",
    [("NDX", "MNQ"), ("QQQ", "MNQ"), ("SPX", "MES"), ("SPY", "MES"), ("GLD", "MGC")],
)
def test_ticker_maps_to_futures_symbol(ticker, symbol):
    assert main.FUTURES_FOR_TICKER[ticker] == symbol


def test_cached_futures_price_reads_requested_symbol(monkeypatch):
    monkeypatch.setattr(main, "_gex_provider_manager", lambda provider: FakeManager(ALL))
    price, source = main._cached_gex_futures_price("webull", "MES")
    assert price == 6900.5
    assert "MES" in source


def test_cached_futures_price_missing_symbol_is_none(monkeypatch):
    monkeypatch.setattr(main, "_gex_provider_manager", lambda provider: FakeManager([inst("MNQ", 27390.0)]))
    assert main._cached_gex_futures_price("webull", "MGC") == (None, "")


def test_cached_futures_price_requires_fresh_cache(monkeypatch):
    monkeypatch.setattr(main, "_gex_provider_manager", lambda provider: FakeManager(ALL, status="miss"))
    assert main._cached_gex_futures_price("webull", "MES") == (None, "")


def test_mnq_wrapper_still_reads_mnq(monkeypatch):
    monkeypatch.setattr(main, "_gex_provider_manager", lambda provider: FakeManager(ALL))
    price, source = main._cached_gex_mnq_price("webull")
    assert price == 27390.0
    assert "MNQ" in source


@pytest.mark.parametrize("ticker,expected", [("SPY", 6900.5), ("SPX", 6900.5), ("GLD", 4100.0), ("QQQ", 27390.0)])
def test_endpoint_uses_matching_futures_price(monkeypatch, ticker, expected):
    monkeypatch.setattr(main, "_gex_provider_manager", lambda provider: FakeManager(ALL))
    monkeypatch.setattr(main, "resolve_expiry", lambda value: date(2026, 11, 18))
    captured = {}

    async def fake_get_levels(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(main.webull_gex_service, "get_levels", fake_get_levels)
    response = _client().get(
        "/api/gex", params={"ticker": ticker, "source": "webull", "spot": 100.0}
    )
    assert response.status_code == 200
    assert captured["futures_price"] == expected
    assert captured["ticker"] == ticker


def test_endpoint_names_missing_futures_symbol(monkeypatch):
    monkeypatch.setattr(main, "_gex_provider_manager", lambda provider: FakeManager([inst("MNQ", 27390.0)]))
    response = _client().get(
        "/api/gex", params={"ticker": "SPY", "source": "webull", "spot": 690.0}
    )
    assert response.status_code == 400
    assert "MES" in response.json()["detail"]
