@echo off
setlocal
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Run install.cmd first.
    set /p "_unidownExit=Press Enter to close..."
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -B -m pip install --upgrade "yt-dlp[default]" "Pillow>=12.0,<13"
set "result=%errorlevel%"
if "%~1"=="" set /p "_unidownExit=Press Enter to close..."
exit /b %result%
