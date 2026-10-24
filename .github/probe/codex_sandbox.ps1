$ErrorActionPreference = "Continue"
$root = Join-Path $env:RUNNER_TEMP "cxsb"
$work = Join-Path $root "work"
$outside = Join-Path $env:USERPROFILE "cxsb-outside"
$env:CODEX_HOME = Join-Path $root "codex-home"
New-Item -ItemType Directory -Force $work, $outside, $env:CODEX_HOME | Out-Null
Set-Content (Join-Path $work "existing.txt") "inside"
Set-Content (Join-Path $outside "secret.txt") "secret"
@"
default_permissions = "coscribe"

[permissions.coscribe.filesystem]
":minimal" = "read"

[permissions.coscribe.filesystem.":workspace_roots"]
"." = "write"

[permissions.coscribe.network]
enabled = false
"@ | Set-Content (Join-Path $env:CODEX_HOME "config.toml")
Get-Content (Join-Path $env:CODEX_HOME "config.toml")
[System.Environment]::OSVersion.Version
(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').UBR

$probe = @"
function Check(`$name, [scriptblock]`$block) { try { & `$block | Out-Null; "CHECK `$name ok" } catch { "CHECK `$name FAILED: `$(`$_.Exception.Message)" } }
Check 'read inside' { Get-Content -ErrorAction Stop existing.txt }
Check 'list inside' { Get-ChildItem -ErrorAction Stop . }
Check 'write inside' { Set-Content -ErrorAction Stop new.txt 'x' }
Check 'delete inside' { Remove-Item -ErrorAction Stop new.txt }
Check 'write outside' { Set-Content -ErrorAction Stop '$outside\probe.txt' 'x' }
Check 'read outside' { Get-Content -ErrorAction Stop '$outside\secret.txt' }
Check 'list outside' { Get-ChildItem -ErrorAction Stop '$env:USERPROFILE\Documents' }
Check 'network' { (New-Object Net.Sockets.TcpClient).Connect('1.1.1.1', 443) }
Check 'whoami' { whoami | Write-Host }
"@
Set-Content (Join-Path $root "probe.ps1") $probe

foreach ($impl in @("mxc", "unelevated", "elevated")) {
  "===== $impl powershell"
  Push-Location $work
  codex -c "windows.sandbox=$impl" sandbox --permission-profile coscribe -C $work -- powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "probe.ps1") 2>&1 | ForEach-Object { "$_" }
  "exit $LASTEXITCODE"
  "===== $impl cmd"
  codex -c "windows.sandbox=$impl" sandbox --permission-profile coscribe -C $work -- cmd.exe /d /c "dir /b & echo hi> cmdout.txt & type cmdout.txt & type $outside\secret.txt & echo x> $outside\cmd.txt" 2>&1 | ForEach-Object { "$_" }
  "exit $LASTEXITCODE"
  Pop-Location
  "outside now:"; Get-ChildItem $outside | ForEach-Object Name
  Remove-Item -ErrorAction SilentlyContinue "$outside\probe.txt", "$outside\cmd.txt"
}
"===== setup --elevated"
codex sandbox setup --help 2>&1 | Select-Object -First 20
