@echo off
REM TextTaskManager - Windows Launcher
REM Double-click this file to open both the web interface and the terminal CLI

cd /d "%~dp0"

REM Check if Python is available
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Error: Python is not installed or not in PATH.
    echo Please install Python from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

echo Starting TextTaskManager...
echo The browser will open with the Web UI.
echo You can also use the terminal interface below.
echo Type 'q' to quit (this will also stop the web server).
echo.

python task_manager.py --web

pause
