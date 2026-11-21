from fastapi import FastAPI
from fastapi.testclient import TestClient

from mission_control.price_checks import block_closes, check_window, closes_from_bars, make_router


def ramp(start, step, n):
    return [start + step * i for i in range(n)]


def test_one_minute_up_on_steady_rise():
    assert check_window(ramp(100.0, 0.5, 21), 20, 0.02, "one-minute bars")["direction"] == "up"


def test_one_minute_flat_inside_noise_band():
    closes = [100.0 + (0.01 if i % 2 else -0.01) for i in range(21)]
    assert check_window(closes, 20, 0.02, "one-minute bars")["direction"] == "flat"


def test_short_history_reports_not_enough():
    r = check_window(ramp(100.0, 1.0, 10), 20, 0.02, "one-minute bars")
    assert r["direction"] is None
    assert "Not enough" in r["detail"]


def test_fifteen_minute_blocks_use_last_close_of_each_block():
    closes = ramp(100.0, 0.01, 330)
    blocks = block_closes(closes, 15)
    assert len(blocks) == 22
    assert blocks[-1] == closes[-1]


def test_fifteen_minute_up_with_enough_history():
    blocks = block_closes(ramp(100.0, 0.01, 330), 15)
    assert check_window(blocks, 20, 0.05, "fifteen-minute bars")["direction"] == "up"


def test_fifteen_minute_short_history_not_enough():
    blocks = block_closes(ramp(100.0, 0.01, 200), 15)
    assert check_window(blocks, 20, 0.05, "fifteen-minute bars")["direction"] is None


def test_closes_skip_bad_bars():
    bars = [{"c": 100}, {"c": "x"}, {"c": -1}, {}, {"c": 101}]
    assert closes_from_bars(bars) == [100.0, 101.0]


def test_endpoint_is_cache_only_and_lists_each_instrument():
    def provider():
        return {"instruments": [{"symbol": "MNQ", "bars": [{"c": c} for c in ramp(100.0, 0.5, 21)]}]}
    app = FastAPI()
    app.include_router(make_router(provider))
    body = TestClient(app).get("/api/agents/price-checks").json()
    assert body["provider_requests_triggered"] == 0
    by = {row["symbol"]: row for row in body["instruments"]}
    assert by["MNQ"]["one_minute"]["direction"] == "up"
    assert by["MES"]["one_minute"]["direction"] is None