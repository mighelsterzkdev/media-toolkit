@echo off
setlocal
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Run install.cmd first.
    set /p "_unidownExit=Press Enter to close..."
    exit /b 1
)
pushd "%~dp0"
"%~dp0.venv\Scripts\python.exe" -B -m pip install --upgrade -c constraints.txt -e ".[desktop]"
set "result=%errorlevel%"
if "%result%"=="0" (
    "%~dp0.venv\Scripts\python.exe" -B -m universal_downloader.compatibility
    if errorlevel 1 set "result=1"
)
popd
if "%~1"=="" set /p "_unidownExit=Press Enter to close..."
exit /b %result%
