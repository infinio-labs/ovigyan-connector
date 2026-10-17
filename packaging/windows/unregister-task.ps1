$ErrorActionPreference = 'SilentlyContinue'
$taskName = 'Ovigyan Attendance Connector'
Stop-ScheduledTask -TaskName $taskName
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
$iconTaskName = 'Ovigyan Connector Icon'
Stop-ScheduledTask -TaskName $iconTaskName
Unregister-ScheduledTask -TaskName $iconTaskName -Confirm:$false
exit 0
