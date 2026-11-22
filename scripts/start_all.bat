@echo off
title Confluence Bot Launcher
cd /d %~dp0..
echo ==========================================
echo   Confluence Trading Bot - Full Launch
echo ==========================================
echo.
echo Starting bot in background...
start "Trading Bot" cmd /k "call .venv\Scripts\activate.bat && python scripts\live_trader.py"
timeout /t 3 /nobreak > nul
echo Starting dashboard...
start "Dashboard" cmd /k "call .venv\Scripts\activate.bat && streamlit run dashboard\app.py"
echo.
echo Both windows launched.
echo - Bot logs: first terminal window
echo - Dashboard: http://localhost:8501
