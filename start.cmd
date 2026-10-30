@echo off
setlocal
chcp 65001 >nul
set "bundle=%~dp0dist\media-toolkit-0.12.0-windows-x64"
if "%~1"=="" goto desktop
if exist "%bundle%\unidown.exe" goto portable_cli
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Run scripts\install.cmd first.
    set /p "_unidownExit=Press Enter to close..."
    exit /b 1
)
pushd "%~dp0"
"%~dp0.venv\Scripts\python.exe" -B -X utf8 -m universal_downloader %*
set "result=%errorlevel%"
popd
exit /b %result%

:portable_cli
pushd "%bundle%"
"%bundle%\unidown.exe" %*
set "result=%errorlevel%"
popd
exit /b %result%

:desktop
if exist "%bundle%\media-toolkit.exe" (
    start "" /d "%bundle%" "%bundle%\media-toolkit.exe"
    exit /b 0
)
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo Run scripts\install.cmd first.
    set /p "_unidownExit=Press Enter to close..."
    exit /b 1
)
pushd "%~dp0"
start "" "%~dp0.venv\Scripts\pythonw.exe" -B -X utf8 -m universal_downloader.gui
popd
exit /b 0
