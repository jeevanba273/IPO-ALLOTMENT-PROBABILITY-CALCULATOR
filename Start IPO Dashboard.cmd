@echo off
rem Double-click to open the IPO Intelligence Dashboard. It runs on Railway around the
rem clock, so nothing starts on this PC: a second copy here would keep its own budget
rem count and spend the API keys twice. To run it locally anyway: `ipo-intel serve --open`.
start "" https://ipo-dashboard-production.up.railway.app
