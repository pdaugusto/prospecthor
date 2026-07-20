@echo off
chcp 65001 >nul
cd /d "%~dp0"

title ProspecTHOR Cockpit
echo.
echo  ========================================
echo   ProspecTHOR — Cockpit local
echo   http://127.0.0.1:5055
echo  ========================================
echo.

if exist "venv\Scripts\python.exe" (
  set PY=venv\Scripts\python.exe
) else (
  set PY=python
)

start "" "http://127.0.0.1:5055"
"%PY%" cockpit\app.py
if errorlevel 1 pause
