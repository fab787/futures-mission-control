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
    version="0.2.0",
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


@app.get("/api/integrations")
def integrations() -> dict[str, Any]:
    """Report honest integration readiness; desktop MCPs are not local adapters."""
    return {
        "integrations": [
            {
                "id": "tradingview",
                "name": "TradingView",
                "role": "Interactive chart embed and future MCP market-data adapter",
                "status": "embed_available_adapter_not_connected",
                "connected": False,
                "capabilities": ["public_chart_embed"],
                "limitations": [
                    "The embedded chart is separate from the user's TradingView MCP.",
                    "Quote availability and delay depend on TradingView permissions.",
                    "No MCP data is consumed by this local application yet.",
                ],
            },
            {
                "id": "yahoo_finance",
                "name": "Yahoo Finance / News",
                "role": "Supplementary news and reference context",
                "status": "not_connected",
                "connected": False,
                "capabilities": [],
                "limitations": [
                    "No Yahoo Finance news adapter is configured.",
                    "Yahoo data must not be treated as a guaranteed real-time futures feed.",
                ],
            },
        ]
    }


@app.get("/api/news")
def news() -> dict[str, Any]:
    """Empty until a source-backed news adapter is connected."""
    return {
        "status": "not_connected",
        "source": None,
        "items": [],
        "message": "News adapter not connected. No headlines are being fabricated.",
    }


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return (ROOT / "mission_control" / "dashboard.html").read_text(encoding="utf-8")
