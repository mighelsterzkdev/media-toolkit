@echo off
setlocal
chcp 65001 >nul
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Run install.cmd first.
    set /p "_unidownExit=Press Enter to close..."
    exit /b 1
)
pushd "%~dp0"
"%~dp0.venv\Scripts\python.exe" -X utf8 -m universal_downloader %*
set "result=%errorlevel%"
popd
if "%~1"=="" set /p "_unidownExit=Press Enter to close..."
exit /b %result%
