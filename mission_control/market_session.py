"""Market session status and the arm switch for automatic data pulls.

Pure functions, except reading and writing the arm-state file.

Hours follow CME Globex for MNQ, MES, and MGC: Sunday 6:00 PM ET through
Friday 5:00 PM ET, with a daily maintenance halt from 5:00 to 6:00 PM ET.
Full-day holidays go in HOLIDAYS. Verify both against the CME calendar.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SUNDAY_OPEN = time(18, 0)
FRIDAY_CLOSE = time(17, 0)
HALT_START = time(17, 0)
HALT_END = time(18, 0)
LONDON_START = time(3, 0)
US_CASH_START = time(9, 30)
US_CASH_END = time(16, 0)
HOLIDAYS: set[date] = set()
DEFAULT_ARM_FILE = Path(__file__).resolve().parents[1] / "data" / "arm_state.json"


def _session(t: time) -> str:
    if t >= SUNDAY_OPEN or t < LONDON_START:
        return "overnight"
    if t < US_CASH_START:
        return "london"
    if t < US_CASH_END:
        return "us_cash"
    return "post_cash"


def _weekend_next_open(local: datetime) -> datetime:
    days = (6 - local.weekday()) % 7
    target = datetime.combine(local.date() + timedelta(days=days), SUNDAY_OPEN, tzinfo=NY)
    if target <= local:
        target += timedelta(days=7)
    return target


def _result(status: str, local: datetime, session: str | None = None, label: str = "",
            next_open: datetime | None = None, reason: str = "") -> dict[str, Any]:
    return {
        "status": status,
        "session": session,
        "label": label,
        "reason": reason,
        "next_open": next_open.isoformat() if next_open else None,
        "as_of_ny": local.isoformat(),
    }


def globex_status(now: datetime) -> dict[str, Any]:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(NY)
    t, wd = local.time(), local.weekday()
    if local.date() in HOLIDAYS:
        return _result("closed", local, label="CLOSED · holiday", reason="Full-day holiday closure.")
    if wd == 5 or (wd == 4 and t >= FRIDAY_CLOSE) or (wd == 6 and t < SUNDAY_OPEN):
        return _result("closed", local, label="CLOSED · weekend", next_open=_weekend_next_open(local),
                       reason="Weekend close, Friday 5:00 PM to Sunday 6:00 PM ET.")
    if HALT_START <= t < HALT_END:
        return _result("halt", local, label="HALT · daily maintenance 5–6 PM ET",
                       next_open=datetime.combine(local.date(), HALT_END, tzinfo=NY),
                       reason="Daily maintenance halt.")
    session = _session(t)
    return _result("open", local, session=session, label=f"OPEN · {session.replace('_', ' ')}",
                   reason="Globex trading.")


def read_arm_state(path: Path = DEFAULT_ARM_FILE) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"armed": False, "changed_at": None, "reason": "No arm file. Default is disarmed."}
    armed = data.get("armed") is True
    return {"armed": armed, "changed_at": data.get("changed_at"),
            "reason": "armed" if armed else "disarmed"}


def write_arm_state(armed: bool, path: Path = DEFAULT_ARM_FILE,
                    now: datetime | None = None) -> dict[str, Any]:
    stamp = now or datetime.now(NY)
    if stamp.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"armed": bool(armed), "changed_at": stamp.isoformat()}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return payload


def auto_pull_allowed(now: datetime, path: Path = DEFAULT_ARM_FILE) -> tuple[bool, str]:
    if not read_arm_state(path)["armed"]:
        return False, "Disarmed: automatic pulls are off. Manual refresh still works."
    status = globex_status(now)
    if status["status"] != "open":
        return False, f"{status['label']}: automatic pulls paused until the market reopens."
    return True, "Armed and market open."