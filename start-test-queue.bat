@echo off
cd /d "%~dp0"
rem Serves the fake queue at http://localhost:8000/client for testing the dashboard.
set PY=python
where py >nul 2>nul && set PY=py -3
%PY% --version >nul 2>nul || (
  echo Python 3 is not installed. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup, then run this again.
  pause
  exit /b 1
)
echo Test queue running at http://localhost:8000/client - close this window to stop it.
%PY% -m http.server 8000 --bind 127.0.0.1 -d fleet\testsite
pause
