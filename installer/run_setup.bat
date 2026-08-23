@echo off
rem ============================================================
rem  Qwen3.8-27B Harness - instalace prostredi a modelu
rem  (spousteno instalatorem po dokonceni setupu, nebo ze
rem   Start Menu zkratkou "Instalace prostredi")
rem ============================================================
setlocal
cd /d "%~dp0"
title Qwen3.8-27B Harness - instalace prostredi

echo ============================================================
echo  [1/4] Python prostredi (venv)
echo ============================================================
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 (
        echo.
        echo [CHYBA] Python 3.12 nenalezen!
        echo Nainstaluj ho z https://www.python.org/downloads/ ^(zaskrtni "Add to PATH"^)
        echo a potom spust tuhle instalaci znovu ze Start Menu.
        pause
        exit /b 1
    )
)

echo ============================================================
echo  [2/4] Python zavislosti
echo ============================================================
".venv\Scripts\python.exe" scripts\sync_deps.py
if errorlevel 1 ( echo [CHYBA] Instalace zavislosti selhala - zkontroluj internet. & pause & exit /b 1 )

echo ============================================================
echo  [3/4] llama.cpp CUDA binarky (~540 MB)
echo ============================================================
".venv\Scripts\python.exe" scripts\download_llama.py
if errorlevel 1 ( echo [CHYBA] Download llama.cpp selhal. & pause & exit /b 1 )

echo ============================================================
echo  [4/4] Modely Qwen3.8-27B Q4 + Q5 + vision (~37 GB)
echo         (muze trvat dlouho podle rychlosti internetu)
echo ============================================================
".venv\Scripts\python.exe" scripts\download_models.py --model all
if errorlevel 1 ( echo [CHYBA] Download modelu selhal - spust znovu, stahuje se jen chybejici. & pause & exit /b 1 )

echo.
echo ============================================================
echo  HOTOVO! Prostredi je pripravene.
echo  Qwen3.8-27B Harness spustis ikonou ze Start Menu / plochy.
echo ============================================================
pause
endlocal
