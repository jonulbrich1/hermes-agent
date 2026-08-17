@echo off
setlocal
cd /d "%~dp0"
echo [Organic AI] Package setup starting in %CD%

where uv >nul 2>&1
if %ERRORLEVEL% EQU 0 goto use_uv

echo [Organic AI] uv not found. Falling back to Python venv.
where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)
if %ERRORLEVEL% NEQ 0 goto fail
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
if %ERRORLEVEL% NEQ 0 goto fail
python -m pip install -e ".[dev]"
if %ERRORLEVEL% NEQ 0 goto fail
goto success

:use_uv
echo [Organic AI] uv detected. Syncing project and development dependencies.
uv sync --extra dev
if %ERRORLEVEL% NEQ 0 goto fail
goto success

:success
echo [Organic AI] Package setup complete.
exit /b 0

:fail
echo [Organic AI] Package setup failed with exit code %ERRORLEVEL%.
exit /b %ERRORLEVEL%
