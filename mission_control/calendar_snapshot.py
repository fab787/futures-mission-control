"""Calendar snapshots for scalping windows: classify, normalize, store.

Pure functions plus a small writer. No provider calls here; calendar_job.py does the pulls.
Times are stored in UTC. New York time is for display only.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
STALE_AFTER = timedelta(hours=26)
WRITABLE_STATUSES = frozenset({"fresh", "partial"})
MAX_EVENTS = 300
SOURCE = "Webull US Macroeconomic Calendar"

_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bFOMC\b.*\bminutes\b", re.IGNORECASE), "low"),
    (re.compile(r"\bpress conference\b|\bFOMC\b.*\bpress\b", re.IGNORECASE), "fomc_presser"),
    (re.compile(r"\bFOMC\b.*\b(statement|rate decision|interest rate|fed funds)\b", re.IGNORECASE), "fomc_statement"),
    (re.compile(r"\bCPI\b|\bconsumer price\b", re.IGNORECASE), "high"),
    (re.compile(r"\bnon-?farm\b|\bpayrolls?\b|\bemployment situation\b|\bunemployment rate\b", re.IGNORECASE), "high"),
    (re.compile(r"\bPCE\b|\bpersonal (?:income|consumption)\b", re.IGNORECASE), "high"),
    (re.compile(
        r"\bPPI\b|\bproducer price\b|\bretail sales\b|\bjobless claims\b|\bISM\b|\bindustrial production\b|\bbeige book\b",
        re.IGNORECASE,
    ), "medium"),
)


def classify_event(name: str, is_fed_related: bool = False) -> str:
    """Map an event name to a scalping class. Unmatched Fed items are medium; everything else is low."""
    text = (name or "").strip()
    for pattern, importance in _RULES:
        if pattern.search(text):
            return importance
    return "medium" if is_fed_related else "low"


def weekly_window(today: date) -> tuple[date, date]:
    """Friday run: next Monday through the following Sunday."""
    days_ahead = (7 - today.weekday()) % 7 or 7
    start = today + timedelta(days=days_ahead)
    return start, start + timedelta(days=6)


def daily_window(today: date) -> tuple[date, date]:
    """Daily run: today through six days ahead."""
    return today, today + timedelta(days=6)


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def normalize_event(raw: dict[str, Any]) -> dict[str, Any] | None:
    name = str(raw.get("event_name") or "").strip()
    publish_date = str(raw.get("publish_date") or "").strip()
    if not name or not publish_date:
        return None
    when = _parse_utc(raw.get("publish_time"))
    fed = bool(raw.get("is_fed_related", False))
    return {
        "name": name[:300],
        "time_utc": when.isoformat() if when else None,
        "time_ny": when.astimezone(NY).strftime("%a %b %d, %I:%M %p ET") if when else None,
        "publish_date": publish_date[:10],
        "confirmed": when is not None,
        "importance": classify_event(name, fed),
        "is_fed_related": fed,
        "indicator_type": raw.get("indicator_type"),
        "period": raw.get("period"),
        "unit": raw.get("unit"),
        "prior_value": raw.get("prior_value"),
        "source": SOURCE,
    }


def build_snapshot(
    raw_events: list[dict[str, Any]],
    *,
    window_start: date,
    window_end: date,
    pulled_at: datetime,
    status: str,
    failures: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    if status not in {"fresh", "partial", "unavailable"}:
        raise ValueError(f"unknown status: {status}")
    if pulled_at.tzinfo is None:
        raise ValueError("pulled_at must be timezone-aware")
    seen: set[tuple[str, str]] = set()
    events: list[dict[str, Any]] = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        item = normalize_event(raw)
        if item is None:
            continue
        try:
            day = date.fromisoformat(item["publish_date"])
        except ValueError:
            continue
        if not (window_start <= day <= window_end):
            continue
        key = (item["name"].casefold(), item["time_utc"] or item["publish_date"])
        if key in seen:
            continue
        seen.add(key)
        events.append(item)
    events.sort(key=lambda item: (item["time_utc"] or item["publish_date"] + "T99", item["name"]))
    events = events[:MAX_EVENTS]
    return {
        "schema": 1,
        "source": SOURCE,
        "status": status,
        "pulled_at": pulled_at.astimezone(UTC).isoformat(),
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        "failures": list(failures)[:20],
        "event_count": len(events),
        "class_counts": dict(Counter(item["importance"] for item in events)),
        "events": events,
        "display_timezone": "America/New_York",
        "trade_authority": "none",
        "order_execution": "disabled",
    }


def write_snapshot(snapshot: dict[str, Any], base_dir: str | Path) -> Path:
    """Write a dated archive copy and replace latest.json. Unavailable snapshots are never stored."""
    if snapshot.get("status") not in WRITABLE_STATUSES:
        raise ValueError("Only fresh or partial snapshots are stored; the last good snapshot is kept.")
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    stamp = snapshot["pulled_at"][:16].replace(":", "").replace("-", "")
    (base / f"calendar_{stamp}.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    temporary = base / "latest.tmp"
    temporary.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    latest = base / "latest.json"
    os.replace(temporary, latest)
    return latest


def load_latest(base_dir: str | Path, now: datetime) -> dict[str, Any]:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    path = Path(base_dir) / "latest.json"
    if not path.exists():
        return {"status": "not_loaded", "events": [], "event_count": 0}
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    pulled = _parse_utc(snapshot.get("pulled_at"))
    if pulled is None:
        return {**snapshot, "status": "stale", "age_hours": None}
    age = now.astimezone(UTC) - pulled
    if age > STALE_AFTER:
        snapshot = {**snapshot, "status": "stale"}
    return {**snapshot, "age_hours": round(age.total_seconds() / 3600, 1)}
