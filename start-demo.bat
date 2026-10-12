@echo off
rem Starts everything needed to use or demonstrate Vyterlix on this computer.
rem Double-click this file. Three black windows open (API, website, worker). Close them to stop.
cd /d "%~dp0"

if not exist backend\.venv\Scripts\python.exe (
  echo The backend has not been set up yet. See README.md, section 2.
  pause
  exit /b 1
)

start "Vyterlix API" cmd /k "cd /d backend && .venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
start "Vyterlix website" cmd /k "backend\.venv\Scripts\python.exe frontend\serve.py"
start "Vyterlix worker" cmd /k "cd /d backend && .venv\Scripts\python.exe -m app.cli.worker run"

echo Waiting a few seconds for everything to start...
timeout /t 8 /nobreak >nul
start http://localhost:5500
echo.
echo Vyterlix is running. The website is at http://localhost:5500
echo You can close this window. To stop Vyterlix, close the three other black windows.
pause
