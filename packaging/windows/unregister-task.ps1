$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'stop-connector.ps1') -ExecutablePath (Join-Path $PSScriptRoot 'ovigyan-connector.exe')
$ErrorActionPreference = 'SilentlyContinue'
$taskName = 'Ovigyan Attendance Connector'
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
$iconTaskName = 'Ovigyan Connector Icon'
Unregister-ScheduledTask -TaskName $iconTaskName -Confirm:$false
exit 0
