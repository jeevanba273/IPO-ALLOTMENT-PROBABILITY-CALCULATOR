@echo off
rem Double-click to start the IPO Intelligence Dashboard: the local database, the app
rem with its automatic schedule, and the dashboard in your browser.
rem Keep this window open (minimise it); closing it stops the app and its schedule.
title IPO Intelligence Dashboard

curl -s -o nul http://127.0.0.1:8000/api/health && (
  echo The dashboard is already running. Opening it...
  start "" http://127.0.0.1:8000
  timeout /t 3 >nul
  exit /b 0
)

cd /d "%~dp0backend"
"%~dp0.venv\Scripts\ipo-intel.exe" serve --open
echo.
echo The dashboard has stopped.
pause
