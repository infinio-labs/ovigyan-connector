param([Parameter(Mandatory = $true)] [string]$ExecutablePath)

$ErrorActionPreference = 'Stop'
Stop-ScheduledTask -TaskName 'Ovigyan Attendance Connector' -ErrorAction SilentlyContinue
Stop-ScheduledTask -TaskName 'Ovigyan Connector Icon' -ErrorAction SilentlyContinue

# Restart Manager cannot close the signed-in user's tray from a SYSTEM update (session mismatch).
# Match the installed path so a different copy of the program is left alone.
$processes = Get-CimInstance Win32_Process -Filter "Name='ovigyan-connector.exe'" |
    Where-Object { $_.ExecutablePath -ieq $ExecutablePath }
foreach ($connectorProcess in $processes) {
    $running = Get-Process -Id $connectorProcess.ProcessId -ErrorAction SilentlyContinue
    if (-not $running) { continue }
    try { Stop-Process -Id $connectorProcess.ProcessId -Force -ErrorAction Stop }
    catch {
        if (Get-Process -Id $connectorProcess.ProcessId -ErrorAction SilentlyContinue) { throw }
    }
    if (-not $running.WaitForExit(30000)) { throw "The installed connector did not stop." }
}
