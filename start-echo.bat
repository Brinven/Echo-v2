@echo off
title Echo Stage 0 - Latency Tester
cd /d "%~dp0echo_stage0"

REM Activate venv if present
if exist "..\venv\Scripts\activate.bat" (
    call "..\venv\Scripts\activate.bat"
)
if exist ".\venv\Scripts\activate.bat" (
    call ".\venv\Scripts\activate.bat"
)

python main.py
pause
