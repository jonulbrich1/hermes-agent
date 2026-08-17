@echo off
setlocal
cd /d "%~dp0"
call "%~dp0RUN_ORGANIC_HERMES_GUI.bat" %*
exit /b %ERRORLEVEL%
