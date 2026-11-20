"""Agent desk: specialist reports and a conservative manager that prints manual signal cards.

Specialists (news, technical, catalyst, risk) read caches only. The manager prints a BUY
or SELL candidate only when the news and technical reports agree and no high-priority
catalyst is active. Otherwise it prints WAIT. Candidates are for manual review only.
Nothing is sent, routed, or approved here. The Master Risk Engine and Account Protection
are not applied because no account is connected.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

INSTRUMENTS = ("MNQ", "MES", "MGC")
RATING_SCORE = {"StrongBuy": 1.0, "Buy": 0.5, "Neutral": 0.0, "Sell": -0.5, "StrongSell": -1.0}
RATING_NAME = {"StrongBuy": "Strong Buy", "Buy": "Buy", "Neutral": "Neutral", "Sell": "Sell", "StrongSell": "Strong Sell"}
CATALYST_WINDOW_HOURS = 12
HIGH_PRIORITY_HOURS = 6
LABEL = {"BUY_CANDIDATE": "BUY CANDIDATE", "SELL_CANDIDATE": "SELL CANDIDATE", "WAIT": "WAIT"}


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _news(inst: dict[str, Any] | None) -> dict[str, Any]:
    combined = (inst or {}).get("combined_bias") or {}
    stance = combined.get("stance")
    if stance in {"bullish", "bearish", "neutral"}:
        state = stance
    elif stance == "mixed":
        state = "conflict"
    else:
        state = "no_data"
    sources = ", ".join(combined.get("sources") or []) or "none"
    return {
        "stance": state,
        "confidence": combined.get("confidence", "unavailable"),
        "evidence": f"Combined from {sources}; status {combined.get('status', 'unavailable')}.",
        "changes_mind_if": "Webull or Yahoo labels change, or new articles arrive within 24 hours.",
    }


def _technical(rating: dict[str, Any]) -> dict[str, Any]:
    tf5 = rating.get("tf5")
    tf1 = rating.get("tf1")
    if tf5 not in RATING_SCORE:
        return {
            "stance": "no_data",
            "confidence": "unavailable",
            "evidence": "TradingView 5-minute rating unavailable.",
            "changes_mind_if": "A fresh 5-minute TradingView rating arrives.",
        }
    score = RATING_SCORE[tf5]
    stance = "bullish" if score >= 0.5 else "bearish" if score <= -0.5 else "neutral"
    label = f"5m {RATING_NAME[tf5]}" + (f", 1m {RATING_NAME[tf1]}" if tf1 in RATING_SCORE else "")
    return {
        "stance": stance,
        "confidence": "medium" if abs(score) == 1.0 else "low",
        "evidence": f"TradingView {label}.",
        "changes_mind_if": "The 5-minute rating steps to a different level.",
    }


def _catalyst(symbol: str, headlines: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    count = 0
    high = False
    for item in headlines:
        if not isinstance(item, dict):
            continue
        published = _parse(item.get("published_at"))
        if published is None:
            continue
        age = (now - published).total_seconds() / 3600
        if age < 0 or age > CATALYST_WINDOW_HOURS:
            continue
        tagged = symbol in (item.get("instrument_hints") or [])
        if not tagged and item.get("affects") != "broad_macro":
            continue
        count += 1
        if item.get("priority") == "high" and age <= HIGH_PRIORITY_HOURS:
            high = True
    level = "active" if high else "watch" if count else "quiet"
    return {
        "level": level,
        "count": count,
        "evidence": f"{count} matching headline(s) in the last {CATALYST_WINDOW_HOURS}h; high-priority in the last {HIGH_PRIORITY_HOURS}h: {'yes' if high else 'no'}.",
        "changes_mind_if": "Matching headlines age out or a new high-priority item appears.",
    }


def _risk() -> dict[str, Any]:
    return {
        "status": "advisory_only",
        "evidence": "No account connected. Account Protection and Master Risk Engine not applied.",
        "changes_mind_if": "A verified account is connected and reviewed.",
    }


def _manager(news: dict[str, Any], tech: dict[str, Any], cat: dict[str, Any]) -> tuple[str, str]:
    if news["stance"] == "no_data":
        return "WAIT", "No usable news reading."
    if news["stance"] == "conflict":
        return "WAIT", "News sources disagree."
    if tech["stance"] == "no_data":
        return "WAIT", "No technical rating."
    if news["stance"] == "neutral" or tech["stance"] == "neutral":
        return "WAIT", "News or technicals are neutral."
    if news["stance"] != tech["stance"]:
        return "WAIT", "News and technicals point in opposite directions."
    if cat["level"] == "active":
        return "WAIT", "High-priority catalyst active; wait for a clearer read."
    signal = "BUY_CANDIDATE" if news["stance"] == "bullish" else "SELL_CANDIDATE"
    return signal, "News and technicals agree; no active high-priority catalyst."


def tradingview_cache() -> dict[str, Any]:
    """Read the TradingView rating cache only; never triggers a provider request."""
    from mission_control import tradingview_rating as tvr

    return {"ratings": tvr._state.get("data") or {}}


def build_agent_desk(intel: dict[str, Any], tv: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    by_symbol = {i.get("symbol"): i for i in (intel.get("instruments") or []) if isinstance(i, dict)}
    headlines = (intel.get("catalyst_watch") or {}).get("headlines") or []
    ratings = (tv or {}).get("ratings") or {}
    rows = []
    for symbol in INSTRUMENTS:
        news = _news(by_symbol.get(symbol))
        tech = _technical(ratings.get(symbol) or {})
        cat = _catalyst(symbol, headlines, now)
        risk = _risk()
        signal, reason = _manager(news, tech, cat)
        strength = None
        if signal != "WAIT":
            strength = "strong" if news["confidence"] == "medium" and tech["confidence"] == "medium" else "moderate"
        card = f"{symbol} · {LABEL[signal]}" + (f" · {strength}" if strength else "") + " · manual trade only · not sent"
        rows.append({
            "symbol": symbol,
            "specialists": {"news": news, "technical": tech, "catalyst": cat, "risk": risk},
            "manager": {"signal": signal, "strength": strength, "reason": reason},
            "signal_card": card,
        })
    ready = all(r["specialists"]["technical"]["stance"] != "no_data" for r in rows)
    return {
        "status": "ready" if ready else "partial",
        "decision_rule": "A BUY or SELL candidate prints only when the news and technical reports agree and no high-priority catalyst is active. Otherwise WAIT.",
        "generated_at": now.isoformat(),
        "summary": dict(Counter(r["manager"]["signal"] for r in rows)),
        "instruments": rows,
        "manual_trade_only": True,
        "orders_created": 0,
        "execution": "disabled",
        "provider_requests_triggered": 0,
        "risk_authority": "advisory_only",
    }
