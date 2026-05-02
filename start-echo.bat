@echo off
REM Echo launcher. Reads HINDSIGHT_API_TENANT_API_KEY from the Hindsight project's
REM env file and exposes it to Python as HINDSIGHT_API_KEY, then runs main.py.
REM Hindsight must be running locally (PM2 process hindsight-memory).

setlocal enableextensions enabledelayedexpansion

set "HINDSIGHT_ENV=H:\AxlyGitHub_H\HindSight\hindsight.env"

if not exist "%HINDSIGHT_ENV%" (
  echo [echo] WARNING: %HINDSIGHT_ENV% not found.
  echo [echo] Memory will be unavailable this session.
  goto :run
)

for /f "usebackq tokens=1,* delims==" %%A in ("%HINDSIGHT_ENV%") do (
  if /I "%%A"=="HINDSIGHT_API_TENANT_API_KEY" set "HINDSIGHT_API_KEY=%%B"
)

if not defined HINDSIGHT_API_KEY (
  echo [echo] WARNING: HINDSIGHT_API_TENANT_API_KEY not found in %HINDSIGHT_ENV%.
  echo [echo] Memory will be unavailable this session.
)

set "HINDSIGHT_URL=http://127.0.0.1:8888"
set "HINDSIGHT_BANK_ID=echo"
set "PYTHONUTF8=1"

:run
cd /d "%~dp0echo_stage0"
python main.py %*
endlocal
