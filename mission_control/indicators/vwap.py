from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class SessionSpec:
    """A session open defined in its own market's local time."""

    name: str
    timezone: str
    open_local: time


NEW_YORK = SessionSpec("New York", "America/New_York", time(9, 30))
LONDON = SessionSpec("London", "Europe/London", time(8, 0))
TOKYO = SessionSpec("Tokyo", "Asia/Tokyo", time(9, 0))
GLOBEX = SessionSpec("Globex", "America/New_York", time(18, 0))


def _require_aware(moment: datetime, label: str) -> None:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def session_start_utc(spec: SessionSpec, on_date: date) -> datetime:
    """The session open on a local calendar date, as a UTC-aware datetime."""
    local = datetime.combine(on_date, spec.open_local, tzinfo=ZoneInfo(spec.timezone))
    return local.astimezone(UTC)


def latest_session_start(spec: SessionSpec, moment: datetime) -> datetime:
    """The most recent session open at or before `moment`."""
    _require_aware(moment, "moment")
    local_date = moment.astimezone(ZoneInfo(spec.timezone)).date()
    start = session_start_utc(spec, local_date)
    if start > moment:
        start = session_start_utc(spec, local_date - timedelta(days=1))
    return start


def _typical_price(bar: dict[str, Any]) -> float:
    return (float(bar["h"]) + float(bar["l"]) + float(bar["c"])) / 3.0


def _check_bars(bars: list[dict[str, Any]]) -> None:
    previous: datetime | None = None
    for bar in bars:
        opened = bar["t"]
        _require_aware(opened, "bar time")
        if previous is not None and opened <= previous:
            raise ValueError("bars must be strictly increasing by time")
        if float(bar["v"]) < 0:
            raise ValueError("volume cannot be negative")
        previous = opened


def session_vwap(bars: list[dict[str, Any]], spec: SessionSpec) -> list[float | None]:
    """Volume-weighted typical price that resets at each session open.

    Input: closed bars, oldest first, keys t (open time, aware), h, l, c, v.
    Output: one value per bar, None while cumulative volume is zero.
    """
    _check_bars(bars)
    output: list[float | None] = []
    current_start: datetime | None = None
    cumulative_pv = 0.0
    cumulative_volume = 0.0
    for bar in bars:
        start = latest_session_start(spec, bar["t"])
        if start != current_start:
            current_start = start
            cumulative_pv = 0.0
            cumulative_volume = 0.0
        volume = float(bar["v"])
        cumulative_pv += _typical_price(bar) * volume
        cumulative_volume += volume
        output.append(cumulative_pv / cumulative_volume if cumulative_volume > 0 else None)
    return output


def anchored_vwap(bars: list[dict[str, Any]], anchor: datetime) -> list[float | None]:
    """Volume-weighted typical price accumulated from `anchor` onward."""
    _require_aware(anchor, "anchor")
    _check_bars(bars)
    output: list[float | None] = []
    cumulative_pv = 0.0
    cumulative_volume = 0.0
    for bar in bars:
        if bar["t"] < anchor:
            output.append(None)
            continue
        volume = float(bar["v"])
        cumulative_pv += _typical_price(bar) * volume
        cumulative_volume += volume
        output.append(cumulative_pv / cumulative_volume if cumulative_volume > 0 else None)
    return output
