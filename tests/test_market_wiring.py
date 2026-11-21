from pathlib import Path

from mission_control import market_routes

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "mission_control" / "main.py").read_text(encoding="utf-8")
HTML = (ROOT / "mission_control" / "dashboard.html").read_text(encoding="utf-8")


def test_router_has_status_and_arm_routes():
    paths = {route.path for route in market_routes.router.routes}
    assert "/api/market/status" in paths
    assert "/api/market/arm" in paths


def test_main_registers_market_router_once():
    assert MAIN.count("app.include_router(market_router)") == 1


def test_gate_fails_closed_and_timers_are_gated():
    assert "var state = { allowed: false" in HTML
    assert HTML.count("fmcMarketGate") >= 9
    assert "setInterval(loadNews,600000);" not in HTML
    assert "setInterval(runNews, 5000);" not in HTML
    assert "window.__fmcFast=setInterval(lvTick,ms);lvTick();" not in HTML


def test_page_calls_arm_endpoint():
    assert "/api/market/arm" in HTML