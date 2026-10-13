@echo off
REM Mbarara Auction System - double-click to set up and start everything.
setlocal
title Mbarara Auction System
cd /d "%~dp0"

REM Find Python: "python" first, then the Windows "py" launcher.
set "PY="
python --version >nul 2>&1 && set "PY=python"
if not defined PY (
    py -3 --version >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
    echo.
    echo Python 3.10 or newer is not installed.
    echo Download it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during installation, then double-click this file again.
    echo.
    pause
    exit /b 1
)

%PY% start.py %*
set "EXITCODE=%errorlevel%"

echo.
if not "%EXITCODE%"=="0" echo Something went wrong - see the messages above.
echo Press any key to close this window.
pause >nul
exit /b %EXITCODE%
