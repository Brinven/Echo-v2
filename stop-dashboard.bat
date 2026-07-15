@echo off
REM ============================================================================
REM Close the Echo dashboard kiosk. Does NOT stop Echo itself (use the dashboard's
REM Stop button or Ctrl+C in the Echo window for that).
REM
REM KILL FILTER SAFETY: anchored to the kiosk's unique --user-data-dir path
REM (...\Echo\kiosk-profile), NOT to "chrome" or "Echo". Michael's real Chrome --
REM tabs, logins, everything -- runs under a different profile and is never matched.
REM Verify before changing this filter:
REM     powershell -c "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Select-Object ProcessId, CommandLine"
REM ============================================================================

setlocal enableextensions

set "KIOSK_PROFILE=%LOCALAPPDATA%\Echo\kiosk-profile"

powershell -NoProfile -Command ^
  "$anchor = [regex]::Escape('%KIOSK_PROFILE%');" ^
  "$procs = Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object { $_.CommandLine -match $anchor };" ^
  "if (-not $procs) { Write-Host '  No Echo kiosk running.'; exit 0 };" ^
  "$procs | ForEach-Object { Write-Host ('  Closing kiosk PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue };" ^
  "Write-Host '  Kiosk closed. Echo itself is untouched.'"

endlocal
