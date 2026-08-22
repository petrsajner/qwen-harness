@echo off
rem ============================================================
rem  Build instalatoru QwenHarness (Inno Setup 6)
rem  - najde ISCC, pripadne doinstaluje Inno Setup pres winget
rem  - verzi cte z installer\version.txt
rem ============================================================
setlocal
cd /d "%~dp0"

set "VERFILE=version.txt"
if not exist "%VERFILE%" ( echo [CHYBA] Chybi %VERFILE%. & pause & exit /b 1 )
set /p VERSION=<"%VERFILE%"
set VERSION=%VERSION: =%

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
"%ISCC%" "/DMyAppVersion=%VERSION%" "qwen-harness.iss"
if errorlevel 1 ( echo [CHYBA] Kompilace selhala. & pause & exit /b 1 )

echo.
echo [BUILD] HOTOVO: dist\QwenHarness-Setup-%VERSION%.exe
pause
