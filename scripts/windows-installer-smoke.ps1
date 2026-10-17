param([Parameter(Mandatory = $true)] [string]$SetupPath)

$ErrorActionPreference = 'Stop'
$setup = (Resolve-Path -LiteralPath $SetupPath).Path
$home_ = "$env:ProgramData\Ovigyan Connector"
$task = 'Ovigyan Attendance Connector'
$exe = "$env:ProgramFiles\Ovigyan Connector\ovigyan-connector.exe"
function Run-Exe($file, $arguments, $label) {
  Write-Host "== $label"
  $p = Start-Process $file -PassThru -ArgumentList $arguments
  if (-not $p.WaitForExit(120000)) { $p.Kill(); throw "$label timed out" }
  Write-Host "== $label exit code $($p.ExitCode)"
  return $p.ExitCode
}
# Installing asks nothing: no terminal address, no token.
$okCode = Run-Exe $setup ('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/LOG=install.log') 'install'
if ($okCode -ne 0) { Get-Content install.log -Tail 60; throw "install failed: $okCode" }
$t = Get-ScheduledTask -TaskName $task
if ($t.Settings.ExecutionTimeLimit -ne 'PT0S') { throw "task has a time limit: $($t.Settings.ExecutionTimeLimit)" }
# The icon has its own on-demand task for the Users group (setup and automatic updates start it from there).
$icon = Get-ScheduledTask -TaskName 'Ovigyan Connector Icon'
if ($icon.Principal.GroupId -notmatch 'Users|S-1-5-32-545') { throw "icon task is not for the Users group: $($icon.Principal.GroupId)" }
if ($icon.Triggers.Count -ne 0) { throw 'the icon task must have no trigger of its own (the Startup shortcut starts it at sign-in)' }
# The settings folder is for SYSTEM and administrators only.
$who = (Get-Acl $home_).Access | ForEach-Object { $_.IdentityReference.Value }
$extra = $who | Where-Object { $_ -notmatch 'SYSTEM|Administrators' }
if ($extra) { throw "settings folder readable by: $extra" }
# The background task is running and its page answers (401 without the secret link).
$deadline = (Get-Date).AddSeconds(90)
$answer = $null
while (-not $answer -and (Get-Date) -lt $deadline) {
  try { $answer = Invoke-WebRequest 'http://127.0.0.1:47890/' -SkipHttpErrorCheck -TimeoutSec 5 } catch { Start-Sleep 2 }
}
if (-not $answer) { Get-Content "$home_\connector.log" -ErrorAction SilentlyContinue; throw 'the connector page never came up' }
if ($answer.StatusCode -ne 401) { throw "page answered $($answer.StatusCode) without the secret" }
# With the secret link the page loads (the same link the Start-menu shortcut uses).
$token = (Get-Content "$home_\ui-token" -Raw).Trim()
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$page = Invoke-WebRequest "http://127.0.0.1:47890/?t=$token" -WebSession $session -TimeoutSec 10
if ($page.Content -notmatch 'Ovigyan Connector') { throw 'the page did not load' }
# Its status note says it has not been paired yet, and standard users can read that note but not the settings.
$note = Get-Content "$home_\status.json" -Raw | ConvertFrom-Json
if ($note.connector -ne 'unpaired') { throw "unexpected connector state: $($note.connector)" }
$statusAcl = (Get-Acl "$home_\status.json").Access | ForEach-Object { $_.IdentityReference.Value }
if (-not ($statusAcl -match 'Users')) { throw 'status.json is not readable by users' }
# The command line sees the same state.
$out = (& $exe status) -join "`n"
if ($out -notmatch 'Not paired') { throw "status said: $out" }
if ($out -match 'connector 0\.0\.0') { throw "the executable does not know its version: $out" }
# Automatic update: fetch a signed release, check it, install it as a separate SYSTEM task.
$version = ([regex]::Match(((& $exe status) -join "`n"), 'connector (\d+\.\d+\.\d+)')).Groups[1].Value
if (-not $version) { throw 'could not read the connector version' }
$stage = Join-Path $env:RUNNER_TEMP 'release-files'
New-Item -ItemType Directory -Force $stage | Out-Null
Copy-Item $exe "$stage\ovigyan-connector.exe"
Copy-Item $setup "$stage\ovigyan-connector-setup-$version.exe"
function Start-Release([string[]]$extra, [int]$port) {
  $log = Join-Path $env:RUNNER_TEMP "release-$port.txt"
  Remove-Item $log -ErrorAction SilentlyContinue
  $args_ = @('tests\support\serve_release.py', '--version', $version, '--port', "$port",
    '--file', "$stage\ovigyan-connector.exe=ovigyan-connector.exe",
    '--file', "$stage\ovigyan-connector-setup-$version.exe=ovigyan-connector-setup-$version.exe") + $extra
  $p = Start-Process python -PassThru -NoNewWindow -RedirectStandardOutput $log -ArgumentList $args_
  $deadline = (Get-Date).AddSeconds(30)
  while ((Get-Date) -lt $deadline -and -not ((Test-Path $log) -and ((Get-Content $log -Raw) -match 'ready'))) { Start-Sleep -Milliseconds 300 }
  if (-not ((Get-Content $log -Raw) -match 'ready')) { throw 'the test release host did not start' }
  return @{ Process = $p; Key = (Get-Content $log)[0].Trim() }
}
function Connector-Started {
  (Get-CimInstance Win32_Process -Filter "Name='ovigyan-connector.exe'" | Where-Object { $_.CommandLine -match ' run' } |
    Measure-Object -Property CreationDate -Maximum).Maximum
}
$env:OVIGYAN_ALLOW_INSECURE_HTTP = 'true'
# 1. A release signed by the wrong key is refused, and no installer runs.
$bad = Start-Release @('--wrong-key') 8765
$env:OVIGYAN_UPDATE_BASE_URL = 'http://127.0.0.1:8765'; $env:OVIGYAN_UPDATE_PUBLIC_KEY = $bad.Key
$refused = (& $exe update --target $version 2>&1) -join "`n"
$refusedCode = $LASTEXITCODE
Stop-Process -Id $bad.Process.Id -Force
if ($refusedCode -eq 0 -or $refused -notmatch 'signature') { throw "a wrongly signed release was not refused (exit $refusedCode): $refused" }
if (Get-ScheduledTask -TaskName 'Ovigyan Connector Update' -ErrorAction SilentlyContinue) { throw 'an installer was started for a bad release' }
# 2. A correctly signed release installs itself: the background task restarts on the new files.
$before = Connector-Started
$good = Start-Release @() 8766
$env:OVIGYAN_UPDATE_BASE_URL = 'http://127.0.0.1:8766'; $env:OVIGYAN_UPDATE_PUBLIC_KEY = $good.Key
$said = (& $exe update --target $version 2>&1) -join "`n"
$code = $LASTEXITCODE
Write-Host $said
Remove-Item Env:OVIGYAN_UPDATE_BASE_URL, Env:OVIGYAN_UPDATE_PUBLIC_KEY, Env:OVIGYAN_ALLOW_INSECURE_HTTP
if ($code -ne 0) { Stop-Process -Id $good.Process.Id -Force; throw "update failed: $said" }
$deadline = (Get-Date).AddSeconds(240)
$restarted = $false
while ((Get-Date) -lt $deadline -and -not $restarted) {
  Start-Sleep 3
  $gone = -not (Get-ScheduledTask -TaskName 'Ovigyan Connector Update' -ErrorAction SilentlyContinue)
  $now = Connector-Started
  $restarted = $gone -and $now -and ($now -gt $before) -and ((Get-ScheduledTask -TaskName $task).State -eq 'Running')
}
Stop-Process -Id $good.Process.Id -Force
if (-not $restarted) { Get-Content "$home_\update.json" -ErrorAction SilentlyContinue; throw 'the update did not finish and restart the connector' }
$answer = $null
$deadline = (Get-Date).AddSeconds(60)
while (-not $answer -and (Get-Date) -lt $deadline) { try { $answer = Invoke-WebRequest 'http://127.0.0.1:47890/' -SkipHttpErrorCheck -TimeoutSec 5 } catch { Start-Sleep 2 } }
if (-not $answer -or $answer.StatusCode -ne 401) { throw 'the connector page did not come back after the update' }
if ((Get-Content "$home_\update.json" -Raw | ConvertFrom-Json).status -ne 'applying') { throw 'update.json does not record the update' }
# Upgrade-in-place keeps working: run the installer again.
$again = Run-Exe $setup ('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/LOG=upgrade.log') 'reinstall'
if ($again -ne 0) { Get-Content upgrade.log -Tail 60; throw "reinstall failed: $again" }
$unCode = Run-Exe "$env:ProgramFiles\Ovigyan Connector\unins000.exe" ('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') 'uninstall'
if (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue) { throw 'task not removed on uninstall' }
if (Get-ScheduledTask -TaskName 'Ovigyan Connector Icon' -ErrorAction SilentlyContinue) { throw 'icon task not removed on uninstall' }
if (-not (Test-Path "$home_\ui-token")) { throw 'uninstall removed the settings folder (it must keep them)' }
