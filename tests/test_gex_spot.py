import asyncio
import pytest

from mission_control import gex_spot
from mission_control.gex import GexUnavailable
from mission_control.gex_spot import WebullSpotService

REQUIRED = ("symbols", "category", "extend_hour_required", "overnight_required")


class FakeStock:
    def __init__(self, rows=None, fail=False):
        self.rows = rows
        self.fail = fail
        self.calls = []

    async def __call__(self, name, args):
        self.calls.append((name, dict(args)))
        assert name == "get_stock_snapshot", name
        missing = [k for k in REQUIRED if k not in args]
        assert not missing, f"missing required arguments: {missing}"
        assert args["category"] == "US_ETF"
        if self.fail:
            raise RuntimeError("boom")
        return self.rows


def run(coro):
    return asyncio.run(coro)


def row(symbol="QQQ", price="661.7500", **extra):
    return {"symbol": symbol, "price": price, "close": price, "trade_status": "OVN", "quote_time": 1, **extra}


def test_returns_price_and_sends_required_args():
    fake = FakeStock([row()])
    out = run(WebullSpotService(call_tool=fake, clock=lambda: 0.0).get_spot("QQQ"))
    assert out["spot"] == 661.75
    assert out["spot_field"] == "price"
    assert out["cache_status"] == "fresh"
    assert out["trade_status"] == "OVN"


def test_second_read_within_ttl_is_cached():
    fake = FakeStock([row()])
    svc = WebullSpotService(call_tool=fake, clock=lambda: 0.0)
    run(svc.get_spot("QQQ"))
    out = run(svc.get_spot("QQQ"))
    assert out["cache_status"] == "cached"
    assert len(fake.calls) == 1


def test_falls_back_to_close_when_price_missing():
    fake = FakeStock([{"symbol": "SPY", "price": "", "close": "690.10"}])
    out = run(WebullSpotService(call_tool=fake, clock=lambda: 0.0).get_spot("SPY"))
    assert out["spot"] == 690.10
    assert out["spot_field"] == "close"


def test_index_ticker_is_refused_without_a_call():
    fake = FakeStock([])
    with pytest.raises(GexUnavailable, match="pass spot explicitly"):
        run(WebullSpotService(call_tool=fake).get_spot("NDX"))
    assert fake.calls == []


def test_no_row_for_ticker_fails_closed():
    fake = FakeStock([row(symbol="SPY")])
    with pytest.raises(GexUnavailable, match="no quote for QQQ"):
        run(WebullSpotService(call_tool=fake, clock=lambda: 0.0).get_spot("QQQ"))


@pytest.mark.parametrize("bad", ["0", "-1", "nan", "abc", None])
def test_unusable_price_fails_closed(bad):
    fake = FakeStock([{"symbol": "GLD", "price": bad, "close": bad}])
    with pytest.raises(GexUnavailable, match="no usable price"):
        run(WebullSpotService(call_tool=fake, clock=lambda: 0.0).get_spot("GLD"))


def test_stale_value_served_only_after_failed_refresh():
    clock = {"t": 0.0}
    fake = FakeStock([row()])
    svc = WebullSpotService(call_tool=fake, clock=lambda: clock["t"], ttl_seconds=15)
    run(svc.get_spot("QQQ"))
    clock["t"] = 100.0
    fake.fail = True
    out = run(svc.get_spot("QQQ"))
    assert out["cache_status"] == "stale"
    assert out["spot"] == 661.75


def test_no_cache_and_failure_raises():
    fake = FakeStock(fail=True)
    with pytest.raises(GexUnavailable, match="spot request failed"):
        run(WebullSpotService(call_tool=fake, clock=lambda: 0.0).get_spot("QQQ"))
