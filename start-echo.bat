@echo off
REM ============================================================================
REM Echo launcher (Stage 5-7).
REM
REM Runtime memory is Ib-Lite -- a self-contained local SQLite store at
REM echo_stage0\echo.db. No external memory server, no API keys, no Hindsight.
REM
REM Python: runs in echo_stage0\.venv (a DEDICATED venv, isolated from global
REM Python so another project's pip install can't clobber the voice-pipeline
REM deps). Create it once with:
REM     python -m venv echo_stage0\.venv
REM     echo_stage0\.venv\Scripts\python -m pip install -r echo_stage0\requirements.txt
REM
REM Servers this launcher handles for you:
REM   - LM Studio at 127.0.0.1:1234 with a model loaded (target: gemma-4-12b-it-qat).
REM     It's a GUI app, so this launcher only CHECKS it -- open LM Studio and load
REM     the model yourself. main.py stops with a clear message if it's down.
REM   - Kokoro-FastAPI at 127.0.0.1:8880. This launcher AUTO-STARTS it if it isn't
REM     already running (from KOKORO_BAT below) and waits until it responds, so you
REM     only have to run THIS file.
REM
REM Dashboard: once Echo is up, open http://127.0.0.1:7862 in a browser.
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

echo.
echo  ---- Pre-flight ----

REM ---- LM Studio (GUI app -- we can only check it, not start it) ----
curl -s -m 2 -o NUL "http://127.0.0.1:1234/v1/models" 2>nul
if errorlevel 1 (
    echo  LM Studio : NOT reachable at 127.0.0.1:1234 -- open LM Studio and load a model.
) else (
    echo  LM Studio : reachable.
)

REM ---- Kokoro-FastAPI (auto-start if it's not already up) ----
set "KOKORO_URL=http://127.0.0.1:8880/health"
set "KOKORO_BAT=H:\AxlyGitHub_H\Kokoro-FastAPI\start-kokoro.bat"
curl -s -m 2 -o NUL "%KOKORO_URL%" 2>nul
if not errorlevel 1 goto kokoro_running
if not exist "%KOKORO_BAT%" goto kokoro_nolauncher
echo  Kokoro    : not running -- starting it in a new window...
start "Kokoro-FastAPI" "%KOKORO_BAT%"
set /a _ktries=0
:kokoro_wait
ping -n 3 127.0.0.1 >nul
curl -s -m 2 -o NUL "%KOKORO_URL%" 2>nul
if not errorlevel 1 goto kokoro_up
set /a _ktries+=1
if %_ktries% GEQ 30 goto kokoro_giveup
goto kokoro_wait
:kokoro_up
echo  Kokoro    : up.
goto kokoro_done
:kokoro_giveup
echo  Kokoro    : no response after ~60s -- continuing anyway; Echo will warn if TTS is down.
goto kokoro_done
:kokoro_running
echo  Kokoro    : already running.
goto kokoro_done
:kokoro_nolauncher
echo  Kokoro    : not running, and its launcher was not found at:
echo              %KOKORO_BAT%
echo              Start Kokoro manually, or fix KOKORO_BAT in this file.
:kokoro_done

echo.
echo  Starting Echo...  (dashboard: http://127.0.0.1:7862)
echo.

".venv\Scripts\python.exe" main.py %*

endlocal
