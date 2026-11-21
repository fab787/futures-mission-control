"""Market status and arm switch. The page's automatic timers read these through one gate."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from mission_control.market_session import (
    NY,
    auto_pull_allowed,
    globex_status,
    read_arm_state,
    write_arm_state,
)

router = APIRouter()


class ArmRequest(BaseModel):
    armed: bool


@router.get("/api/market/status")
def market_status() -> dict[str, Any]:
    now = datetime.now(NY)
    status = globex_status(now)
    allowed, reason = auto_pull_allowed(now)
    return {**status, "arm": read_arm_state(), "auto_pull_allowed": allowed, "auto_pull_reason": reason}


@router.post("/api/market/arm")
def set_arm(body: ArmRequest) -> dict[str, Any]:
    return write_arm_state(body.armed)