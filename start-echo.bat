@echo off
REM ============================================================================
REM Echo launcher (Stage 5).
REM
REM Runtime memory is Ib-Lite -- a self-contained local SQLite store at
REM echo_stage0\echo.db. No external memory server, no API keys, no Hindsight.
REM (The CC hindsight plugin still uses bank "echo" for DEV notes, but Echo's
REM  runtime no longer talks to Hindsight at all.)
REM
REM Python: runs in echo_stage0\.venv (a DEDICATED venv, isolated from global
REM Python so another project's pip install can't clobber the voice-pipeline
REM deps). Create it once with:
REM     python -m venv echo_stage0\.venv
REM     echo_stage0\.venv\Scripts\python -m pip install -r echo_stage0\requirements.txt
REM
REM Requirements (both servers must already be running):
REM   - LM Studio at 127.0.0.1:1234 with a model loaded (Echo target:
REM     gemma-4-12b-it-qat). main.py exits with a clear message if not.
REM   - Kokoro-FastAPI at 127.0.0.1:8880 (H:\AxlyGitHub_H\Kokoro-FastAPI\start-kokoro.bat).
REM     tts.py exits with a clear message if not.
REM ============================================================================

setlocal enableextensions

REM Force UTF-8 so console output (em-dashes etc.) doesn't choke on cp1252.
set "PYTHONUTF8=1"

cd /d "%~dp0echo_stage0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  ERROR: Echo venv not found at echo_stage0\.venv
    echo  Create it once with:
    echo      python -m venv .venv
    echo      .venv\Scripts\python -m pip install -r requirements.txt
    echo.
    exit /b 1
)

".venv\Scripts\python.exe" main.py %*

endlocal
