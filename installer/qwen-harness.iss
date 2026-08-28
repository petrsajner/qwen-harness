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
#define MyAppVersion "1.4.2"
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
en.SetupEnvMenu=Set up environment and models (up to ~59 GiB)
cze.SetupEnvMenu=Instalace prostředí a modelů (až 59 GiB)
en.BackupSetupMenu=Set up from offline backup
cze.BackupSetupMenu=Instalace z offline zálohy
en.RunSetupDesc=Set up the environment and download models (requires separately installed 64-bit Python 3.12)
cze.RunSetupDesc=Nainstalovat prostředí a modely (vyžaduje samostatně nainstalovaný 64bitový Python 3.12)

[Messages]
en.WelcomeLabel2=This wizard will install [name/ver], a local AI harness for Qwen and Ornith.%n%nREQUIRED: Install 64-bit Python 3.12 separately and enable "Add Python to PATH" before continuing. Python is not bundled.%n%nAfter installation, the environment and selected models will be prepared from an offline backup or downloaded automatically (up to ~59 GiB).%n%nContinue?
cze.WelcomeLabel2=Tento pruvodce nainstaluje [name/ver] - lokalni AI harness pro Qwen a Ornith.%n%nVYZADOVANO: Pred pokracovanim samostatne nainstalujte 64bitovy Python 3.12 a zapnete "Add Python to PATH". Python neni soucasti instalatoru.%n%nPo instalaci se prostredi a vybrane modely pripravi z offline zalohy nebo automaticky stahnou (az ~59 GiB).%n%nPOKRACOVAT?

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
Source: "..\run_cli.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "version.txt"; DestDir: "{app}"; DestName: "version.txt"; Flags: ignoreversion
Source: "..\config.yaml"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\AGENTS.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\output\pdf\QwenHarness-Manual-EN.pdf"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\output\pdf\QwenHarness-Manual-CS.pdf"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\app_icon.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\harness\*.py"; DestDir: "{app}\harness"; Flags: ignoreversion recursesubdirs
Source: "..\harness\tools\*.py"; DestDir: "{app}\harness\tools"; Flags: ignoreversion
Source: "..\scripts\*.py"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "..\tests\*.py"; DestDir: "{app}\tests"; Flags: ignoreversion
Source: "..\memory\*.md"; DestDir: "{app}\memory"; Flags: ignoreversion onlyifdoesntexist recursesubdirs createallsubdirs
Source: "..\skills\*.md"; DestDir: "{app}\skills"; Flags: ignoreversion recursesubdirs createallsubdirs
; setup skript (venv + modely) - spouští se po instalaci
Source: "run_setup.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "run_setup_from_backup.bat"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\{#MyAppName} (CLI)"; Filename: "{app}\run_cli.bat"; WorkingDir: "{app}"; IconFilename: "{app}\app_icon.ico"
Name: "{group}\{cm:SetupEnvMenu}"; Filename: "{app}\run_setup.bat"; WorkingDir: "{app}"
Name: "{group}\{cm:BackupSetupMenu}"; Filename: "{app}\run_setup_from_backup.bat"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; HLAVNI KROK: vytvori venv, stahne zavislosti, llama.cpp i modely (~59 GiB)
; s prubehem v konzoli - hned po dokonceni instalatoru (default zaskrtnuto)
Filename: "{app}\run_setup.bat"; Description: "{cm:RunSetupDesc}"; Flags: postinstall shellexec runasoriginaluser; WorkingDir: "{app}"

[Code]
var
  ModelPage: TWizardPage;
  ModelList: TNewCheckListBox;
  // naplni se v InitializeWizard (Pascal Script neumi typovane konstanty);
  // zrcadli min_vram_gb z config.yaml (nejnizsi profil kazdeho modelu)
  ModelKeys: array[0..5] of String;
  ModelNames: array[0..5] of String;
  ModelMinVram: array[0..5] of Double;

procedure FillModelTable;
begin
  ModelKeys[0] := 'q3';
  ModelKeys[1] := 'q4';
  ModelKeys[2] := 'q5';
  ModelKeys[3] := 'ornith_q5';
  ModelKeys[4] := 'nemotron_q4';
  ModelKeys[5] := 'nemotron_q5';
  ModelNames[0] := 'Qwen3.8-27B IQ3_S  (12.0 GB download)  -  16 GB GPUs (borderline)';
  ModelNames[1] := 'Qwen3.8-27B Q4_K_M  (16.5 GB download)  -  24 GB+ GPUs';
  ModelNames[2] := 'Qwen3.8-27B Q5_K_M  (19.8 GB download)  -  24 GB+ GPUs';
  ModelNames[3] := 'Ornith 1.5 35B-A3B Q5 Abliterated  (23.0 GB download)  -  32 GB GPUs';
  ModelNames[4] := 'Nemotron 3.5 Lightning 30B-A3B Q4_K_XL  (25.5 GB download)  -  24 GB+ GPUs (32 GB: up to 1M ctx)';
  ModelNames[5] := 'Nemotron 3.5 Lightning 30B-A3B Q5_K_XL  (30.4 GB download)  -  26 GB+ GPUs';
  ModelMinVram[0] := 15.0;
  ModelMinVram[1] := 23.0;
  ModelMinVram[2] := 24.0;
  ModelMinVram[3] := 30.0;
  ModelMinVram[4] := 24.0;
  ModelMinVram[5] := 26.0;
end;

function DetectVRAM: Double;
var
  ResultCode: Integer;
  TmpFile: String;
  Lines: TArrayOfString;
begin
  Result := 0;
  TmpFile := ExpandConstant('{tmp}\qwen-vram.txt');
  Exec(ExpandConstant('{cmd}'),
       Format('/c nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits > "%s"', [TmpFile]),
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if (ResultCode = 0) and LoadStringsFromFile(TmpFile, Lines) and (GetArrayLength(Lines) > 0) then
    Result := StrToIntDef(Trim(Lines[0]), 0) / 1024.0;
end;

procedure InitializeWizard;
var
  I: Integer;
  Vram: Double;
  Fits, Checked: Boolean;
  Subtitle: String;
begin
  FillModelTable;
  Vram := DetectVRAM;
  if Vram > 0 then
    Subtitle := Format('Detected GPU: %.1f GB VRAM. Only models that fit are selectable; uncheck what you do not want to download.', [Vram])
  else
    Subtitle := 'GPU VRAM could not be detected - all models are offered. Uncheck what you do not want to download.';
  ModelPage := CreateCustomPage(wpSelectDir, 'Models to download',
    'Choose which models to download on first launch. ' + Subtitle);
  ModelList := TNewCheckListBox.Create(ModelPage.Surface);
  ModelList.SetBounds(ScaleX(0), ScaleY(0), ModelPage.SurfaceWidth, ScaleY(120));
  ModelList.Parent := ModelPage.Surface;
  ModelList.ShowLines := False;
  for I := 0 to 5 do
  begin
    Fits := (Vram <= 0) or (ModelMinVram[I] <= Vram);
    Checked := Fits;  // default: stahnout vse, co se vejde (auto chovani)
    ModelList.AddCheckBox(ModelNames[I], '', 0, Checked, Fits, False, False, TObject(I));
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  I: Integer;
  AnyChecked: Boolean;
begin
  Result := True;
  if CurPageID = ModelPage.ID then
  begin
    AnyChecked := False;
    for I := 0 to ModelList.Items.Count - 1 do
      if ModelList.Checked[I] then AnyChecked := True;
    if not AnyChecked then
    begin
      MsgBox('Keep at least one model checked - the app cannot run without any model.',
             mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  I: Integer;
  Selection: String;
begin
  if CurStep = ssPostInstall then
  begin
    CreateDir(ExpandConstant('{app}\runtime'));
    // uloz vybrany jazyk instalatoru -> aplikace se podle nej nastavi
    // ("en" nebo "cze"; webapp mapuje cze->cs, vychozi je anglictina)
    SaveStringToFile(ExpandConstant('{app}\runtime\ui-language.txt'), ActiveLanguage, False);
    // uloz vyber modelu z wizardu (comma list; run_setup.bat ho preda downloadu)
    Selection := '';
    if Assigned(ModelList) then
      for I := 0 to ModelList.Items.Count - 1 do
        if ModelList.Checked[I] and ModelList.ItemEnabled[I] then
        begin
          if Selection <> '' then Selection := Selection + ',';
          Selection := Selection + ModelKeys[I];
        end;
    if Selection <> '' then
      SaveStringToFile(ExpandConstant('{app}\runtime\model-selection.txt'), Selection, False);
    // Self-contained backup folder (Setup.exe beside manifest), or sidecar backup.
    if FileExists(ExpandConstant('{src}\manifest.json')) then
      SaveStringToFile(ExpandConstant('{app}\runtime\offline-backup-path.txt'),
        ExpandConstant('{src}'), False)
    else if FileExists(ExpandConstant('{src}\QwenHarness-Offline-Backup\manifest.json')) then
      SaveStringToFile(ExpandConstant('{app}\runtime\offline-backup-path.txt'),
        ExpandConstant('{src}\QwenHarness-Offline-Backup'), False);
  end;
end;
