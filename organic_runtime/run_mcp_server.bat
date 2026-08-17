@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo [Organic AI] Environment not found. Run setup_local.bat first. 1>&2
    exit /b 1
)
set ORGANIC_BACKEND=mvp
set ORGANIC_IDLE_GROWTH_ENABLED=1
set ORGANIC_WEB_PROVIDER=auto
set ORGANIC_SEMANTIC_MODE=pydantic
set ORGANIC_MODEL=ollama:qwen3:0.6b
set OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
where ollama >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [Organic AI] Ollama is required for the Qwen Semantic Interface. 1>&2
    exit /b 1
)
ollama list | findstr /I /C:"qwen3:0.6b" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [Organic AI] Qwen Semantic Interface model is missing. Run ..\setup_qwen.bat first. 1>&2
    exit /b 1
)
set PYTHONPATH=%CD%\src;%PYTHONPATH%
.venv\Scripts\python.exe -m organic_runtime mcp
exit /b %ERRORLEVEL%
