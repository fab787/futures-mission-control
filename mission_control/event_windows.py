"""Event timing windows for scalping (1-minute and 15-minute charts).

minutes_to = event time minus now, in minutes: positive before the release, negative after.
Pure functions, no I/O.

Release times: CPI, NFP, PPI, retail, PCE, claims at 08:30 ET; industrial production 09:15 ET;
ISM and crude inventories 10:00 to 10:30 ET; FOMC statement 14:00 ET; press conference 14:30 ET.
"""
from __future__ import annotations

WINDOWS: dict[str, dict[str, float]] = {
    "fomc_statement": {"block_before": 10, "block_after": 3, "cool_after": 15, "warn_before": 60, "upcoming_before": 240},
    "fomc_presser": {"block_before": 10, "block_after": 3, "cool_after": 45, "warn_before": 60, "upcoming_before": 240},
    "high": {"block_before": 10, "block_after": 3, "cool_after": 15, "warn_before": 60, "upcoming_before": 240},
    "medium": {"block_before": 3, "block_after": 1, "cool_after": 5, "warn_before": 30, "upcoming_before": 240},
    "low": {"block_before": 0, "block_after": 0, "cool_after": 0, "warn_before": 0, "upcoming_before": 240},
    "unscheduled": {"block_before": 0, "block_after": 0, "cool_after": 15, "warn_before": 0, "upcoming_before": 0},
}
DEFAULT_CLASS = "medium"


def window_for(importance: str) -> dict[str, float]:
    return WINDOWS.get(importance, WINDOWS[DEFAULT_CLASS])


def catalyst_state(minutes_to: float, importance: str = DEFAULT_CLASS) -> str:
    w = window_for(importance)
    m = float(minutes_to)
    has_block = w["block_before"] > 0 or w["block_after"] > 0
    if has_block and -w["block_after"] <= m <= w["block_before"]:
        return "BLOCK"
    if -w["cool_after"] <= m < -w["block_after"]:
        return "COOL"
    if w["block_before"] < m <= w["warn_before"]:
        return "WARN"
    if w["warn_before"] < m <= w["upcoming_before"]:
        return "UPCOMING"
    if -w["upcoming_before"] <= m < -w["cool_after"]:
        return "RECENT"
    return "BACKGROUND"


def entry_gate(state: str) -> str:
    """block = no new entries; confirm = entry only after a closed-bar confirmation; warn = show warning; open = no gate."""
    if state == "BLOCK":
        return "block"
    if state == "COOL":
        return "confirm"
    if state == "WARN":
        return "warn"
    return "open"
