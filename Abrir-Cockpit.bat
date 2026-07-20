@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM Atalho: sobe o cockpit SEM ficar com janela preta na sua cara.
REM (ainda precisa de um processo no fundo — usa pythonw)

if exist "venv\Scripts\pythonw.exe" (
  set PYW=venv\Scripts\pythonw.exe
  set PY=venv\Scripts\python.exe
) else (
  set PYW=pythonw
  set PY=python
)

REM Se a porta 5055 ja estiver em uso, so abre o browser
powershell -NoProfile -Command "try { $c = New-Object Net.Sockets.TcpClient('127.0.0.1', 5055); $c.Close(); exit 0 } catch { exit 1 }"
if %ERRORLEVEL%==0 (
  start "" "http://127.0.0.1:5055"
  exit /b 0
)

start "" "http://127.0.0.1:5055"
start "" "%PYW%" "%~dp0cockpit\app.py"
exit /b 0
