; ============================================================
;  Qwen3.8-27B Harness - installer (Inno Setup 6)
;  Build:  installer\build_installer.bat  →  dist\QwenHarness-Setup-<verze>.exe
;
;  Language: the wizard offers English (default) and Czech; the
;  selected language is written to {app}\runtime\ui-language.txt
;  and the app starts in it (English when nothing is selected).
; ============================================================

; Verzi lze předefinovat z příkazové řádky: ISCC /DMyAppVersion=x.y.z
; (používá installer\release.bat s verzí z installer\version.txt)
#ifndef MyAppVersion
#define MyAppVersion "1.3.0"
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
; vzdy zobraz vyber jazyka (anglictina je prvni = vychozi)
ShowLanguageDialog=yes

[Languages]
; First entry = default language (English base after installation).
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "cze"; MessagesFile: "compiler:Languages\Czech.isl"

[CustomMessages]
en.SetupEnvMenu=Set up environment and models (~59 GiB)
cze.SetupEnvMenu=Instalace prostředí a modelů (59 GiB)
en.RunSetupDesc=Set up the environment and download models (~59 GiB, required to run)
cze.RunSetupDesc=Nainstalovat prostředí a stáhnout modely (~59 GiB, nutné pro provoz)

[Messages]
en.WelcomeLabel2=This wizard will install [name/ver], a local AI harness for Qwen and Ornith (RTX 5090).%n%nAfter installation, the environment and models (~59 GiB) will download automatically on first launch.%n%nContinue?
cze.WelcomeLabel2=Tento pruvodce nainstaluje [name/ver] - lokalni AI harness pro Qwen a Ornith (RTX 5090).%n%nPo instalaci se pri prvnim spusteni automaticky stahne prostredi a modely (~59 GiB).%n%nPOKRACOVAT?

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
Source: "version.txt"; DestDir: "{app}"; DestName: "version.txt"; Flags: ignoreversion
Source: "..\config.yaml"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\app_icon.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\harness\*.py"; DestDir: "{app}\harness"; Flags: ignoreversion recursesubdirs
Source: "..\harness\tools\*.py"; DestDir: "{app}\harness\tools"; Flags: ignoreversion
Source: "..\scripts\*.py"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "..\tests\*.py"; DestDir: "{app}\tests"; Flags: ignoreversion
Source: "..\memory\*.md"; DestDir: "{app}\memory"; Flags: ignoreversion onlyifdoesntexist recursesubdirs createallsubdirs
Source: "..\skills\*.md"; DestDir: "{app}\skills"; Flags: ignoreversion recursesubdirs createallsubdirs
; setup skript (venv + modely) - spouští se po instalaci
Source: "run_setup.bat"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\{cm:SetupEnvMenu}"; Filename: "{app}\run_setup.bat"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; HLAVNI KROK: vytvori venv, stahne zavislosti, llama.cpp i modely (~59 GiB)
; s prubehem v konzoli - hned po dokonceni instalatoru (default zaskrtnuto)
Filename: "{app}\run_setup.bat"; Description: "{cm:RunSetupDesc}"; Flags: postinstall shellexec runasoriginaluser; WorkingDir: "{app}"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  // uloz vybrany jazyk instalatoru -> aplikace se podle nej nastavi
  // ("en" nebo "cze"; webapp mapuje cze->cs, vychozi je anglictina)
  if CurStep = ssPostInstall then
  begin
    CreateDir(ExpandConstant('{app}\runtime'));
    SaveStringToFile(ExpandConstant('{app}\runtime\ui-language.txt'), ActiveLanguage, False);
  end;
end;
