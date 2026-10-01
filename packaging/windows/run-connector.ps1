param(
    [Parameter(Mandatory = $true)] [string]$ExecutablePath,
    [Parameter(Mandatory = $true)] [string]$ConfigPath,
    [string]$LogPath
)

$ErrorActionPreference = 'Stop'
Get-Content -LiteralPath $ConfigPath | ForEach-Object {
    if ($_ -match '^\s*(ATTENDANCE_[A-Z0-9_]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim(), 'Process')
    } elseif ($_.Trim() -and -not $_.TrimStart().StartsWith('#')) {
        throw 'Invalid connector configuration line; expected ATTENDANCE_NAME=value.'
    }
}

if (-not $LogPath) {
    & $ExecutablePath
    exit $LASTEXITCODE
}

# Keep one previous log (5 MB cap, checked at each start; the task restarts on failure and at boot).
if ((Test-Path -LiteralPath $LogPath) -and (Get-Item -LiteralPath $LogPath).Length -gt 5MB) {
    Move-Item -LiteralPath $LogPath -Destination "$LogPath.1" -Force
}
& $ExecutablePath 2>&1 | ForEach-Object { '{0:s} {1}' -f (Get-Date), $_ } | Out-File -LiteralPath $LogPath -Append -Encoding utf8
exit $LASTEXITCODE
