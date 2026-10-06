; Ovigyan Connector - Windows installer (Inno Setup 6).
; Build:  iscc /DAppVersion=1.2.3 packaging\windows\installer.iss     (needs dist\ovigyan-connector.exe)
; Install asks nothing: it sets the connector up as a background task and opens its page, where staff enter the
; Ovigyan address and connection key and add terminals. Silent: setup.exe /VERYSILENT
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Ovigyan Connector"
#define TaskName "Ovigyan Attendance Connector"
#define Exe "ovigyan-connector.exe"

[Setup]
AppId={{B7E6C2A4-5C1B-4B0E-9E3A-1D5A7C6F2A10}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Ovigyan
DefaultDirName={autopf}\Ovigyan Connector
DefaultGroupName={#AppName}
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist
OutputBaseFilename=ovigyan-connector-setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
SetupLogging=yes
UninstallDisplayName={#AppName}

[Files]
Source: "..\..\dist\{#Exe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "run-connector.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "register-task.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "unregister-task.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{commonappdata}\Ovigyan Connector"

[Icons]
; The one thing staff need: open the connector's page.
Name: "{group}\{#AppName}"; Filename: "powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -Command ""Start-Process -WindowStyle Hidden -FilePath '{app}\ovigyan-connector.exe' -ArgumentList 'open'"""; IconFilename: "{app}\{#Exe}"
Name: "{commondesktop}\{#AppName}"; Filename: "powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -Command ""Start-Process -WindowStyle Hidden -FilePath '{app}\ovigyan-connector.exe' -ArgumentList 'open'"""; IconFilename: "{app}\{#Exe}"
; The notification-area icon, for whoever signs in to this PC. (Only when this build includes it.)
Name: "{commonstartup}\{#AppName} status icon"; Filename: "powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -Command ""Start-Process -WindowStyle Hidden -FilePath '{app}\ovigyan-connector.exe' -ArgumentList 'tray'"""; IconFilename: "{app}\{#Exe}"

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -Command ""Start-Process -WindowStyle Hidden -FilePath '{app}\ovigyan-connector.exe' -ArgumentList 'open'"""; Description: "Open {#AppName} now"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "cmd.exe"; Parameters: "/C set PSModulePath=& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\unregister-task.ps1"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveTask"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): string;
var
  Code: Integer;
begin
  { An upgrade replaces the executable the task is running. }
  Exec('schtasks.exe', '/End /TN "{#TaskName}"', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
  Home: string;
begin
  if CurStep = ssPostInstall then
  begin
    Home := ExpandConstant('{commonappdata}\Ovigyan Connector');
    { Clear PSModulePath: a PowerShell 7 parent leaks its module path and breaks Windows PowerShell's Get-Acl. }
    if not Exec('cmd.exe',
      '/C set PSModulePath=& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\register-task.ps1') + '"' +
      ' -ExecutablePath "' + ExpandConstant('{app}\{#Exe}') + '"' +
      ' -HomePath "' + Home + '"' +
      ' -LogPath "' + Home + '\connector.log" -StartNow',
      '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
      RaiseException('Registering the background task failed (exit code ' + IntToStr(Code) + '). See the setup log.');
  end;
end;
