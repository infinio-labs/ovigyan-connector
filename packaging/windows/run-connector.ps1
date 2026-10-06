param(
    [Parameter(Mandatory = $true)] [string]$ExecutablePath,
    [string]$LogPath
)

$ErrorActionPreference = 'Stop'

if (-not $LogPath) {
    & $ExecutablePath run
    exit $LASTEXITCODE
}

# Keep one previous log (5 MB cap, checked at each start; the task restarts on failure and at boot).
if ((Test-Path -LiteralPath $LogPath) -and (Get-Item -LiteralPath $LogPath).Length -gt 5MB) {
    Move-Item -LiteralPath $LogPath -Destination "$LogPath.1" -Force
}
& $ExecutablePath run 2>&1 | ForEach-Object { '{0:s} {1}' -f (Get-Date), $_ } | Out-File -LiteralPath $LogPath -Append -Encoding utf8
exit $LASTEXITCODE
