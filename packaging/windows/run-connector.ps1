param(
    [Parameter(Mandatory = $true)] [string]$ExecutablePath,
    [Parameter(Mandatory = $true)] [string]$ConfigPath
)

$ErrorActionPreference = 'Stop'
Get-Content -LiteralPath $ConfigPath | ForEach-Object {
    if ($_ -match '^\s*(ATTENDANCE_[A-Z0-9_]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim(), 'Process')
    } elseif ($_.Trim() -and -not $_.TrimStart().StartsWith('#')) {
        throw 'Invalid connector configuration line; expected ATTENDANCE_NAME=value.'
    }
}

& $ExecutablePath
exit $LASTEXITCODE
