; ============================================================
;  Qwen3.8-27B Harness - instalator (Inno Setup 6)
;  Build:  installer\build_installer.bat  →  dist\QwenHarness-Setup-1.0.0.exe
; ============================================================

; Verzi lze předefinovat z příkazové řádky: ISCC /DMyAppVersion=x.y.z
; (používá installer\release.bat s verzí z installer\version.txt)
#ifndef MyAppVersion
#define MyAppVersion "1.1.0"
#endif

#define MyAppName "Qwen3.8-27B Harness"
#define MyAppPublisher "Petr - lokalni AI harness"
#define MyAppExeName "QwenHarness.exe"
#define MyAppIcon "..\app_icon.ico"

[Setup]
AppId={{8F3A2C1B-6D5E-4F8A-9B7C-2E1D0A4B5C6E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\QwenHarness
DefaultGroupName={#MyAppName}
; bez admin prav - instalace do uzivatelskeho profilu
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=QwenHarness-Setup-{#MyAppVersion}
SetupIconFile={#MyAppIcon}
UninstallDisplayIcon={app}\app_icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no

[Languages]
Name: "cze"; MessagesFile: "compiler:Languages\Czech.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; hlavní aplikace (PyInstaller: exe + _internal)
Source: "..\dist\QwenHarness\QwenHarness.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\QwenHarness\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
; podpůrné zdroje (harness jádro, skripty, config)
Source: "..\qwen_app.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\webapp.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\tui.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\run_app.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\config.yaml"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\app_icon.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\harness\*.py"; DestDir: "{app}\harness"; Flags: ignoreversion recursesubdirs
Source: "..\harness\tools\*.py"; DestDir: "{app}\harness\tools"; Flags: ignoreversion
Source: "..\scripts\*.py"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "..\tests\*.py"; DestDir: "{app}\tests"; Flags: ignoreversion
; setup skript (venv + modely) - spouští se po instalaci
Source: "run_setup.bat"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\Instalace prostředí a modelů (37 GB)"; Filename: "{app}\run_setup.bat"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; HLAVNI KROK: vytvori venv, stahne zavislosti, llama.cpp i modely (~37 GB)
; s prubehem v konzoli - hned po dokonceni instalatoru (default zaskrtnuto)
Filename: "{app}\run_setup.bat"; Description: "Nainstalovat prostředí a stáhnout modely (~37 GB, nutné pro provoz)"; Flags: postinstall shellexec runasoriginaluser; WorkingDir: "{app}"

[Messages]
cze.WelcomeLabel2=Tento pruvodce nainstaluje [name/ver] - lokalni AI harness pro Qwen3.8-27B (RTX 5090).%n%nPo instalaci se pri prvem spusteni automaticky stahne prostredi a modely (~37 GB).%n%nPOKRAČOVAT?
