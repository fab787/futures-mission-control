"""Pure calendar and earnings helpers for FAB787 QUANTUM (Stage 2a).

This module makes no network calls, imports no provider adapter, writes no
files, and has no trading authority. It only:

* loads the editable YAML config files (earnings watchlist, macro filters),
* normalizes TradingView ``get_economic_calendar`` and ``get_earnings_calendar``
  payloads into small, predictable records,
* filters and windows those records, and
* assembles a read-only calendar view.

Fetching, caching, cooldowns and disk persistence belong to a later stage.

All times are UTC. Converting to America/New_York is a display concern.
"""
from __future__ import annotations

import copy
import json
import math
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

SOURCE = "TradingView MCP"
PULL_WINDOW_DAYS = 14
MAX_VIEW_DAYS = 31
# Project-chosen guard against an accidentally huge request. It is not a
# documented TradingView limit.
MAX_WATCHLIST_SYMBOLS = 25
MAX_ALIASES_PER_FILTER = 20

_SYMBOL_RE = re.compile(r"^[A-Z0-9_]+:[A-Z0-9.\-]+$")
_WRAPPERS = ("data", "result", "payload", "response")
_ECONOMIC_ROW_KEYS = ("result", "events", "items", "data")


# --------------------------------------------------------------------------
# Small parsing helpers
# --------------------------------------------------------------------------

def _decode(value: Any) -> Any:
    """Decode a JSON string once; return everything else unchanged."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None
    return value


def _find_list(
    payload: Any,
    keys: tuple[str, ...],
    *,
    allow_bare_list: bool = True,
    _depth: int = 0,
) -> list[Any] | None:
    """Find a list of rows in the wrappers TradingView has been seen to use."""
    payload = _decode(payload)
    if isinstance(payload, list):
        return payload if allow_bare_list else None
    if not isinstance(payload, dict) or _depth > 4:
        return None
    for key in keys:
        value = _decode(payload.get(key))
        if isinstance(value, list):
            return value
    for key in _WRAPPERS:
        value = payload.get(key)
        if isinstance(value, (dict, str)):
            found = _find_list(
                value, keys, allow_bare_list=allow_bare_list, _depth=_depth + 1
            )
            if found is not None:
                return found
    return None


def _parse_utc(value: Any) -> datetime | None:
    """Parse an ISO timestamp that carries a timezone; otherwise return None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    return None


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:limit] if cleaned else None


def _require_aware(now: Any) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("'now' must be a timezone-aware datetime.")
    return now.astimezone(UTC)


def _check_days(days: Any) -> float:
    if isinstance(days, bool) or not isinstance(days, (int, float)):
        raise ValueError("'days' must be a number.")
    if not (0 < days <= MAX_VIEW_DAYS):
        raise ValueError(f"'days' must be greater than 0 and at most {MAX_VIEW_DAYS}.")
    return days


# --------------------------------------------------------------------------
# Config loaders
# --------------------------------------------------------------------------

def load_earnings_watchlist(path: str | Path) -> list[dict[str, str | None]]:
    """Load ``config/earnings_watchlist.yaml``.

    Every symbol must be exchange-qualified, for example ``NASDAQ:NVDA``.
    Raises ``FileNotFoundError`` if the file is missing and ``ValueError`` if
    its content is invalid, so a caller can treat either as "calendar
    unavailable" without affecting the price path.
    """
    with Path(path).open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    entries = raw.get("earnings_watchlist") if isinstance(raw, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError("Watchlist file must contain a non-empty 'earnings_watchlist' list.")
    if len(entries) > MAX_WATCHLIST_SYMBOLS:
        raise ValueError(f"Watchlist is limited to {MAX_WATCHLIST_SYMBOLS} symbols.")
    items: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for entry in entries:
        if isinstance(entry, str):
            symbol, name = entry, None
        elif isinstance(entry, dict):
            symbol, name = entry.get("symbol"), entry.get("name")
        else:
            raise ValueError("Each watchlist entry must be a symbol or a symbol/name pair.")
        symbol = symbol.strip().upper() if isinstance(symbol, str) else ""
        if not _SYMBOL_RE.match(symbol):
            raise ValueError(
                f"Invalid watchlist symbol {symbol!r}: use EXCHANGE:TICKER, for example NASDAQ:NVDA."
            )
        if symbol in seen:
            raise ValueError(f"Duplicate watchlist symbol: {symbol}")
        seen.add(symbol)
        items.append({"symbol": symbol, "name": _text(name, 120)})
    return items


def load_macro_event_filters(path: str | Path) -> dict[str, list[str]]:
    """Load ``config/macro_event_filters.yaml`` into ``{filter_name: [aliases]}``."""
    with Path(path).open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    section = raw.get("macro_event_filters") if isinstance(raw, dict) else None
    if not isinstance(section, dict) or not section:
        raise ValueError("Filter file must contain a non-empty 'macro_event_filters' mapping.")
    filters: dict[str, list[str]] = {}
    for name, aliases in section.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Each filter needs a non-empty name.")
        if not isinstance(aliases, list) or not aliases:
            raise ValueError(f"Filter {name!r} needs a non-empty list of aliases.")
        if len(aliases) > MAX_ALIASES_PER_FILTER:
            raise ValueError(f"Filter {name!r} has more than {MAX_ALIASES_PER_FILTER} aliases.")
        cleaned: list[str] = []
        for alias in aliases:
            if not isinstance(alias, str) or not alias.strip():
                raise ValueError(f"Filter {name!r} contains an empty alias.")
            cleaned.append(alias.strip())
        filters[name.strip()] = cleaned
    return filters


# --------------------------------------------------------------------------
# Normalizers
# --------------------------------------------------------------------------

def normalize_economic_events(payload: Any) -> list[dict[str, Any]]:
    """Normalize a TradingView economic-calendar payload.

    Keeps only the fields the dashboard needs and drops the long ``comment``
    text. Rows without a parseable timezone-aware ``date`` or without any
    title are skipped. The input is never modified.
    """
    rows = _find_list(payload, _ECONOMIC_ROW_KEYS) or []
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _text(row.get("title"), 200)
        indicator = _text(row.get("indicator"), 200)
        when = _parse_utc(row.get("date"))
        if when is None or (title is None and indicator is None):
            continue
        raw_id = row.get("id")
        event_id = _text(str(raw_id), 64) if raw_id is not None else None
        key = event_id or f"{(title or indicator or '').casefold()}|{when.isoformat()}"
        if key in seen:
            continue
        seen.add(key)
        importance = row.get("importance")
        events.append({
            "id": event_id,
            "title": title or indicator,
            "indicator": indicator,
            "country": _text(row.get("country"), 8),
            "importance": importance if isinstance(importance, int) and not isinstance(importance, bool) else None,
            "time_utc": when.isoformat(),
            "period": _text(row.get("period"), 40),
            "unit": _text(row.get("unit"), 16),
            "scale": _text(row.get("scale"), 16),
            "forecast": _number(row.get("forecast")),
            "previous": _number(row.get("previous")),
            "actual": _number(row.get("actual")),
            "source": SOURCE,
        })
    events.sort(key=lambda item: (item["time_utc"], item["title"] or ""))
    return events


def _earnings_item(
    row: dict[str, Any],
    *,
    in_window: bool,
) -> dict[str, Any] | None:
    symbol = _text(row.get("symbol"), 40)
    if symbol is None:
        return None
    symbol = symbol.upper()
    next_at = _parse_utc(row.get("release_next_at"))
    next_date = _parse_date(row.get("release_next_date")) or (next_at.date() if next_at else None)
    if next_date is None:
        return None

    # UNVERIFIED interpretation: release_next_time_flag 1 = a real time of day,
    # 0 = no real time. Inferred from one sample where MU (flag 0, date not
    # confirmed) carried a placeholder 12:00Z while NVDA and AVGO (flag 1) did
    # not. When the flag says 0 the time is dropped, so the dashboard shows a
    # date only and never invents precision.
    flag = row.get("release_next_time_flag")
    time_known: bool | None
    if isinstance(flag, int) and not isinstance(flag, bool) and flag in (0, 1):
        time_known = flag == 1
    else:
        time_known = None
    report_at = next_at if time_known is not False else None

    confirmed = row.get("next_date_confirmed")
    last_date = _parse_date(row.get("release_date"))
    return {
        "symbol": symbol,
        "ticker": _text(row.get("name"), 16) or symbol.split(":")[-1],
        "name": _text(row.get("description"), 120),
        "next_report_date": next_date.isoformat(),
        "next_report_at_utc": report_at.isoformat() if report_at else None,
        "date_confirmed": confirmed if isinstance(confirmed, bool) else None,
        "time_known": time_known,
        "last_report_date": last_date.isoformat() if last_date else None,
        "fiscal_period_end": _text(row.get("fiscal_period_end"), 10),
        "eps_forecast": _number(row.get("eps_forecast_next_fq")),
        "revenue_forecast": _number(row.get("revenue_forecast_next_fq")),
        "in_window": in_window,
        "source": SOURCE,
    }


def normalize_earnings(
    payload: Any,
    watchlist: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normalize a TradingView earnings-calendar payload.

    The tool returns symbols inside the requested window under ``earnings`` and
    the remaining symbols (date only) under ``out_of_window``. Both are kept,
    flagged with ``in_window``. When a watchlist is given, symbols outside it
    are dropped and watchlist symbols absent from the response are listed in
    ``missing_symbols``.
    """
    allowed = {entry["symbol"] for entry in watchlist} if watchlist is not None else None
    in_rows = _find_list(payload, ("earnings",)) or []
    out_rows = _find_list(payload, ("out_of_window",), allow_bare_list=False) or []

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rows, in_window in ((in_rows, True), (out_rows, False)):
        for row in rows:
            if not isinstance(row, dict):
                continue
            item = _earnings_item(row, in_window=in_window)
            if item is None or item["symbol"] in seen:
                continue
            if allowed is not None and item["symbol"] not in allowed:
                continue
            seen.add(item["symbol"])
            items.append(item)
    items.sort(key=lambda item: (item["next_report_date"], item["symbol"]))
    return {
        "source": SOURCE,
        "items": items,
        "missing_symbols": sorted(allowed - seen) if allowed is not None else [],
    }


# --------------------------------------------------------------------------
# Filtering and windows
# --------------------------------------------------------------------------

def _alias_pattern(alias: str) -> re.Pattern[str]:
    return re.compile(
        r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


def match_macro_filters(event: dict[str, Any], filters: dict[str, list[str]]) -> list[str]:
    """Return the filter names an event matches.

    Both ``title`` and ``indicator`` are searched. This matters: TradingView's
    FOMC decision has the title "Interest Rate" and only its indicator says
    "FOMC Decision Fed Funds Tgt Rate". Aliases match whole words, case
    insensitively, so "ISM" does not match "Prism".
    """
    haystack = " | ".join(
        part for part in (event.get("title"), event.get("indicator")) if isinstance(part, str)
    )
    return [
        name
        for name, aliases in filters.items()
        if any(_alias_pattern(alias).search(haystack) for alias in aliases)
    ]


def filter_macro_events(
    events: list[dict[str, Any]],
    filters: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """Keep only events matching a filter, tagged with ``matched_filters``."""
    kept: list[dict[str, Any]] = []
    for event in events:
        matched = match_macro_filters(event, filters)
        if matched:
            tagged = copy.deepcopy(event)
            tagged["matched_filters"] = matched
            kept.append(tagged)
    return kept


def events_in_window(
    events: list[dict[str, Any]],
    *,
    now: datetime,
    days: float,
    include_past_hours: float = 0.0,
) -> list[dict[str, Any]]:
    """Return events whose ``time_utc`` is in ``[now - past, now + days)``."""
    now_utc = _require_aware(now)
    _check_days(days)
    start = now_utc - timedelta(hours=max(0.0, include_past_hours))
    end = now_utc + timedelta(days=days)
    selected = []
    for event in events:
        when = _parse_utc(event.get("time_utc"))
        if when is not None and start <= when < end:
            selected.append(copy.deepcopy(event))
    selected.sort(key=lambda item: (item["time_utc"], item.get("title") or ""))
    return selected


def earnings_in_window(
    items: list[dict[str, Any]],
    *,
    now: datetime,
    days: float,
) -> list[dict[str, Any]]:
    """Return upcoming earnings inside the window.

    An item with a known time must fall in ``[now, now + days)``. An item with
    a date only is included when its date is from today through the window's
    last day, inclusive, because its time of day is unknown.
    """
    now_utc = _require_aware(now)
    _check_days(days)
    end = now_utc + timedelta(days=days)
    selected = []
    for item in items:
        at = _parse_utc(item.get("next_report_at_utc"))
        if at is not None:
            keep = now_utc <= at < end
        else:
            when = _parse_date(item.get("next_report_date"))
            keep = when is not None and now_utc.date() <= when <= end.date()
        if keep:
            selected.append(copy.deepcopy(item))
    selected.sort(key=lambda item: (item["next_report_date"], item["symbol"]))
    return selected


def weekly_pull_dates(now: datetime, days: int = PULL_WINDOW_DAYS) -> dict[str, str]:
    """Date range for the weekly provider pull: today through ``days`` ahead."""
    start = _require_aware(now).date()
    _check_days(days)
    return {
        "date_from": start.isoformat(),
        "date_to": (start + timedelta(days=int(days))).isoformat(),
    }


def build_calendar_view(
    *,
    economic_events: list[dict[str, Any]],
    earnings: list[dict[str, Any]],
    macro_filters: dict[str, list[str]],
    now: datetime,
) -> dict[str, Any]:
    """Assemble the read-only calendar view from already-normalized data.

    ``this_week`` is a rolling 7 days from ``now``, not a Monday-to-Friday
    calendar week. Per window, ``other_macro_event_count`` reports events the
    filter hid, so nothing is dropped silently.
    """
    now_utc = _require_aware(now)
    windows: dict[str, Any] = {}
    for name, days in (("next_3_days", 3), ("this_week", 7)):
        in_window = events_in_window(economic_events, now=now_utc, days=days)
        matched = filter_macro_events(in_window, macro_filters)
        windows[name] = {
            "days": days,
            "macro_events": matched,
            "other_macro_event_count": len(in_window) - len(matched),
            "earnings": earnings_in_window(earnings, now=now_utc, days=days),
        }
    return {
        "source": SOURCE,
        "timezone": "UTC",
        "generated_for": now_utc.isoformat(),
        "windows": windows,
        "filter_names": sorted(macro_filters),
        "trade_authority": "none",
        "order_execution": "disabled",
        "human_approval_required": True,
    }
