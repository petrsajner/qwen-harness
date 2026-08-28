@echo off
rem ============================================================
rem  Marvin - CLI (terminal UI) launcher
rem  Opens a console window with the interactive TUI.
rem ============================================================
setlocal
cd /d "%~dp0"
title Marvin - CLI

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Python environment is missing.
    echo Run "Set up environment and models" from the Start Menu first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" tui.py
if errorlevel 1 (
    echo.
    echo [ERROR] The CLI exited with an error - see the message above.
    pause
)
endlocal
