# Stop the Windows-MCP HTTP server started by start-http.ps1, including its child processes.
param([string]$Root = $PSScriptRoot)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path -LiteralPath $Root).Path
$exe = Join-Path $Root 'runtime\venv\Scripts\python.exe'
$pidFile = Join-Path $Root 'windows-mcp-http.pid'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Output 'not running'; exit 0 }
$raw = (Get-Content -LiteralPath $pidFile -Raw).Trim()
$proc = if ($raw -match '^\d+$') { Get-Process -Id ([int]$raw) -ErrorAction SilentlyContinue }
if ($proc -and $proc.Path -ne $exe) {
    # The PID was reused by an unrelated program; never kill it.
    Remove-Item -LiteralPath $pidFile -Force
    Write-Output "stale pid file removed; pid $raw now belongs to $($proc.Path), left untouched"
    exit 0
}
if ($proc) {
    & taskkill.exe /PID $proc.Id /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Unable to stop the windows-mcp process tree.' }
}
Remove-Item -LiteralPath $pidFile -Force
Write-Output 'stopped'
