@echo off
title Trading Dashboard
cd /d %~dp0..
echo Starting Streamlit Dashboard...
echo Open http://localhost:8501 in your browser.
echo.
call .venv\Scripts\activate.bat
streamlit run dashboard\app.py
pause
