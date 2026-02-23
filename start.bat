@echo off
cd /d "%~dp0"
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)
cd app
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
