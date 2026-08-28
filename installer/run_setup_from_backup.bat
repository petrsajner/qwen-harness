@echo off
rem Select a portable offline backup, remember it, and run the normal setup.
setlocal
cd /d "%~dp0"
set "BACKUP="
for /f "usebackq delims=" %%I in (`powershell -NoProfile -STA -Command "Add-Type -AssemblyName System.Windows.Forms; $d=New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description='Select Marvin offline backup'; if($d.ShowDialog() -eq 'OK'){[Console]::Write($d.SelectedPath)}"`) do set "BACKUP=%%I"
if not defined BACKUP exit /b 0
if not exist "%BACKUP%\manifest.json" (
    echo [ERROR] The selected folder does not contain manifest.json:
    echo %BACKUP%
    pause
    exit /b 1
)
if not exist "runtime" mkdir "runtime"
>"runtime\offline-backup-path.txt" echo %BACKUP%
set "QWEN_HARNESS_BACKUP=%BACKUP%"
set "QWEN_HARNESS_BACKUP_PREFER=1"
call run_setup.bat
endlocal
