# Authenticode-sign one file. Needs env WINDOWS_SIGN_CERT_BASE64 (PFX, base64) and WINDOWS_SIGN_CERT_PASSWORD.
param([Parameter(Mandatory = $true)] [string]$Path)
$ErrorActionPreference = 'Stop'
$pfx = Join-Path $env:RUNNER_TEMP 'sign.pfx'
[IO.File]::WriteAllBytes($pfx, [Convert]::FromBase64String($env:WINDOWS_SIGN_CERT_BASE64))
try {
    $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" |
        Sort-Object FullName -Descending | Select-Object -First 1
    & $signtool.FullName sign /f $pfx /p $env:WINDOWS_SIGN_CERT_PASSWORD /fd sha256 /tr http://timestamp.digicert.com /td sha256 $Path
    if ($LASTEXITCODE -ne 0) { throw "signtool failed for $Path" }
} finally {
    Remove-Item $pfx -Force -ErrorAction SilentlyContinue
}
