@echo off
cd /d "%~dp0"
title ProspecTHOR Cockpit LOG
echo.
echo   Cockpit com log
echo   http://127.0.0.1:5055
echo.
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" "cockpit\start.py"
) else (
  python "cockpit\start.py"
)
pause