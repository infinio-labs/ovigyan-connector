$ErrorActionPreference = 'SilentlyContinue'
$taskName = 'Ovigyan Attendance Connector'
Stop-ScheduledTask -TaskName $taskName
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
exit 0
