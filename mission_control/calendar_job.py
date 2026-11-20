"""Scheduled calendar pull: weekly (Friday, next Monday to Sunday) or daily (today plus six days).

Run from the project folder:
  python -m mission_control.calendar_job --mode weekly
  python -m mission_control.calendar_job --mode daily

Exit code 0 = snapshot written. Exit code 1 = every day failed; the last good snapshot is kept.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from mission_control.calendar_snapshot import build_snapshot, daily_window, weekly_window, write_snapshot
from mission_control.webull_news import _fetch_calendar_from_webull

BASE_DIR = Path(__file__).resolve().parent.parent / "data" / "calendar"


async def pull_window(start: date, end: date) -> tuple[dict | None, bool]:
    raw_events: list[dict] = []
    failures: list[str] = []
    days = (end - start).days + 1
    day = start
    while day <= end:
        try:
            result = await _fetch_calendar_from_webull(day.isoformat(), day.isoformat())
            raw_events.extend(result.get("events", []))
            if result.get("pagination_remaining"):
                failures.append(f"{day.isoformat()}: more events than one refresh returns")
        except Exception as exc:
            failures.append(f"{day.isoformat()}: {type(exc).__name__}")
        day += timedelta(days=1)
    if len(failures) == days:
        return None, False
    status = "fresh" if not failures else "partial"
    snapshot = build_snapshot(
        raw_events,
        window_start=start,
        window_end=end,
        pulled_at=datetime.now(UTC),
        status=status,
        failures=failures,
    )
    return snapshot, True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pull the Webull macro calendar snapshot.")
    parser.add_argument("--mode", choices=["weekly", "daily"], required=True)
    parser.add_argument("--today", default=None, help="yyyy-mm-dd, for testing only")
    args = parser.parse_args(argv)
    today = date.fromisoformat(args.today) if args.today else datetime.now(ZoneInfo("America/New_York")).date()
    start, end = weekly_window(today) if args.mode == "weekly" else daily_window(today)
    snapshot, ok = asyncio.run(pull_window(start, end))
    if not ok or snapshot is None:
        print("Calendar pull failed on every day. The last good snapshot is kept.")
        return 1
    path = write_snapshot(snapshot, BASE_DIR)
    print(f"Wrote {path}: {snapshot['event_count']} events, status {snapshot['status']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
