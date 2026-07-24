# OpenPVScope Inno Setup script
# Build frontend + PyInstaller first (packaging/windows/build.bat), then compile this.
#
# ODX is not bundled. The app detects an existing install or downloads/installs ODX
# on demand from the Photogrammetry UI (AGPL companion).

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

[Components]
Name: "main"; Description: "OpenPVScope application"; Flags: fixed
Name: "dji"; Description: "DJI Thermal SDK assets (when bundled)"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; After PyInstaller: packaging/windows/dist/OpenPVScope/*
Source: "dist\OpenPVScope\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: main
; Optional DJI Thermal SDK
Source: "..\..\engines\dji_tsdk\*"; DestDir: "{app}\engines\dji_tsdk"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist; Components: dji
; AGPL notice for ODX (installed on demand from the app)
Source: "vendor\ODX_AGPL_NOTICE.txt"; DestDir: "{app}\licenses"; Flags: ignoreversion; Components: main

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Components: main
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Components: main

[Registry]
Root: HKLM; Subkey: "Software\Classes\.opsx"; ValueType: string; ValueName: ""; ValueData: "OpenPVScope.Project"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Classes\OpenPVScope.Project"; ValueType: string; ValueName: ""; ValueData: "OpenPVScope Project"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\Classes\OpenPVScope.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"
Root: HKLM; Subkey: "Software\Classes\OpenPVScope.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent; Components: main
