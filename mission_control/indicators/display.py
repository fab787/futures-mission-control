from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

PRIMARY_TIMEZONE = "America/New_York"
PRIMARY_LABEL = "ET/NY"


def _clock(moment: datetime) -> str:
    hour = moment.hour % 12 or 12
    suffix = "AM" if moment.hour < 12 else "PM"
    return f"{hour}:{moment.minute:02d} {suffix}"


def format_session_open(
    moment_utc: datetime,
    local_timezone: str,
    local_label: str,
    primary_timezone: str = PRIMARY_TIMEZONE,
    primary_label: str = PRIMARY_LABEL,
) -> tuple[str, str]:
    """Return (main, faded) text for a session open.

    main:  "3:00 AM ET/NY"  or  "7:00 PM ET/NY, previous day"
    faded: "(8:00 AM London)" or "(9:00 AM Tokyo)"
    Display only: never used by any calculation.
    """
    if moment_utc.tzinfo is None or moment_utc.utcoffset() is None:
        raise ValueError("moment must be timezone-aware")
    primary = moment_utc.astimezone(ZoneInfo(primary_timezone))
    local = moment_utc.astimezone(ZoneInfo(local_timezone))
    main = f"{_clock(primary)} {primary_label}"
    if primary.date() < local.date():
        main += ", previous day"
    elif primary.date() > local.date():
        main += ", next day"
    return main, f"({_clock(local)} {local_label})"
