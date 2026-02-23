@echo off
REM ---- UPDATE THIS after the new Railway app is deployed ----
set SSI_URL=https://YOUR-NEW-RAILWAY-URL.up.railway.app
REM -----------------------------------------------------------

REM ---- VPN credentials --------------------------------------
set VPN_SERVER=remote.select-sales.com:4433
set VPN_USERNAME=sspain
set VPN_PASSWORD=YOUR_VPN_PASSWORD_HERE
set VPN_DOMAIN=ssi.local
set NECLI="C:\Program Files (x86)\SonicWall\SSL-VPN\NetExtender\NECLI.exe"
REM -----------------------------------------------------------

cd /d "%~dp0"

echo [1/4] Disconnecting any existing VPN session...
%NECLI% disconnect >nul 2>&1

echo [2/4] Connecting to %VPN_SERVER%...
%NECLI% connect -s %VPN_SERVER% -u %VPN_USERNAME% -p %VPN_PASSWORD% -d %VPN_DOMAIN%

if %errorlevel% == 0 (
    echo  VPN connected successfully!
) else (
    echo  VPN connection failed. Continuing anyway...
)

echo [3/4] Closing ALL existing Chrome windows...
taskkill /F /IM chrome.exe >nul 2>&1
timeout /t 3 /nobreak >nul

if exist "venv\Scripts\activate.bat" call venv\Scripts\activate.bat
start "SSI Cookie Bridge" /min python "%~dp0cookie_bridge.py"

echo [4/4] Opening Chrome with remote debugging on port 9222...

set CHROME=
for %%p in (
    "%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"
    "%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"
    "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
) do (
    if not defined CHROME if exist %%p set CHROME=%%p
)
if not defined CHROME set CHROME=chrome

start "" %CHROME% ^
    --remote-debugging-port=9222 ^
    --user-data-dir="%LOCALAPPDATA%\SSI_Chrome" ^
    "http://edw.select-sales.com/" ^
    "%SSI_URL%"

echo Done! Select Sales opens in Tab 1, SSI Interact opens in Tab 2.
pause
