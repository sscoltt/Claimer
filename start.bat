@echo off
cd /d "%~dp0"
rem Prefer the "py" launcher; plain "python" on a fresh Windows 11 is only a Microsoft Store shortcut.
set PY=python
where py >nul 2>nul && set PY=py -3
%PY% --version >nul 2>nul || (
  echo Python 3 is not installed. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup, then run this again.
  pause
  exit /b 1
)
%PY% fleet\launcher.py
pause
