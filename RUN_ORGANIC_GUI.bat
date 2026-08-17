@echo off
setlocal
cd /d "%~dp0"
call "%~dp0ORGANIC_ENV.bat"

if not exist organic_runtime\.venv\Scripts\python.exe (
    echo [Organic AI] Embedded runtime environment not found.
    echo [Organic AI] Run: organic_runtime\setup_local.bat
    exit /b 1
)

where ollama >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [Organic AI] Ollama is required for Qwen.
    exit /b 1
)

ollama list | findstr /I /C:"qwen3:0.6b" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [Organic AI] qwen3:0.6b is missing. Run: ollama pull qwen3:0.6b
    exit /b 1
)

organic_runtime\.venv\Scripts\python.exe -m organic_runtime gui --host 127.0.0.1 --port 8787 %*
exit /b %ERRORLEVEL%
