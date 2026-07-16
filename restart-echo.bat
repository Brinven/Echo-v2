@echo off
REM ============================================================================
REM Restart Echo: stop -> brief pause -> start.
REM
REM stop-echo.bat tries the graceful path first (which saves the session and writes
REM the summary), so a restart is not lossy. The pause is there to let the mic device
REM and port 7862 actually free up -- starting into a half-released port makes
REM start_webui's port probe report "taken" and Echo comes up with NO control surface.
REM
REM The kiosk is deliberately NOT closed: start-echo.bat's kiosk launcher no-ops when
REM one is already open, and the dashboard page heals itself (it shows ECHO IS OFFLINE
REM while she's down and clears the moment she's serving again). Closing and reopening
REM it would just make the panel flash for no reason.
REM ============================================================================

setlocal enableextensions

call "%~dp0stop-echo.bat"

echo.
echo  Waiting for the mic and port 7862 to release...
ping -n 4 127.0.0.1 >nul

echo.
call "%~dp0start-echo.bat" %*

endlocal
