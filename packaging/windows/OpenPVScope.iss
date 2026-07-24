# OpenPVScope Inno Setup script
# Build frontend + PyInstaller first (packaging/windows/build.bat fetches ODX), then compile this.
#
# Photogrammetry: vendor\ODX_Setup_*.exe is required (scripts/fetch_odx_setup.ps1).
# The installer chains a silent ODX install by default (Full type).
# ODX is AGPL — shipped as a separate companion installer, not embedded in the app binary.

#define MyAppName "OpenPVScope"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "OpenPVScope Contributors"
#define MyAppExeName "OpenPVScope.exe"

[Setup]
AppId={{A7C3E9F1-4B2D-4E8A-9C1F-0D5E6A8B7C2D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=Output
OutputBaseFilename=OpenPVScope-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Types]
Name: "full"; Description: "Full installation (app + ODX photogrammetry)"
Name: "compact"; Description: "App only (import GeoTIFFs; install ODX later)"
Name: "custom"; Description: "Custom"; Flags: iscustom

[Components]
Name: "main"; Description: "OpenPVScope application"; Types: full compact custom; Flags: fixed
Name: "odx"; Description: "ODX photogrammetry engine (AGPL companion installer)"; Types: full custom
Name: "dji"; Description: "DJI Thermal SDK assets (when bundled)"; Types: full compact custom

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; After PyInstaller: packaging/windows/dist/OpenPVScope/*
Source: "dist\OpenPVScope\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: main
; Optional DJI Thermal SDK
Source: "..\..\engines\dji_tsdk\*"; DestDir: "{app}\engines\dji_tsdk"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist; Components: dji
; ODX Setup companion — required for Full/odx; omit skipifsourcedoesntexist so compile fails if missing
Source: "vendor\ODX_Setup*.exe"; DestDir: "{tmp}"; Flags: ignoreversion; Components: odx
; License notice for ODX AGPL
Source: "vendor\ODX_AGPL_NOTICE.txt"; DestDir: "{app}\licenses"; Flags: ignoreversion; Components: odx

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Components: main
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Components: main

[Registry]
Root: HKLM; Subkey: "Software\Classes\.opsx"; ValueType: string; ValueName: ""; ValueData: "OpenPVScope.Project"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\OpenPVScope.Project"; ValueType: string; ValueName: ""; ValueData: "OpenPVScope Project"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\Classes\OpenPVScope.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"
Root: HKLM; Subkey: "Software\Classes\OpenPVScope.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

[Code]
function OdxSetupPath: String;
var
  FindRec: TFindRec;
begin
  Result := '';
  if FindFirst(ExpandConstant('{tmp}\ODX_Setup*.exe'), FindRec) then
  begin
    try
      Result := ExpandConstant('{tmp}\') + FindRec.Name;
    finally
      FindClose(FindRec);
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  SetupExe: String;
  ResultCode: Integer;
begin
  if (CurStep = ssPostInstall) and IsComponentSelected('odx') then
  begin
    SetupExe := OdxSetupPath;
    if SetupExe = '' then
      MsgBox('ODX_Setup was not bundled with this installer. Install ODX from https://github.com/WebODM/ODX/releases or re-run OpenPVScope Full Setup from a complete release build.', mbInformation, MB_OK)
    else
    begin
      // ODX Inno installer; PrivilegesRequired=lowest — silent install to default C:\ODX
      if not Exec(SetupExe, '/VERYSILENT /NORESTART /SUPPRESSMSGBOXES /DIR=C:\ODX', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
        MsgBox('Could not launch ODX setup. Install ODX manually from https://github.com/WebODM/ODX/releases', mbError, MB_OK)
      else if ResultCode <> 0 then
        MsgBox('ODX setup exited with code ' + IntToStr(ResultCode) + '. Install ODX from https://github.com/WebODM/ODX/releases', mbInformation, MB_OK)
      else if not FileExists('C:\ODX\run.bat') then
        MsgBox('ODX setup finished but C:\ODX\run.bat was not found. Reinstall ODX from https://github.com/WebODM/ODX/releases', mbError, MB_OK);
    end;
  end;
end;

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent; Components: main
