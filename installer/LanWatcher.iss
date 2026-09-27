; Inno Setup script for LAN Watcher (Windows 10/11 x64)
; Build:  iscc installer/LanWatcher.iss   (after installer/build.sh produces dist/LANWatcher.exe)
; Output: installer/out/LANWatcher-Setup-1.0.0.exe

#define AppName "LAN Watcher"
#define AppVersion "1.0.0"
#define AppPublisher "Mahdi"
#define AppExeName "LANWatcher.exe"

[Setup]
AppId={{7F3B5C21-9E14-4C86-A6D0-LANWATCHER001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\LAN Watcher
DefaultGroupName={#AppName}
OutputDir=out
OutputBaseFilename=LANWatcher-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\{#AppExeName}
WizardStyle=modern
LicenseFile=..\LICENSE
SetupIconFile=lanwatcher.ico

[Files]
Source: "..\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; user data (database, settings, logs) lives in %LOCALAPPDATA%\LAN Watcher and is
; intentionally NOT deleted automatically - users remove it via Settings -> Tools.
Type: files; Name: "{app}\*.log"
