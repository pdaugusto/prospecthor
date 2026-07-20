@echo off
chcp 65001 >nul
cd /d "%~dp0"
title ProspecTHOR Cockpit (com log)
echo.
echo  Cockpit COM janela de log (so se precisar debugar)
echo  http://127.0.0.1:5055
echo  Pode minimizar esta janela — nao feche enquanto usar o painel.
echo.

if exist "venv\Scripts\python.exe" (
  set PY=venv\Scripts\python.exe
) else (
  set PY=python
)

start "" "http://127.0.0.1:5055"
"%PY%" cockpit\app.py
if errorlevel 1 pause
