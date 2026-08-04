#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

#define MyAppName "Svodika"
#define MyAppExeName "MeetingRecorder.exe"
#define ProjectRoot SourcePath + "\.."

[Setup]
AppId={{93A6E2FD-EF56-4A72-B6A0-19D32BB86D42}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Svodika
VersionInfoCompany=Svodika
VersionInfoDescription=Установщик приложения Svodika
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoCopyright=Copyright (c) Svodika
DefaultDirName={localappdata}\Programs\Svodika
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#ProjectRoot}\installer-output
OutputBaseFilename=MeetingRecorderSetup-{#MyAppVersion}
SetupIconFile={#ProjectRoot}\ui_qt\assets\meeting-recorder-logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=yes
CloseApplicationsFilter={#MyAppExeName}
MinVersion=10.0.17763
ChangesAssociations=no
ChangesEnvironment=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"
Name: "startmenuicon"; Description: "Добавить в меню «Пуск»"; GroupDescription: "Ярлыки:"

[Files]
Source: "{#ProjectRoot}\dist\MeetingRecorder\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Keep the shortcut icon outside the bundled _internal tree. A versioned path
; also prevents Explorer from reusing the icon cached for an older release.
Source: "{#ProjectRoot}\ui_qt\assets\meeting-recorder-logo.ico"; DestDir: "{app}"; DestName: "MeetingRecorder-{#MyAppVersion}.ico"; Flags: ignoreversion

[InstallDelete]
; Recreate shortcuts so upgrades cannot leave their previous icon metadata.
Type: files; Name: "{autodesktop}\{#MyAppName}.lnk"
Type: files; Name: "{group}\{#MyAppName}.lnk"
Type: files; Name: "{autodesktop}\Запись встреч.lnk"
Type: files; Name: "{userprograms}\Запись встреч\Запись встреч.lnk"
Type: dirifempty; Name: "{userprograms}\Запись встреч"
Type: files; Name: "{app}\MeetingRecorder-1.0.4.ico"
Type: files; Name: "{app}\MeetingRecorder-1.0.5.ico"
Type: files; Name: "{app}\MeetingRecorder-1.0.6.ico"
Type: files; Name: "{app}\MeetingRecorder-1.0.7.ico"
Type: files; Name: "{app}\MeetingRecorder-1.0.8.ico"
Type: files; Name: "{app}\MeetingRecorder-1.0.9.ico"
Type: files; Name: "{app}\MeetingRecorder-1.0.10.ico"
Type: files; Name: "{app}\MeetingRecorder-1.0.11.ico"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\MeetingRecorder-{#MyAppVersion}.ico"; IconIndex: 0; Tasks: startmenuicon
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"; Tasks: startmenuicon
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\MeetingRecorder-{#MyAppVersion}.ico"; IconIndex: 0; Tasks: desktopicon

[UninstallRun]
; Let Qt hide its tray icon and release recorder resources first. The taskkill
; fallback also handles older builds and an application that stopped responding.
Filename: "{app}\{#MyAppExeName}"; Parameters: "--shutdown-for-uninstall"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "GracefulStopMeetingRecorder"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM ""{#MyAppExeName}"""; Flags: runhidden waituntilterminated; RunOnceId: "ForceStopMeetingRecorder"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#MyAppExeName}"; Flags: nowait; Check: WizardSilent

[Code]
const
  SHCNE_ASSOCCHANGED = $08000000;
  SHCNF_IDLIST = $0000;

procedure SHChangeNotify(wEventId, uFlags, dwItem1, dwItem2: Integer);
  external 'SHChangeNotify@shell32.dll stdcall';

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, 0, 0);
end;
