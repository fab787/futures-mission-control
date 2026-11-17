"""Tests for the Webull chain layer. Mocked tool calls only; no network."""

import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from mission_control import gex_webull
from mission_control.gex import GexUnavailable
from mission_control.gex_webull import WebullGexService, fetch_chain, list_contracts, resolve_expiry

ET = ZoneInfo("America/New_York")


def occ(root, expiry, cp, strike):
    return f"{root}{expiry:%y%m%d}{cp}{int(round(strike * 1000)):08d}"


def contracts_for(root, expiry, strikes, start_id=0):
    rows, i = [], start_id
    for strike in strikes:
        for cp in ("C", "P"):
            rows.append({"symbol": occ(root, expiry, cp, strike), "instrument_id": str(i)})
            i += 1
    return rows


def snapshot_for(symbol, iv="28.76%", oi=1000):
    return {"symbol": symbol, "implied_volatility": iv, "open_interest": oi}


class FakeWebull:
    def __init__(self, contracts, snapshots):
        self.contracts = contracts
        self.snapshots = snapshots
        self.calls = []

    async def __call__(self, name, args):
        self.calls.append((name, dict(args)))
        if name == "get_option_contracts":
            rows = self.contracts.get(args["root_symbol"], [])
            start = 0
            if "last_instrument_id" in args:
                ids = [r["instrument_id"] for r in rows]
                start = ids.index(args["last_instrument_id"]) + 1
            return rows[start:start + args.get("page_size", 1000)]
        if name == "get_option_snapshot":
            return [self.snapshots[s] for s in args["symbols"].split(",") if s in self.snapshots]
        raise AssertionError(f"unexpected tool: {name}")


def run(coro):
    return asyncio.run(coro)


def future(days):
    return datetime.now(ET).date() + timedelta(days=days)


# --- paging and filtering -----------------------------------------------------

def test_pages_until_an_empty_page(monkeypatch):
    monkeypatch.setattr(gex_webull, "PAGE_SIZE", 3)
    rows = contracts_for("QQQ", future(5), [650, 655, 660, 665, 670, 675])  # 12 rows
    fake = FakeWebull({"QQQ": rows}, {})
    out = run(list_contracts("QQQ", fake))
    assert len(out) == 12
    listing_calls = [c for c in fake.calls if c[0] == "get_option_contracts"]
    assert len(listing_calls) == 5  # four full pages, then one empty page


def test_missing_instrument_id_fails_closed(monkeypatch):
    monkeypatch.setattr(gex_webull, "PAGE_SIZE", 2)
    rows = [{"symbol": occ("QQQ", future(5), "C", 660)}, {"symbol": occ("QQQ", future(5), "P", 660)}]
    fake = FakeWebull({"QQQ": rows}, {})
    with pytest.raises(GexUnavailable, match="instrument id"):
        run(list_contracts("QQQ", fake))


def test_keeps_only_the_requested_expiry():
    target, other = future(5), future(6)
    rows = contracts_for("QQQ", target, [660]) + contracts_for("QQQ", other, [660], start_id=10)
    snaps = {r["symbol"]: snapshot_for(r["symbol"]) for r in rows}
    chain = run(fetch_chain("QQQ", target, FakeWebull({"QQQ": rows}, snaps)))
    assert len(chain) == 2
    assert all(r["symbol"].startswith(f"QQQ{target:%y%m%d}") for r in chain)


def test_snapshots_are_batched_in_twenties():
    target = future(5)
    rows = contracts_for("QQQ", target, list(range(600, 645)))  # 90 contracts
    snaps = {r["symbol"]: snapshot_for(r["symbol"]) for r in rows}
    fake = FakeWebull({"QQQ": rows}, snaps)
    run(fetch_chain("QQQ", target, fake))
    sizes = [len(args["symbols"].split(",")) for name, args in fake.calls if name == "get_option_snapshot"]
    assert sizes == [20, 20, 20, 20, 10]


def test_empty_expiry_is_reported_clearly():
    rows = contracts_for("QQQ", future(5), [660])
    with pytest.raises(GexUnavailable, match="no QQQ contracts"):
        run(fetch_chain("QQQ", future(9), FakeWebull({"QQQ": rows}, {})))


# --- service: cache keying and calculation -----------------------------------

def test_cache_is_keyed_by_ticker_and_expiry():
    target, other = future(5), future(6)
    rows = contracts_for("QQQ", target, [655, 660, 665]) + contracts_for("QQQ", other, [655, 660, 665], start_id=20)
    snaps = {r["symbol"]: snapshot_for(r["symbol"]) for r in rows}
    fake = FakeWebull({"QQQ": rows}, snaps)
    svc = WebullGexService(call_tool=fake, clock=lambda: 0.0)

    def levels(expiry):
        return run(svc.get_levels(ticker="QQQ", expiry=expiry, spot=661.75,
                                  futures_price=27390.0, price_source="test"))

    levels(target)
    levels(target)
    assert svc.fetch_count == 1          # second call served from cache
    levels(other)
    assert svc.fetch_count == 2          # different expiry is a different key


def test_qqq_end_to_end_returns_dashboard_fields():
    target = future(5)
    rows = contracts_for("QQQ", target, [655, 660, 665, 670])
    snaps = {r["symbol"]: snapshot_for(r["symbol"]) for r in rows}
    svc = WebullGexService(call_tool=FakeWebull({"QQQ": rows}, snaps), clock=lambda: 0.0)
    result = run(svc.get_levels(ticker="QQQ", expiry=target, spot=661.75,
                                futures_price=27390.0, price_source="test"))
    assert result["futures_price"] == 27390.0
    assert "net_gex_per_1pct_move" in result
    assert "call_wall_futures" in result


def test_missing_snapshot_fails_closed_at_payload_stage():
    target = future(5)
    rows = contracts_for("QQQ", target, [660])
    svc = WebullGexService(call_tool=FakeWebull({"QQQ": rows}, {}), clock=lambda: 0.0)
    with pytest.raises(GexUnavailable, match="'(iv|open_interest)'"):
        run(svc.get_levels(ticker="QQQ", expiry=target, spot=661.75,
                           futures_price=27390.0, price_source="test"))


def test_unsupported_ticker_is_rejected():
    svc = WebullGexService(call_tool=FakeWebull({}, {}), clock=lambda: 0.0)
    with pytest.raises(GexUnavailable, match="Unsupported"):
        run(svc.get_levels(ticker="XYZ", expiry=future(5), spot=1.0,
                           futures_price=1.0, price_source="test"))


# --- expiry resolution --------------------------------------------------------

def test_empty_expiry_resolves_to_active_session():
    assert resolve_expiry(None, datetime(2026, 11, 16, 10, tzinfo=ET)) == date(2026, 11, 16)


def test_explicit_trading_day_is_accepted():
    assert resolve_expiry("2026-11-20") == date(2026, 11, 20)


def test_thanksgiving_expiry_is_rejected():
    with pytest.raises(GexUnavailable, match="not a trading day"):
        resolve_expiry("2026-11-26")


def test_bad_expiry_format_is_rejected():
    with pytest.raises(GexUnavailable, match="YYYY-MM-DD"):
        resolve_expiry("20-11-2026")
