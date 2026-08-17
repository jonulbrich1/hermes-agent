@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo [Organic AI] Environment not found. Run setup_local.bat first.
    exit /b 1
)
echo [Organic AI] Starting diagnostic GUI with heuristic Semantic Interface.
set ORGANIC_BACKEND=mvp
set ORGANIC_IDLE_GROWTH_ENABLED=1
set ORGANIC_WEB_PROVIDER=auto
set ORGANIC_SEMANTIC_MODE=heuristic
set PYTHONPATH=%CD%\src;%PYTHONPATH%
.venv\Scripts\python.exe -m organic_runtime gui --host 127.0.0.1 --port 8787 %*
exit /b %ERRORLEVEL%
