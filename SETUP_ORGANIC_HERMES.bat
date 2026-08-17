@echo off
setlocal
cd /d "%~dp0"
call "%~dp0ORGANIC_ENV.bat"

echo [Organic AI] Setting up the combined Hermes and Organic Python environment.

if not exist .venv\Scripts\python.exe (
    where py >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        py -3 -m venv .venv
    ) else (
        python -m venv .venv
    )
    if %ERRORLEVEL% NEQ 0 goto fail
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
if %ERRORLEVEL% NEQ 0 goto fail

python -m pip install -e .
if %ERRORLEVEL% NEQ 0 goto fail

python -m pip install mcp==1.28.1
if %ERRORLEVEL% NEQ 0 goto fail

if not exist organic_runtime\.venv\Scripts\python.exe (
    call organic_runtime\setup_local.bat
    if %ERRORLEVEL% NEQ 0 goto fail
)

echo [Organic AI] Setup complete.
echo [Organic AI] Start the GUI with RUN_ORGANIC_HERMES_GUI.bat
exit /b 0

:fail
echo [Organic AI] Setup failed with exit code %ERRORLEVEL%.
exit /b %ERRORLEVEL%
