"""Webull option-chain adapter and expiry logic for GEX (PR A, data layer).

Converts Webull option rows into the payload shape that gex.calculate_gex
already reads. The gamma, dealer-sign, expected-move and wall math in gex.py
is unchanged. Fails closed: a missing or unrecognised field raises
GexUnavailable instead of being guessed.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from mission_control.gex import GexUnavailable

ET = ZoneInfo("America/New_York")
CLOSE = time(16, 0)

# Option roots per calculation ticker. SPX and NDX include the PM-settled
# weekly root so 0DTE contracts are not missed.
TICKER_ROOTS: dict[str, tuple[str, ...]] = {
    "NDX": ("NDX", "NDXP"),
    "SPX": ("SPX", "SPXW"),
    "QQQ": ("QQQ",),
    "SPY": ("SPY",),
    "GLD": ("GLD",),
}

# Futures-to-calculation-spot ratio bands. Index tickers map 1:1 to the cash
# index. ETFs map through a share multiplier (MNQ/QQQ about 41, MES/SPY about
# 10, MGC/GLD about 10 to 11), so the ETF band is wide but still rejects a
# wrong instrument.
RATIO_BANDS: dict[str, tuple[float, float]] = {
    "NDX": (0.50, 1.50),
    "SPX": (0.50, 1.50),
    "QQQ": (5.0, 100.0),
    "SPY": (5.0, 100.0),
    "GLD": (5.0, 100.0),
}

# Standard NDX and SPX roots settle in the morning on the expiry date.
# NDXP and SPXW settle at the 4:00 PM close.
AM_SETTLED_ROOTS = frozenset({"NDX", "SPX"})

OCC_RE = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")

# Accepted field names. The first live run must confirm which name Webull uses.
# Any name not on this list fails closed.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("symbol", "option_symbol", "contract_symbol", "occ_symbol"),
    "open_interest": ("open_interest", "openInterest", "oi"),
    "iv": ("imp_vol", "implied_volatility", "impliedVolatility", "iv", "implied_vol"),
}

# NYSE full-day holidays. Verify against the exchange calendar each year.
NYSE_HOLIDAYS_2026: frozenset[date] = frozenset({
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # Martin Luther King Jr. Day
    date(2026, 2, 16),   # Presidents' Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day (observed)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
})


def _pick(row: dict[str, Any], field: str) -> Any:
    for key in FIELD_ALIASES[field]:
        value = row.get(key)
        if value not in (None, ""):
            return value
    raise GexUnavailable(
        f"Webull option row has no '{field}' field "
        f"(looked for: {', '.join(FIELD_ALIASES[field])})"
    )


def _iv_decimal(raw: Any) -> float:
    """Return implied volatility as a decimal. '28.76%' and 28.76 both give 0.2876."""
    text = str(raw).strip()
    as_percent = text.endswith("%")
    try:
        value = float(text.rstrip("%").replace(",", ""))
    except ValueError as exc:
        raise GexUnavailable(f"Implied volatility is not numeric: {raw!r}") from exc
    if not math.isfinite(value) or value <= 0:
        raise GexUnavailable(f"Implied volatility must be positive: {raw!r}")
    if as_percent or value > 1.5:
        value /= 100.0
    if value > 5.0:
        raise GexUnavailable(f"Implied volatility out of range after conversion: {raw!r}")
    return value


def _open_interest(raw: Any) -> float:
    try:
        value = float(str(raw).replace(",", ""))
    except ValueError as exc:
        raise GexUnavailable(f"Open interest is not numeric: {raw!r}") from exc
    if not math.isfinite(value) or value < 0:
        raise GexUnavailable(f"Open interest must be zero or positive: {raw!r}")
    return value


def option_row_to_cboe(row: dict[str, Any]) -> dict[str, Any]:
    """Convert one Webull option row to the Cboe-shaped fields gex.py reads."""
    if not isinstance(row, dict):
        raise GexUnavailable("Webull option row must be an object")
    symbol = str(_pick(row, "symbol")).strip().upper()
    if not OCC_RE.match(symbol):
        raise GexUnavailable(f"Unrecognised option symbol format: {symbol}")
    return {
        "option": symbol,
        "open_interest": _open_interest(_pick(row, "open_interest")),
        "iv": _iv_decimal(_pick(row, "iv")),
    }


def build_payload(rows: Iterable[dict[str, Any]], spot: float, ticker: str) -> dict[str, Any]:
    """Build the payload calculate_gex expects. Rejects rows from the wrong root."""
    if ticker not in TICKER_ROOTS:
        raise GexUnavailable(f"Unsupported GEX ticker: {ticker}")
    if not math.isfinite(spot) or spot <= 0:
        raise GexUnavailable("A finite, positive underlying spot is required")
    allowed = TICKER_ROOTS[ticker]
    options: list[dict[str, Any]] = []
    for row in rows:
        converted = option_row_to_cboe(row)
        root = OCC_RE.match(converted["option"]).group(1)
        if root not in allowed:
            raise GexUnavailable(f"Option {converted['option']} does not belong to {ticker}")
        options.append(converted)
    if not options:
        raise GexUnavailable(f"No option rows returned for {ticker}")
    return {"data": {"current_price": float(spot), "options": options}}


def settlement_time(root: str, expiry: date) -> datetime:
    """Settlement moment for a root: AM roots at the open, PM roots at the close."""
    if root in AM_SETTLED_ROOTS:
        return datetime.combine(expiry, time(9, 30), tzinfo=ET)
    return datetime.combine(expiry, CLOSE, tzinfo=ET)


def is_trading_day(day: date, holidays: frozenset[date] = NYSE_HOLIDAYS_2026) -> bool:
    return day.weekday() < 5 and day not in holidays


def next_trading_day(day: date, holidays: frozenset[date] = NYSE_HOLIDAYS_2026) -> date:
    candidate = day + timedelta(days=1)
    while not is_trading_day(candidate, holidays):
        candidate += timedelta(days=1)
    return candidate


def last_trading_day_on_or_before(day: date, holidays: frozenset[date] = NYSE_HOLIDAYS_2026) -> date:
    candidate = day
    while not is_trading_day(candidate, holidays):
        candidate -= timedelta(days=1)
    return candidate


def active_session(now: datetime, holidays: frozenset[date] = NYSE_HOLIDAYS_2026) -> date:
    """0DTE session: today before the 4:00 PM close on a trading day, otherwise the next session."""
    local = now.astimezone(ET)
    today = local.date()
    if is_trading_day(today, holidays) and local.time() < CLOSE:
        return today
    return next_trading_day(today, holidays)


def expiry_choices(now: datetime, holidays: frozenset[date] = NYSE_HOLIDAYS_2026) -> dict[str, date]:
    """The four dropdown choices. Weekly choices use the last trading day on or before Friday."""
    session = active_session(now, holidays)
    friday = session + timedelta(days=(4 - session.weekday()) % 7)
    friday_next = friday + timedelta(days=7)
    return {
        "0DTE": session,
        "next_trading_day": next_trading_day(session, holidays),
        "this_week": last_trading_day_on_or_before(friday, holidays),
        "next_week": last_trading_day_on_or_before(friday_next, holidays),
    }
