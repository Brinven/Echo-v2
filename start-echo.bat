@echo off
REM ============================================================================
REM Echo launcher (Stage 5).
REM
REM Runtime memory is Ib-Lite -- a self-contained local SQLite store at
REM echo_stage0\echo.db. No external memory server, no API keys, no Hindsight.
REM (The CC hindsight plugin still uses bank "echo" for DEV notes, but Echo's
REM  runtime no longer talks to Hindsight at all.)
REM
REM Requirements:
REM   - LM Studio running at 127.0.0.1:1234 with a model loaded
REM     (Echo target: gemma-4-12b-it-qat). main.py exits with a clear message if not.
REM ============================================================================

setlocal enableextensions

REM Force UTF-8 so console output (em-dashes etc.) doesn't choke on cp1252.
set "PYTHONUTF8=1"

cd /d "%~dp0echo_stage0"
python main.py %*

endlocal
