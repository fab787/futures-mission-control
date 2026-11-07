# Local Development (Windows)

Use Python 3.12 or newer. From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
uvicorn mission_control.main:app --reload
```

Open http://127.0.0.1:8000. API checks are available at `/api/health` and `/api/instruments`.

Run tests:

```powershell
pytest
```

This first slice is intentionally a static, honest-status dashboard and a small read-only API. It does not connect to TradingView, Yahoo Finance, Claude, a brokerage, or a paper account yet.
