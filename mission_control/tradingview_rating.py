"""TradingView technical ratings for MNQ, MES, and MGC.

Score: the 5-minute technical rating. Label: the 1-minute rating shown beside it.
Both come from one batched read-only request for all three contracts, made at most
once per 5 minutes. Errors back off for 15 minutes. The dashboard reads this cache,
so page refreshes never send a TradingView request. No trade, risk, or order authority.
"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from mission_control.provider_errors import TradingViewQuotaExceeded
from mission_control.tradingview import SYMBOLS, AuthorizationRequired, call_readonly_tool

TTL_SECONDS = 300
BACKOFF_SECONDS = 900
COLUMNS = ["TechRating_5m", "TechRating_1m"]
VALID = {"StrongBuy", "Buy", "Neutral", "Sell", "StrongSell"}

_state: dict[str, Any] = {"at": 0.0, "data": None, "backoff_until": 0.0, "error": None, "fetches": 0}
_lock = asyncio.Lock()


def _norm(value: Any) -> str | None:
    text = str(value or "").replace(" ", "")
    return text if text in VALID else None


def _parse(payload: Any) -> dict[str, dict[str, str | None]]:
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, dict):
        rows = payload if isinstance(payload, dict) else {}
    result: dict[str, dict[str, str | None]] = {}
    for symbol, tv_symbol in SYMBOLS.items():
        row = rows.get(tv_symbol) if isinstance(rows.get(tv_symbol), dict) else {}
        result[symbol] = {
            "tf5": _norm(row.get("TechRating_5m")),
            "tf1": _norm(row.get("TechRating_1m")),
        }
    return result


def _response(status: str, message: str | None = None) -> dict[str, Any]:
    now = time.time()
    has_data = _state["data"] is not None
    return {
        "status": status,
        "message": message,
        "score_timeframe": "5m",
        "label_timeframe": "1m",
        "ratings": _state["data"] or {},
        "fetched_at": datetime.fromtimestamp(_state["at"], UTC).isoformat() if has_data else None,
        "age_seconds": round(now - _state["at"], 1) if has_data else None,
        "next_refresh_seconds": round(max(0.0, _state["at"] + TTL_SECONDS - now), 1) if has_data else None,
        "provider_requests_triggered": 0,
        "trade_authority": "none",
        "execution": "disabled",
    }


async def get_ratings() -> dict[str, Any]:
    now = time.time()
    if _state["data"] is not None and now - _state["at"] < TTL_SECONDS:
        return _response("cached")
    if now < _state["backoff_until"]:
        return _response("backoff", _state["error"])

    async with _lock:
        now = time.time()
        if _state["data"] is not None and now - _state["at"] < TTL_SECONDS:
            return _response("cached")
        if now < _state["backoff_until"]:
            return _response("backoff", _state["error"])

        _state["fetches"] += 1
        try:
            payload = await call_readonly_tool("get_symbol_data_batch", {
                "symbols": list(SYMBOLS.values()),
                "columns": COLUMNS,
            })
        except AuthorizationRequired:
            return _response("authorization_required", "Authorize TradingView: python -m mission_control.tradingview_auth")
        except TradingViewQuotaExceeded:
            _state["backoff_until"] = time.time() + BACKOFF_SECONDS
            _state["error"] = "TradingView quota or rate limit reached; retry in 15 minutes"
            return _response("quota_limited", _state["error"])
        except Exception as exc:
            _state["backoff_until"] = time.time() + BACKOFF_SECONDS
            _state["error"] = f"{type(exc).__name__}; retry in 15 minutes"
            if _state["data"] is not None:
                return _response("stale", _state["error"])
            return _response("unavailable", _state["error"])

        _state["data"] = _parse(payload)
        _state["at"] = time.time()
        _state["error"] = None
        _state["backoff_until"] = 0.0
        return _response("fresh")
