; Ovigyan Connector - Windows installer (Inno Setup 6).
; Build:  iscc /DAppVersion=1.2.3 packaging\windows\installer.iss     (needs dist\ovigyan-connector.exe)
; Silent: setup.exe /VERYSILENT /DEVICE_HOST=192.168.1.50 /MACHINE_ID=NFZ824090078 ^
;                   /INGEST_URL=https://school.example.com/api/attendance/device-events /TOKEN=ovigyan_dev_...
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Ovigyan Connector"
#define TaskName "Ovigyan Attendance Connector"

[Setup]
AppId={{B7E6C2A4-5C1B-4B0E-9E3A-1D5A7C6F2A10}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Ovigyan
DefaultDirName={autopf}\Ovigyan Connector
DisableProgramGroupPage=yes
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
Source: "..\..\dist\ovigyan-connector.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "run-connector.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "register-task.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "unregister-task.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{commonappdata}\Ovigyan Connector"

[UninstallRun]
Filename: "cmd.exe"; Parameters: "/C set PSModulePath=& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\unregister-task.ps1"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveTask"

[Code]
var
  SettingsPage: TInputQueryWizardPage;

function ConfigPath: string;
begin
  Result := ExpandConstant('{commonappdata}\Ovigyan Connector\connector.env');
end;

{ /NAME=value from the command line (silent roll-outs), or '' }
function CmdValue(const Name: string): string;
var
  I: Integer;
  Prefix: string;
begin
  Result := '';
  Prefix := '/' + Name + '=';
  for I := 1 to ParamCount do
    if CompareText(Copy(ParamStr(I), 1, Length(Prefix)), Prefix) = 0 then
    begin
      Result := Copy(ParamStr(I), Length(Prefix) + 1, MaxInt);
      Exit;
    end;
end;

{ Existing value from a previous install, so an upgrade does not ask again. }
function ExistingValue(const Key: string): string;
var
  Lines: TArrayOfString;
  I: Integer;
  Prefix: string;
begin
  Result := '';
  Prefix := Key + '=';
  if LoadStringsFromFile(ConfigPath, Lines) then
    for I := 0 to GetArrayLength(Lines) - 1 do
      if Copy(Lines[I], 1, Length(Prefix)) = Prefix then
      begin
        Result := Trim(Copy(Lines[I], Length(Prefix) + 1, MaxInt));
        Exit;
      end;
end;

function Pick(const CmdName, Key: string): string;
begin
  Result := CmdValue(CmdName);
  if Result = '' then Result := ExistingValue(Key);
end;

procedure InitializeWizard;
begin
  SettingsPage := CreateInputQueryPage(wpSelectDir, 'Terminal and Ovigyan settings',
    'Connect this PC to the attendance terminal and to Ovigyan.',
    'Find the Machine ID and token in Ovigyan under Academics > Attendance > Setup > Access Devices.');
  SettingsPage.Add('Terminal IP address:', False);
  SettingsPage.Add('Machine ID (terminal serial number, as entered in Ovigyan):', False);
  SettingsPage.Add('Ovigyan ingest URL (https://.../api/attendance/device-events):', False);
  SettingsPage.Add('Connector token (ovigyan_dev_...):', True);
  SettingsPage.Values[0] := Pick('DEVICE_HOST', 'ATTENDANCE_DEVICE_HOST');
  SettingsPage.Values[1] := Pick('MACHINE_ID', 'ATTENDANCE_MACHINE_ID');
  SettingsPage.Values[2] := Pick('INGEST_URL', 'ATTENDANCE_INGEST_URL');
  SettingsPage.Values[3] := Pick('TOKEN', 'ATTENDANCE_DEVICE_TOKEN');
end;

function SettingsError: string;
var
  Url: string;
begin
  Result := '';
  Url := Trim(SettingsPage.Values[2]);
  if (Trim(SettingsPage.Values[0]) = '') or (Trim(SettingsPage.Values[1]) = '') then
    Result := 'Terminal IP address and Machine ID are required.'
  else if Copy(Url, 1, 8) <> 'https://' then
    Result := 'The Ovigyan ingest URL must start with https://'
  else if Copy(Trim(SettingsPage.Values[3]), 1, 12) <> 'ovigyan_dev_' then
    Result := 'The connector token must start with ovigyan_dev_ (issue one in Ovigyan).'
  else if (Pos(#13, Url) > 0) or (Pos(#10, Url) > 0) then
    Result := 'The ingest URL must be a single line.';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Msg: string;
begin
  Result := True;
  { Silent installs are validated in PrepareToInstall; a message box here would block them forever. }
  if (CurPageID = SettingsPage.ID) and not WizardSilent then
  begin
    Msg := SettingsError;
    if Msg <> '' then
    begin
      MsgBox(Msg, mbError, MB_OK);
      Result := False;
    end;
  end;
end;

{ Also validates silent installs, where no wizard page is shown. }
function PrepareToInstall(var NeedsRestart: Boolean): string;
var
  Code: Integer;
begin
  Result := SettingsError;
  if Result = '' then
    Exec('schtasks.exe', '/End /TN "{#TaskName}"', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Text: string;
  Code: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    Text :=
      'ATTENDANCE_DEVICE_HOST=' + Trim(SettingsPage.Values[0]) + #13#10 +
      'ATTENDANCE_MACHINE_ID=' + Trim(SettingsPage.Values[1]) + #13#10 +
      'ATTENDANCE_INGEST_URL=' + Trim(SettingsPage.Values[2]) + #13#10 +
      'ATTENDANCE_DEVICE_TOKEN=' + Trim(SettingsPage.Values[3]) + #13#10 +
      'ATTENDANCE_OUTBOX_PATH=' + ExpandConstant('{commonappdata}\Ovigyan Connector\outbox.db') + #13#10;
    { register-task.ps1 restricts this file to SYSTEM and Administrators right after this. }
    if not SaveStringToFile(ConfigPath, Text, False) then
      RaiseException('Could not write ' + ConfigPath);
    { Clear PSModulePath: a PowerShell 7 parent leaks its module path and breaks Windows PowerShell's Get-Acl. }
    if not Exec('cmd.exe',
      '/C set PSModulePath=& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\register-task.ps1') + '"' +
      ' -ExecutablePath "' + ExpandConstant('{app}\ovigyan-connector.exe') + '"' +
      ' -ConfigPath "' + ConfigPath + '"' +
      ' -LogPath "' + ExpandConstant('{commonappdata}\Ovigyan Connector\connector.log') + '" -StartNow',
      '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
      RaiseException('Registering the background task failed (exit code ' + IntToStr(Code) + '). See the setup log.');
  end;
end;
