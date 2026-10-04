@echo off
setlocal
cd /d "%~dp0"
title DIRECT STREAM FOR PLAYSTATION 5

echo ========================================================
echo   DIRECT STREAM FOR PLAYSTATION 5
echo   Starting local transfer dashboard...
echo ========================================================
echo.

where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    python ps5_streamer.py %*
    goto :eof
)

where py >nul 2>nul
if %ERRORLEVEL% equ 0 (
    py -3 ps5_streamer.py %*
    goto :eof
)

where python3 >nul 2>nul
if %ERRORLEVEL% equ 0 (
    python3 ps5_streamer.py %*
    goto :eof
)

echo.
echo [ERROR] Python 3.9 or later was not detected on your PC!
echo.
echo Please download and install Python from:
echo   https://www.python.org/downloads/windows/
echo.
echo *IMPORTANT*: Check "Add python.exe to PATH" during installation.
echo.
pause
