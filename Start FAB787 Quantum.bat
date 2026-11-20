@echo off
title FAB787 QUANTUM launcher
cd /d "%~dp0"
start "FAB787 QUANTUM server" python -m uvicorn mission_control.main:app --host 127.0.0.1 --port 8001
timeout /t 8 /nobreak >nul
start "" http://127.0.0.1:8001/
