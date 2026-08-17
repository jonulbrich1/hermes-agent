@echo off
setlocal
cd /d "%~dp0"
call "%~dp0ORGANIC_ENV.bat"
set npm_config_engine_strict=false

if exist .venv\Scripts\python.exe (
    set PYEXE=%CD%\.venv\Scripts\python.exe
) else (
    set PYEXE=python
)

"%PYEXE%" -c "import fastapi, uvicorn, mcp; import hermes_cli.main" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [Organic AI] Combined Hermes and Organic dependencies are not ready.
    echo [Organic AI] Run: SETUP_ORGANIC_HERMES.bat
    exit /b 1
)

if not exist organic_runtime\.venv\Scripts\python.exe (
    echo [Organic AI] Organic runtime environment is not ready.
    echo [Organic AI] Run: organic_runtime\setup_local.bat
    exit /b 1
)

"%PYEXE%" scripts\configure_organic_hermes.py >nul
if %ERRORLEVEL% NEQ 0 (
    echo [Organic AI] Could not enable the Hermes plugin or configure MCP.
    exit /b 1
)
set ORGANIC_HERMES_PLUGIN_ENABLED=1

"%PYEXE%" scripts\ensure_organic_runtime.py
if %ERRORLEVEL% NEQ 0 (
    echo [Organic AI] The shared Organic runtime did not start.
    exit /b 1
)

where ollama >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [Organic AI] Warning: Ollama was not found. The Organic tab will show the Qwen connection error.
) else (
    ollama list | findstr /I /C:"qwen3:0.6b" >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo [Organic AI] Warning: qwen3:0.6b is missing. Run: ollama pull qwen3:0.6b
    )
    ollama list | findstr /I /C:"%ORGANIC_HERMES_CHAT_MODEL%" >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo [Organic AI] Warning: %ORGANIC_HERMES_CHAT_MODEL% is missing. Run: ollama pull %ORGANIC_HERMES_CHAT_MODEL%
    )
)

echo [Organic AI] Starting Hermes dashboard with the Organic workflow tab.
echo [Organic AI] Open http://127.0.0.1:9119/organic if the browser does not open there.
"%PYEXE%" -m hermes_cli.main dashboard --host 127.0.0.1 --port 9119 %*
exit /b %ERRORLEVEL%
