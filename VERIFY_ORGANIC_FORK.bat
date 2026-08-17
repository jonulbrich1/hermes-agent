@echo off
setlocal
cd /d "%~dp0"
call "%~dp0ORGANIC_ENV.bat"

where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [Organic AI] Python was not found on PATH.
    exit /b 1
)

python tests\test_organic_ai_plugin.py
if %ERRORLEVEL% NEQ 0 exit /b %ERRORLEVEL%

python tests\test_organic_dashboard_plugin.py
if %ERRORLEVEL% NEQ 0 exit /b %ERRORLEVEL%

if exist organic_runtime\.venv\Scripts\python.exe (
    organic_runtime\.venv\Scripts\python.exe -m pytest -q organic_runtime\tests
    exit /b %ERRORLEVEL%
)

echo [Organic AI] Skipping embedded runtime pytest because organic_runtime\.venv is not set up.
echo [Organic AI] Run organic_runtime\setup_local.bat to enable full runtime tests from this fork.
exit /b 0
