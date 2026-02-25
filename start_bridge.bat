@echo off
REM ── SSI Cookie Bridge — lightweight starter ────────────────────────────────
REM Use this when the VPN is already connected and you just need the bridge.
REM Does NOT reconnect the VPN or open Chrome — just starts cookie_bridge.py.
REM
REM Lets you use "Inspect Page" and "Run Script" from the UI without running
REM the full launch_chrome.bat (useful after a reboot when VPN is already up).
REM ──────────────────────────────────────────────────────────────────────────

cd /d "%~dp0"

if exist "venv\Scripts\activate.bat" call venv\Scripts\activate.bat

echo SSI Cookie Bridge starting on port 9223...
echo Stop with Ctrl+C
echo.
python "%~dp0cookie_bridge.py"
pause
