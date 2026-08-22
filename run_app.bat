@echo off
rem ============================================================
rem  Qwen3.8-27B Harness - spoustec desktop aplikace
rem  - pri prvem spusteni vytvori venv + stahne zavislosti/modely
rem  - pak otevre nativni okno aplikace (qwen_app.py)
rem ============================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [SETUP] Prvni spusteni: vytvarim Python prostredi...
    py -3.12 -m venv .venv
    if errorlevel 1 (
        echo [CHYBA] Python 3.12 nenalezen. Nainstaluj ho z https://www.python.org/downloads/
        pause
        exit /b 1
    )
    echo [SETUP] Instaluji zavislosti...
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 ( echo [CHYBA] Instalace zavislosti selhala. & pause & exit /b 1 )
)

if not exist "runtime\llama\llama-server.exe" (
    echo [SETUP] Stahuji llama.cpp CUDA binarky (~540 MB)...
    .venv\Scripts\python.exe scripts\download_llama.py
    if errorlevel 1 ( echo [CHYBA] Download llama.cpp selhal. & pause & exit /b 1 )
)

if not exist "runtime\models\mmproj-F16.gguf" (
    echo [SETUP] Stahuji modely Qwen3.8-27B Q4+Q5 (~37 GB, muze trvat dlouho)...
    .venv\Scripts\python.exe scripts\download_models.py --model all
    if errorlevel 1 ( echo [CHYBA] Download modelu selhal. & pause & exit /b 1 )
)

echo [APP] Spoustim Qwen3.8-27B Harness...
start "" ".venv\Scripts\pythonw.exe" "qwen_app.py"
endlocal
