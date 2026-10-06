param(
    [Parameter(Mandatory = $true)] [string]$ExecutablePath,
    [Parameter(Mandatory = $true)] [string]$HomePath,
    [string]$LogPath,
    [switch]$StartNow
)

$ErrorActionPreference = 'Stop'
# Leave a record of what happened (the installer runs this hidden).
if ($LogPath) { Start-Transcript -LiteralPath ($LogPath + '.register.txt') -Force | Out-Null }
$taskName = 'Ovigyan Attendance Connector'
$runnerPath = Join-Path $PSScriptRoot 'run-connector.ps1'

New-Item -ItemType Directory -Force -Path $HomePath | Out-Null

function Set-Acl-For($path, $usersRead) {
    $acl = Get-Acl -LiteralPath $path
    $acl.SetAccessRuleProtection($true, $false)
    $acl.Access | ForEach-Object { [void]$acl.RemoveAccessRule($_) }
    $inherit = if ((Get-Item -LiteralPath $path).PSIsContainer) { 'ContainerInherit,ObjectInherit' } else { 'None' }
    foreach ($sid in 'S-1-5-18', 'S-1-5-32-544') {
        $who = New-Object System.Security.Principal.SecurityIdentifier($sid)
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($who, 'FullControl', $inherit, 'None', 'Allow')))
    }
    if ($usersRead) {
        $users = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-545')
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($users, 'Read', 'Allow')))
    }
    Set-Acl -LiteralPath $path -AclObject $acl
}

# The settings folder holds the connection credential and terminal passwords: SYSTEM and administrators only.
Set-Acl-For $HomePath $false

# Two small files are meant for the people who use this PC:
#  - ui-token opens the connector's page (staff shortcut, notification icon). It is not a cloud credential:
#    it only stops web pages and other programs from using the page. Created once, kept on upgrade.
#  - status.json is what the notification icon shows. It never holds a secret.
$tokenPath = Join-Path $HomePath 'ui-token'
if (-not (Test-Path -LiteralPath $tokenPath) -or (Get-Item -LiteralPath $tokenPath).Length -lt 32) {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    [IO.File]::WriteAllText($tokenPath, $token)
}
Set-Acl-For $tokenPath $true
$statusPath = Join-Path $HomePath 'status.json'
if (-not (Test-Path -LiteralPath $statusPath)) { [IO.File]::WriteAllText($statusPath, '{}') }
Set-Acl-For $statusPath $true

$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -ExecutablePath "{1}"' -f $runnerPath, $ExecutablePath
if ($LogPath) { $arguments += ' -LogPath "{0}"' -f $LogPath }
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory (Split-Path $ExecutablePath)
$trigger = New-ScheduledTaskTrigger -AtStartup
# No time limit (the default stops a task after 72 hours), keep running on battery, restart if it crashes.
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Registered $taskName"
if ($StartNow) { Start-ScheduledTask -TaskName $taskName }
