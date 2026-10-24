@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "result=%errorlevel%"
if "%~1"=="" set /p "_unidownExit=Press Enter to close..."
exit /b %result%
