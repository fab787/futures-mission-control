from __future__ import annotations

import asyncio

from mission_control.tradingview import (
    _capture_response,
    _record_tool_error,
    _reset_diagnostics,
    diagnostics_snapshot,
)


def test_tradingview_response_hook_captures_rate_limit_headers_only():
    class Response:
        status_code = 429
        reason_phrase = "Too Many Requests"
        headers = {
            "retry-after": "60",
            "x-ratelimit-limit": "100",
            "x-ratelimit-remaining": "0",
            "authorization": "Bearer must-not-be-captured",
        }

    _reset_diagnostics()
    asyncio.run(_capture_response(Response()))
    result = diagnostics_snapshot()

    assert result["last_http_status"] == 429
    assert result["quota_detected"] is True
    assert result["rate_limit_headers"] == {
        "retry-after": "60",
        "x-ratelimit-limit": "100",
        "x-ratelimit-remaining": "0",
    }
    assert "authorization" not in result["rate_limit_headers"]


def test_tradingview_diagnostic_error_redacts_credentials():
    _reset_diagnostics()
    _record_tool_error("get_ohlcv", "HTTP 429 access_token=must-not-leak")
    result = diagnostics_snapshot()

    assert result["last_tool"] == "get_ohlcv"
    assert "429" in result["last_error"]
    assert "must-not-leak" not in result["last_error"]
