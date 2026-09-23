$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot '.runtime\tunnel.pid'
$exe = Join-Path $PSScriptRoot 'tunnel-client-v0.0.14-windows-amd64\tunnel-client.exe'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Output 'No tracked tunnel process.'; exit 0 }
$targetId = [int](Get-Content -LiteralPath $pidFile)
$proc = Get-Process -Id $targetId -ErrorAction SilentlyContinue
if ($proc) {
    if ($proc.Path -ne $exe) { throw 'PID now belongs to another executable; refusing to stop it.' }
    & taskkill.exe /PID $targetId /T /F
    if ($LASTEXITCODE -ne 0) { throw 'Unable to stop tunnel process tree.' }
}
Remove-Item -LiteralPath $pidFile -Force
Write-Output 'Local Coding Agent stopped.'
