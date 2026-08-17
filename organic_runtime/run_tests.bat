@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo [Organic AI] Environment not found. Run setup_local.bat first.
    exit /b 1
)
.venv\Scripts\python.exe -m pytest -q tests
exit /b %ERRORLEVEL%
