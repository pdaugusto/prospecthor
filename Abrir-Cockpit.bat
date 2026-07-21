@echo off
cd /d "%~dp0"
title ProspecTHOR Cockpit
echo.
echo   ProspecTHOR Cockpit
echo   http://127.0.0.1:5055
echo   Nao feche esta janela.
echo.
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" "cockpit\start.py"
) else (
  python "cockpit\start.py"
)
if errorlevel 1 (
  echo.
  echo ERRO ao abrir o cockpit.
  pause
)