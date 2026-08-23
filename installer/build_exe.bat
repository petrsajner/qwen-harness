@echo off
rem ============================================================
rem  Build QwenHarness.exe (PyInstaller, onedir s ikonou)
rem  Vysledek: dist\QwenHarness\QwenHarness.exe (+ _internal\)
rem ============================================================
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [CHYBA] Chybi .venv - spust scripts\setup_env.py
    pause & exit /b 1
)

echo [BUILD] Instaluji PyInstaller...
".venv\Scripts\python.exe" -m pip install pyinstaller --quiet

echo [BUILD] Kompiluji QwenHarness.exe...
".venv\Scripts\python.exe" -m PyInstaller ^
    --noconfirm --clean --onedir --noconsole ^
    --name QwenHarness ^
    --icon app_icon.ico ^
    --add-data "installer\version.txt;." ^
    --collect-all webview ^
    --hidden-import clr ^
    launcher\launcher_app.py
if errorlevel 1 ( echo [CHYBA] Kompilace selhala. & pause & exit /b 1 )

echo [BUILD] HOTOVO: dist\QwenHarness\QwenHarness.exe
endlocal
