param(
    [Parameter(Mandatory = $true)] [string]$ExecutablePath,
    [Parameter(Mandatory = $true)] [string]$ConfigPath
)

$ErrorActionPreference = 'Stop'
$taskName = 'IDeS Attendance Device Connector'
$runnerPath = Join-Path $PSScriptRoot 'run-connector.ps1'
$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -ExecutablePath "{1}" -ConfigPath "{2}"' -f `
    $runnerPath, $ExecutablePath, $ConfigPath
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory (Split-Path $ExecutablePath)
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Configuration file does not exist: $ConfigPath"
}

# Keep connector credentials out of machine-wide environment variables. Only
# SYSTEM and local administrators should be able to read the service config.
$acl = Get-Acl -LiteralPath $ConfigPath
$acl.SetAccessRuleProtection($true, $false)
$acl.Access | ForEach-Object { [void]$acl.RemoveAccessRule($_) }
$systemSid = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')
$adminSid = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-544')
$systemRule = New-Object System.Security.AccessControl.FileSystemAccessRule($systemSid, 'FullControl', 'Allow')
$adminRule = New-Object System.Security.AccessControl.FileSystemAccessRule($adminSid, 'FullControl', 'Allow')
$acl.AddAccessRule($systemRule)
$acl.AddAccessRule($adminRule)
Set-Acl -LiteralPath $ConfigPath -AclObject $acl

# Remove matching values left by previous versions of this setup script.
Get-Content -LiteralPath $ConfigPath | ForEach-Object {
    if ($_ -match '^\s*(ATTENDANCE_[A-Z0-9_]+)=(.*)$') {
        $name = $matches[1]
        $value = $matches[2].Trim()
        if ([Environment]::GetEnvironmentVariable($name, 'Machine') -ceq $value) {
            [Environment]::SetEnvironmentVariable($name, $null, 'Machine')
        }
    }
}

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Registered $taskName"
