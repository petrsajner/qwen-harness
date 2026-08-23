@echo off
rem ============================================================
rem  Qwen3.8-27B Harness - desktop app launcher
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

echo [SETUP] Checking dependencies...
.venv\Scripts\python.exe scripts\sync_deps.py
if errorlevel 1 ( echo [ERROR] Dependency installation failed. & pause & exit /b 1 )

if not exist "runtime\llama\llama-server.exe" (
    echo [SETUP] Downloading llama.cpp CUDA binaries (~540 MB)...
    .venv\Scripts\python.exe scripts\download_llama.py
    if errorlevel 1 ( echo [ERROR] llama.cpp download failed. & pause & exit /b 1 )
)

if not exist "runtime\models\mmproj-F16.gguf" (
    echo [SETUP] Downloading Qwen3.8-27B Q4+Q5 models (~37 GB, may take long)...
    .venv\Scripts\python.exe scripts\download_models.py --model all
    if errorlevel 1 ( echo [ERROR] Model download failed. & pause & exit /b 1 )
)

echo [APP] Starting Qwen3.8-27B Harness...
start "" ".venv\Scripts\pythonw.exe" "qwen_app.py"
endlocal
