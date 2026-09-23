# Report whether the Windows-MCP HTTP server is running and listening. Exit code 0 = healthy.
param([string]$Root = $PSScriptRoot)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path -LiteralPath $Root).Path
$exe = Join-Path $Root 'runtime\venv\Scripts\python.exe'
$pidFile = Join-Path $Root 'windows-mcp-http.pid'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Output 'not running'; exit 1 }
$raw = (Get-Content -LiteralPath $pidFile -Raw).Trim()
$proc = if ($raw -match '^\d+$') { Get-Process -Id ([int]$raw) -ErrorAction SilentlyContinue }
if (-not $proc -or $proc.Path -ne $exe) { Write-Output 'stale pid file'; exit 1 }
if (-not (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)) {
    Write-Output "process pid=$raw is alive but port 8000 is not listening"; exit 1
}
Write-Output "running pid=$raw endpoint=http://127.0.0.1:8000/mcp/"
exit 0
