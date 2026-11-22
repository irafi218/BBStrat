@echo off
title Confluence Trading Bot
cd /d %~dp0..
echo Starting Confluence Trading Bot...
echo Press Ctrl+C to stop.
echo.
call .venv\Scripts\activate.bat
python scripts\live_trader.py %*
pause
