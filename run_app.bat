@echo off
rem ============================================================
rem  Marvin - desktop app launcher
rem  - on first run it creates the venv + downloads deps/models
rem  - then opens the native app window (qwen_app.py)
rem ============================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [SETUP] First run: creating the Python environment...
    py -3.12 -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Python 3.12 not found. Install it from https://www.python.org/downloads/
        pause
        exit /b 1
    )
)

set "BACKUP="
if defined QWEN_HARNESS_BACKUP set "BACKUP=%QWEN_HARNESS_BACKUP%"
if exist "runtime\offline-backup-path.txt" set /p BACKUP=<"runtime\offline-backup-path.txt"
set "BACKUP_OK="
if defined BACKUP if exist "%BACKUP%\manifest.json" (
    set "BACKUP_OK=1"
    echo [BACKUP] Local fallback is available: %BACKUP%
)

echo [SETUP] Checking dependencies...
.venv\Scripts\python.exe scripts\sync_deps.py
if errorlevel 1 if defined BACKUP_OK (
    echo [BACKUP] Online dependency repair failed - restoring local Python packages.
    .venv\Scripts\python.exe scripts\offline_backup.py restore --backup "%BACKUP%" --root "%CD%" --components python-dependencies
    if not errorlevel 1 .venv\Scripts\python.exe scripts\sync_deps.py
)
if errorlevel 1 ( echo [ERROR] Dependency installation failed online and from backup. & pause & exit /b 1 )

if not exist "runtime\llama\llama-server.exe" (
    echo [SETUP] Downloading llama.cpp CUDA binaries (~540 MB)...
    .venv\Scripts\python.exe scripts\download_llama.py
    if errorlevel 1 if defined BACKUP_OK (
        echo [BACKUP] Online llama.cpp download failed - restoring local runtime.
        .venv\Scripts\python.exe scripts\offline_backup.py restore --backup "%BACKUP%" --root "%CD%" --components llama
        if not errorlevel 1 .venv\Scripts\python.exe scripts\download_llama.py
    )
    if errorlevel 1 ( echo [ERROR] llama.cpp is unavailable online and in the backup. & pause & exit /b 1 )
)

if not exist "runtime\models\mmproj-F16.gguf" (
    echo [SETUP] Downloading models that fit the GPU (may take long)...
    .venv\Scripts\python.exe scripts\download_models.py --model auto
    if errorlevel 1 if defined BACKUP_OK (
        echo [BACKUP] A required model is unavailable online - restoring local model files.
        .venv\Scripts\python.exe scripts\offline_backup.py restore --backup "%BACKUP%" --root "%CD%" --components models
        if not errorlevel 1 .venv\Scripts\python.exe scripts\download_models.py --model auto
    )
    if errorlevel 1 ( echo [ERROR] Models are unavailable online and in the backup. & pause & exit /b 1 )
)

echo [APP] Starting Marvin...
start "" ".venv\Scripts\pythonw.exe" "qwen_app.py"
endlocal
