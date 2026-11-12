; Build with tools/build_installer.py; version is supplied from the source.
#ifndef AppVersion
  #error AppVersion must be supplied by the build script
#endif
#define AppSource "..\..\dist\Redux"

[Setup]
AppId={{B85D541B-430B-4F95-9389-D43152236C5A}
AppName=Redux
AppVersion={#AppVersion}
AppPublisher=Redux
AppPublisherURL=https://github.com/baysidejr/redux
AppSupportURL=https://github.com/baysidejr/redux/issues
DefaultDirName={localappdata}\Programs\Redux
DefaultGroupName=Redux
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir=..\..\dist
OutputBaseFilename=ReduxSetup-{#AppVersion}-win-x64
SetupIconFile=..\..\redux\assets\redux.ico
UninstallDisplayIcon={app}\Redux.exe
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
CloseApplications=no
; The bundled Redux Controller receiver holds this mutex while it runs (elevated tray app);
; Setup asks the user to exit it from the tray instead of failing on its locked files.
AppMutex=Local\ReduxControllerReceiver
RestartApplications=no
UninstallDisplayName=Redux
VersionInfoVersion={#AppVersion}.0
SetupLogging=yes

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked
Name: startup; Description: "Open Redux fullscreen when I sign in"; Flags: unchecked

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "redux-launch.json,redux-launch-diagnostic.json,.redux-diagnose-next-launch"

[Dirs]
Name: "{app}\bios"; Flags: uninsneveruninstall
Name: "{app}\library"; Flags: uninsneveruninstall

[Icons]
Name: "{group}\Redux"; Filename: "{app}\Redux.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Redux"; Filename: "{app}\Redux.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ReduxMediaLibrary"; ValueData: """{app}\Redux.exe"" --startup --fullscreen"; Tasks: startup

[Run]
Filename: "{app}\Redux.exe"; Description: "Open Redux"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Command: String;
begin
  if CurUninstallStep = usUninstall then
    if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'ReduxMediaLibrary', Command) then
      if (Command = '"' + ExpandConstant('{app}\Redux.exe') + '" --startup --fullscreen') or
         (Command = ExpandConstant('{app}\Redux.exe') + ' --startup --fullscreen') then
        RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'ReduxMediaLibrary');
end;
