$ErrorActionPreference = 'SilentlyContinue'
$taskName = 'IDeS Attendance Device Connector'
Stop-ScheduledTask -TaskName $taskName
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
exit 0
