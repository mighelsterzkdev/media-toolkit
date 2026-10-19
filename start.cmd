@echo off
setlocal
chcp 65001 >nul
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Run install.cmd first.
    set /p "_unidownExit=Press Enter to close..."
    exit /b 1
)
pushd "%~dp0"
if "%~1"=="" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" -B -X utf8 -m universal_downloader.gui
    popd
    exit /b 0
)
"%~dp0.venv\Scripts\python.exe" -B -X utf8 -m universal_downloader %*
set "result=%errorlevel%"
popd
if "%~1"=="" if not "%result%"=="0" if not "%result%"=="130" set /p "_unidownExit=Press Enter to close..."
exit /b %result%
