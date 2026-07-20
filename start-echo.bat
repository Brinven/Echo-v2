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
REM   - The LLM server (Sindri proxy at 127.0.0.1:4610 -- configured in
REM     echo_stage0\config.json `llm_base_url` / the ECHO_LLM_URL env var; falls back
REM     to LM Studio :1234 if unset). GUI apps, so this launcher only CHECKS the
REM     endpoint -- open Sindri (or LM Studio) yourself. main.py stops with a clear
REM     message if it's down.
REM   - Kokoro-FastAPI at 127.0.0.1:8880. This launcher AUTO-STARTS it if it isn't
REM     already running (from KOKORO_BAT below) and waits until it responds, so you
REM     only have to run THIS file.
REM
REM Dashboard: comes up automatically, full-screen on the 10" touchscreen (see
REM ECHO_KIOSK below). It's also at http://127.0.0.1:7862 in any browser.
REM ============================================================================

setlocal enableextensions

REM Force UTF-8 so console output (em-dashes etc.) doesn't choke on cp1252.
set "PYTHONUTF8=1"

REM ---- Auto-open the dashboard on the 10" touchscreen ------------------------
REM The panel runs Echo and nothing else (Michael, 2026-07-15), so one launcher brings up
REM both. Set to 0 to stop taking the screen over -- start-dashboard.bat still works by hand.
set "ECHO_KIOSK=1"

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

REM ---- LLM server (GUI app -- we can only check it, not start it) ----
REM Resolve the configured endpoint through llm.py so this pre-flight can never
REM disagree with what Echo actually dials (single source: llm.LLM_BASE_URL).
set "LLM_URL=http://127.0.0.1:1234/v1"
for /f "usebackq delims=" %%u in (`".venv\Scripts\python.exe" -c "from llm import LLM_BASE_URL; print(LLM_BASE_URL)" 2^>nul`) do set "LLM_URL=%%u"
curl -s -m 2 -o NUL "%LLM_URL%/models" 2>nul
if errorlevel 1 (
    echo  LLM server: NOT reachable at %LLM_URL% -- start Sindri ^(or LM Studio^) and serve a model.
) else (
    echo  LLM server: reachable at %LLM_URL%.
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

REM ---- Dashboard kiosk (background; opens once Echo is actually serving) ----
REM Spawned BEFORE main.py because main.py holds the foreground until Echo exits -- anything
REM after it would never run. It polls for the dashboard and only then opens, so it can't land
REM on an error page. It also no-ops if a kiosk is already open, so a restart won't stack them.
if "%ECHO_KIOSK%"=="1" (
    if exist "%~dp0start-dashboard.bat" (
        echo  Dashboard : will open on the touchscreen once Echo is serving.
        start "Echo kiosk" /min "%~dp0start-dashboard.bat" --wait
    ) else (
        echo  Dashboard : start-dashboard.bat not found -- open http://127.0.0.1:7862 yourself.
    )
)

echo.
echo  Starting Echo...  (dashboard: http://127.0.0.1:7862)
echo.

".venv\Scripts\python.exe" main.py %*

endlocal
