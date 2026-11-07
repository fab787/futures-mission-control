from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

ROOT = Path(__file__).resolve().parent.parent
INSTRUMENTS_FILE = ROOT / "config" / "instruments.yaml"

app = FastAPI(
    title="Futures Mission Control",
    version="0.1.0",
    description="Read-only futures intelligence dashboard foundation.",
)


def load_instruments() -> dict[str, Any]:
    """Load enabled instrument definitions from the project YAML config."""
    with INSTRUMENTS_FILE.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    instruments = raw.get("instruments", {})
    return {
        symbol: {"symbol": symbol, **settings}
        for symbol, settings in instruments.items()
        if settings.get("enabled", False)
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "read-only", "execution": "disabled"}


@app.get("/api/instruments")
def instruments() -> dict[str, list[dict[str, Any]]]:
    return {"instruments": list(load_instruments().values())}


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return (ROOT / "mission_control" / "dashboard.html").read_text(encoding="utf-8")
