@echo off
REM ============================================================================
REM Stop Echo. Graceful first -- POST /api/quit is the same path the dashboard's
REM "Stop Echo" button uses, which reaches the session save and the summary. Force
REM only if that doesn't take.
REM
REM Echo also saves after every turn now, so a force-kill loses at most the turn in
REM flight. Before 2026-07-16 it saved ONLY at the end, there was no stop script, and
REM closing the window was the only way to stop her -- so every conversation between
REM 2026-07-14 and 2026-07-16 was thrown away. That is why this file exists.
REM
REM ---------------------------------------------------------------------------
REM KILL FILTER SAFETY -- read this before touching the filter.
REM
REM Anchored to ExecutablePath under THIS repo's folder. Dry-run against every
REM python.exe on this box, 2026-07-16:
REM
REM   ExecutablePath -like '<repo>\*'                -> 1 proc   (Echo)          CORRECT
REM   CommandLine -match '.venv\Scripts\python.exe'  -> 6 procs  (PromptFactory,
REM        PromptVault, and Kokoro-FastAPI -- Echo's OWN TTS)                    WRONG
REM   CommandLine -match 'Echo'                      -> 14 procs                 WRONG
REM
REM A CommandLine filter CANNOT work here: Echo's command line is the RELATIVE
REM ".venv\Scripts\python.exe  main.py" and does not contain the word Echo at all.
REM "Echo" is also a substring of unrelated paths -- never match on the bare name.
REM
REM THE CHILD: the venv launcher spawns the BASE interpreter as a child process whose
REM ExecutablePath is the GLOBAL python (...\Programs\Python\Python311\python.exe).
REM No path filter can see it. It is found by ParentProcessId and must be killed too,
REM or a force-stop leaves half of Echo alive holding the mic and the port.
REM
REM Re-verify before changing the filter:
REM   powershell -c "Get-CimInstance Win32_Process -Filter \"Name='python.exe\'\" | Select-Object ProcessId, ParentProcessId, ExecutablePath, CommandLine | Format-List"
REM ============================================================================

setlocal enableextensions

set "ECHO_ROOT=%~dp0"

powershell -NoProfile -Command ^
  "$root = '%ECHO_ROOT%';" ^
  "$port = 7862;" ^
  "$cfg = Join-Path $root 'echo_stage0\echo_webui.json';" ^
  "if (Test-Path $cfg) { try { $p = (Get-Content $cfg -Raw | ConvertFrom-Json).port; if ($p) { $port = $p } } catch {} };" ^
  "$find = { @(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.ExecutablePath -like ($root + '*') }) };" ^
  "$procs = & $find;" ^
  "if (-not $procs) { Write-Host '  Echo is not running.'; exit 0 };" ^
  "$ids = @($procs.ProcessId);" ^
  "Write-Host ('  Echo PID ' + ($ids -join ', ') + ' -- asking her to stop gracefully (saves the session)...');" ^
  "try { Invoke-RestMethod -Uri ('http://127.0.0.1:' + $port + '/api/quit') -Method Post -TimeoutSec 3 | Out-Null }" ^
  "catch { Write-Host '  (dashboard did not answer -- will force)' };" ^
  "$deadline = (Get-Date).AddSeconds(15);" ^
  "while ((Get-Date) -lt $deadline -and (& $find)) { Start-Sleep -Milliseconds 400 };" ^
  "$alive = & $find;" ^
  "if (-not $alive) { Write-Host '  Echo stopped cleanly -- session saved.' } else {" ^
  "  Write-Host '  Did not exit in 15s -- forcing (the per-turn save means this is safe).';" ^
  "  $kids = @(Get-CimInstance Win32_Process | Where-Object { $ids -contains $_.ParentProcessId });" ^
  "  $kids | ForEach-Object { Write-Host ('    killing child PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue };" ^
  "  $alive | ForEach-Object { Write-Host ('    killing PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue };" ^
  "  Write-Host '  Echo stopped.' };" ^
  "$bat = [regex]::Escape((Join-Path $root 'start-echo.bat'));" ^
  "Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | Where-Object { $_.CommandLine -match $bat } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue };" ^
  "Write-Host '  (Kokoro and the kiosk are left alone -- stop-dashboard.bat closes the kiosk.)'"

endlocal
