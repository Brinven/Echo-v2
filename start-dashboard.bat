@echo off
REM ============================================================================
REM Echo dashboard kiosk launcher -- opens the control panel full-screen on the
REM 10" touchscreen and nothing else.
REM
REM Chrome in --kiosk: no tabs, no address bar, no title bar. Alt+F4 exits (or run
REM stop-dashboard.bat from the desk). Echo must already be running -- start-echo.bat
REM serves the dashboard from inside the Echo process.
REM
REM WHY A SEPARATE --user-data-dir: without it, launching Chrome while Chrome is
REM already open just hands the URL to the EXISTING window on the main monitor and
REM exits -- the kiosk flags are silently ignored. A separate profile forces its own
REM browser process, which is the only way this reliably lands on the touchscreen.
REM It also keeps Michael's real Chrome profile (tabs, sessions, logins) untouched,
REM and gives stop-dashboard.bat a unique string to anchor its kill filter to.
REM
REM WINDOW POSITION: --window-position picks the MONITOR by virtual-desktop
REM coordinate; kiosk then fills that display. 0,1080 = the 10" panel (1280x800,
REM below the primary) as of 2026-07-15. If you rearrange displays in Windows
REM Settings, update KIOSK_POS -- check the layout with:
REM     powershell -c "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Screen]::AllScreens | %% { $_.DeviceName + ' ' + $_.Bounds }"
REM ============================================================================

setlocal enableextensions

set "DASH_URL=http://127.0.0.1:7862"
set "KIOSK_POS=0,1080"
set "KIOSK_PROFILE=%LOCALAPPDATA%\Echo\kiosk-profile"

set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" (
    echo.
    echo  ERROR: Chrome not found. Edit CHROME in this file, or use Edge:
    echo         "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
    echo.
    exit /b 1
)

REM Don't stack kiosks. Re-running this (or restarting Echo, which auto-launches it) would
REM otherwise open a SECOND kiosk window on the same profile, and the panel has no window
REM management by touch -- you'd be stuck looking at whichever landed on top.
powershell -NoProfile -Command ^
  "$a = [regex]::Escape('%KIOSK_PROFILE%');" ^
  "if (Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object { $_.CommandLine -match $a }) { exit 9 } else { exit 0 }"
if errorlevel 9 (
    echo.
    echo  Kiosk is already open on the touchscreen -- nothing to do.
    echo  ^(stop-dashboard.bat closes it.^)
    echo.
    exit /b 0
)

REM --wait: poll until Echo is serving, THEN open. Used by start-echo.bat, which launches this
REM in the background before main.py takes over the foreground -- the dashboard only exists once
REM main.py is up, so opening immediately would land the kiosk on an error page it cannot
REM navigate off (no address bar, no keyboard on the panel).
if /i "%~1"=="--wait" goto wait_for_dash

REM Manual run: Echo should already be up. Warn but still open -- a touch refresh is possible
REM once it comes up, and Michael may be opening this deliberately ahead of Echo.
curl -s -m 2 -o NUL "%DASH_URL%/api/state" 2>nul
if errorlevel 1 (
    echo.
    echo  WARNING: nothing is serving %DASH_URL%
    echo           Run start-echo.bat first -- the dashboard lives inside the Echo process.
    echo           Opening the kiosk anyway; refresh it once Echo is up.
    echo.
)
goto open_kiosk

:wait_for_dash
echo  Waiting for Echo to serve %DASH_URL% before opening the kiosk...
set /a _dtries=0
:dash_wait_loop
curl -s -m 2 -o NUL "%DASH_URL%/api/state" 2>nul
if not errorlevel 1 goto open_kiosk
set /a _dtries+=1
REM ~90s: Echo's startup loads STT, the MiniLM embedder, and possibly the ECAPA voice model.
if %_dtries% GEQ 45 goto dash_giveup
ping -n 3 127.0.0.1 >nul
goto dash_wait_loop
:dash_giveup
echo.
echo  Echo never came up at %DASH_URL% after ~90s -- NOT opening the kiosk.
echo  (An empty kiosk can't be navigated off by touch.) Check the Echo window for errors,
echo  then run start-dashboard.bat by hand once it's serving.
echo.
exit /b 1

:open_kiosk
echo.
echo  Opening the Echo dashboard in kiosk mode on the touchscreen at %KIOSK_POS%...
echo  (Alt+F4 to exit, or run stop-dashboard.bat)
echo.

REM --overscroll-history-navigation=0 : a swipe-right on a touchscreen is a BACK gesture
REM     by default. On a single-page dashboard that navigates to nothing and strands you.
REM --disable-pinch : no accidental pinch-zoom knocking the layout off.
REM --noerrdialogs / --disable-session-crashed-bubble : never block the panel with a modal
REM     that needs a mouse to dismiss (the panel is touch-only; a "Restore pages?" bubble
REM     after a hard shutdown would sit there forever).
start "" "%CHROME%" ^
    --user-data-dir="%KIOSK_PROFILE%" ^
    --kiosk ^
    --app="%DASH_URL%" ^
    --window-position=%KIOSK_POS% ^
    --no-first-run ^
    --no-default-browser-check ^
    --noerrdialogs ^
    --disable-session-crashed-bubble ^
    --disable-infobars ^
    --overscroll-history-navigation=0 ^
    --disable-pinch ^
    --disable-features=TranslateUI

endlocal
