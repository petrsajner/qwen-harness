@echo off
rem ============================================================
rem  Build instalatoru QwenHarness (Inno Setup 6)
rem  - najde ISCC, pripadne doinstaluje Inno Setup pres winget
rem  - vysledek: dist\QwenHarness-Setup-1.0.0.exe
rem ============================================================
setlocal
cd /d "%~dp0"

set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"

if not defined ISCC (
    echo [BUILD] Inno Setup 6 nenalezen - instaluji pres winget...
    winget install JRSoftware.InnoSetup -e --accept-source-agreements --accept-package-agreements --silent
    if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
    if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
)

if not defined ISCC (
    echo [CHYBA] Inno Setup se nepodarilo nainstalovat. Nainstaluj rucne: https://jrsoftware.org/isdl.php
    pause
    exit /b 1
)

echo [BUILD] Kompiluji instalator (%ISCC%)...
"%ISCC%" "qwen-harness.iss"
if errorlevel 1 ( echo [CHYBA] Kompilace selhala. & pause & exit /b 1 )

echo.
echo [BUILD] HOTOVO: dist\QwenHarness-Setup-1.0.0.exe
pause
