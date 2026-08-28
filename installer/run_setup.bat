@echo off
rem ============================================================
rem  Marvin - environment and models setup
rem  (run by the installer right after setup, or from the
rem   Start Menu shortcut "Set up environment and models")
rem ============================================================
setlocal
cd /d "%~dp0"
title Marvin - environment setup

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

set "BACKUP="
if defined QWEN_HARNESS_BACKUP set "BACKUP=%QWEN_HARNESS_BACKUP%"
if exist "runtime\offline-backup-path.txt" set /p BACKUP=<"runtime\offline-backup-path.txt"
set "BACKUP_OK="
if defined BACKUP (
    if exist "%BACKUP%\manifest.json" (
        set "BACKUP_OK=1"
        echo [BACKUP] Local fallback is available: %BACKUP%
    ) else (
        echo [WARNING] Configured offline backup has no manifest.json: %BACKUP%
    )
)
if defined QWEN_HARNESS_BACKUP_PREFER if defined BACKUP_OK (
    echo [BACKUP] Explicit offline setup selected - restoring local components first.
    ".venv\Scripts\python.exe" scripts\offline_backup.py restore --backup "%BACKUP%" --root "%CD%"
    if errorlevel 1 ( echo [ERROR] Offline backup restore failed. & pause & exit /b 1 )
)

echo ============================================================
echo  [2/4] Python dependencies
echo ============================================================
".venv\Scripts\python.exe" scripts\sync_deps.py
if errorlevel 1 if defined BACKUP_OK (
    echo [BACKUP] Online dependency installation failed - restoring local Python packages.
    ".venv\Scripts\python.exe" scripts\offline_backup.py restore --backup "%BACKUP%" --root "%CD%" --components python-dependencies
    if not errorlevel 1 ".venv\Scripts\python.exe" scripts\sync_deps.py
)
if errorlevel 1 ( echo [ERROR] Dependency installation failed online and from backup. & pause & exit /b 1 )

echo ============================================================
echo  [3/4] llama.cpp CUDA binaries (~540 MB)
echo ============================================================
".venv\Scripts\python.exe" scripts\download_llama.py
if errorlevel 1 if defined BACKUP_OK (
    echo [BACKUP] Online llama.cpp download failed - restoring the local runtime.
    ".venv\Scripts\python.exe" scripts\offline_backup.py restore --backup "%BACKUP%" --root "%CD%" --components llama
    if not errorlevel 1 ".venv\Scripts\python.exe" scripts\download_llama.py
)
if errorlevel 1 ( echo [ERROR] llama.cpp is unavailable online and in the backup. & pause & exit /b 1 )

echo ============================================================
echo  [4/4] Qwen/Ornith models + vision projectors
echo         (selection from the installer; re-running downloads
echo          only missing files - may take a long time)
echo ============================================================
set "MODELS="
if exist "runtime\model-selection.txt" set /p MODELS=<"runtime\model-selection.txt"
if "%MODELS%"=="" (
    set "MODEL_ARGS=--model auto"
) else (
    echo [SETUP] Selected models: %MODELS%
    set "MODEL_ARGS=--models %MODELS%"
)
".venv\Scripts\python.exe" scripts\download_models.py %MODEL_ARGS%
if errorlevel 1 if defined BACKUP_OK (
    echo [BACKUP] A selected model is unavailable online - restoring local model files.
    ".venv\Scripts\python.exe" scripts\offline_backup.py restore --backup "%BACKUP%" --root "%CD%" --components models
    if not errorlevel 1 ".venv\Scripts\python.exe" scripts\download_models.py %MODEL_ARGS%
)
if errorlevel 1 ( echo [ERROR] A selected model is unavailable online and in the backup. & pause & exit /b 1 )

echo.
echo ============================================================
echo  DONE! The environment is ready.
echo  Start Marvin from the Start Menu / desktop icon.
echo ============================================================
pause
endlocal
