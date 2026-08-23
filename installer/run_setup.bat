@echo off
rem ============================================================
rem  Qwen3.8-27B Harness - environment and models setup
rem  (run by the installer right after setup, or from the
rem   Start Menu shortcut "Set up environment and models")
rem ============================================================
setlocal
cd /d "%~dp0"
title Qwen3.8-27B Harness - environment setup

echo ============================================================
echo  [1/4] Python environment (venv)
echo ============================================================
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 (
        echo.
        echo [ERROR] Python 3.12 not found!
        echo Install it from https://www.python.org/downloads/ ^(check "Add to PATH"^)
        echo then run this setup again from the Start Menu.
        pause
        exit /b 1
    )
)

echo ============================================================
echo  [2/4] Python dependencies
echo ============================================================
".venv\Scripts\python.exe" scripts\sync_deps.py
if errorlevel 1 ( echo [ERROR] Dependency installation failed - check your internet connection. & pause & exit /b 1 )

echo ============================================================
echo  [3/4] llama.cpp CUDA binaries (~540 MB)
echo ============================================================
".venv\Scripts\python.exe" scripts\download_llama.py
if errorlevel 1 ( echo [ERROR] llama.cpp download failed. & pause & exit /b 1 )

echo ============================================================
echo  [4/4] Qwen Q4 + Q5 + Ornith Q5 models + vision (~59 GiB)
echo         (may take a long time depending on your connection)
echo ============================================================
".venv\Scripts\python.exe" scripts\download_models.py --model all
if errorlevel 1 ( echo [ERROR] Model download failed - run again, only missing files are re-downloaded. & pause & exit /b 1 )

echo.
echo ============================================================
echo  DONE! The environment is ready.
echo  Start Qwen3.8-27B Harness from the Start Menu / desktop icon.
echo ============================================================
pause
endlocal
