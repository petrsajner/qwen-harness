@echo off
rem ============================================================
rem  RELEASE: build nove verze QwenHarness (exe + instalator)
rem  - spusti testy, prebuilduje exe, zkompiluje instalator
rem    s verzi z version.txt (stejne cislo zobrazuje aplikace)
rem  - vysledek: dist\QwenHarness-Setup-<verze>.exe
rem  Reinstall u zakladu: vse je rychle (venv/modely zustavaji,
rem  setup krok odskrtni - program se jen prekopiruje)
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0.."
set "PYTHONUTF8=1"

set "VERFILE=installer\version.txt"
if not exist "%VERFILE%" ( echo 1.2.0> "%VERFILE%" )
set /p VERSION=<"%VERFILE%"
set VERSION=%VERSION: =%

echo ============================================================
echo  RELEASE %VERSION%  (testy -^> exe -^> instalator)
echo ============================================================

echo [1/3] Testy...
".venv\Scripts\python.exe" tests\test_core.py >nul 2>&1
if errorlevel 1 (
    echo [CHYBA] Testy neprosly - build zastaven.
    ".venv\Scripts\python.exe" tests\test_core.py
    pause & exit /b 1
)
echo        OK - vsechny testy prosly.

echo [2/3] Build QwenHarness.exe...
call installer\build_exe.bat
if errorlevel 1 ( echo [CHYBA] Exe build selhal. & pause & exit /b 1 )

echo [3/3] Kompilace instalatoru %VERSION%...
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC ( echo [CHYBA] ISCC nenalezen. & pause & exit /b 1 )
"%ISCC%" "/DMyAppVersion=%VERSION%" installer\qwen-harness.iss
if errorlevel 1 ( echo [CHYBA] Instalator build selhal. & pause & exit /b 1 )

echo.
echo ============================================================
echo  RELEASE HOTOVO: dist\QwenHarness-Setup-%VERSION%.exe
echo  Verze aplikace i instalatoru: %VERSION%
echo ============================================================
endlocal
