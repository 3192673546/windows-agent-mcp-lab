# Start Windows-MCP as a local Streamable HTTP server on 127.0.0.1:8000.
# Place this script in the Windows-MCP install root (see ../README.md), or pass -Root.
param([string]$Root = $PSScriptRoot)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path -LiteralPath $Root).Path
# Launch through the venv's python.exe: the uv-generated windows-mcp.exe wrapper embeds an
# absolute path and stops working once the install folder is moved.
$exe = Join-Path $Root 'runtime\venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw "Windows-MCP venv not found: $exe" }
$logs = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$pidFile = Join-Path $Root 'windows-mcp-http.pid'
if (Test-Path -LiteralPath $pidFile) {
    $raw = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    $prior = if ($raw -match '^\d+$') { Get-Process -Id ([int]$raw) -ErrorAction SilentlyContinue }
    if ($prior -and $prior.Path -eq $exe) { Write-Output "already running pid=$($prior.Id)"; exit 0 }
    Remove-Item -LiteralPath $pidFile -Force
}
if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 8000 is occupied by another process. Stop it before starting Windows-MCP.'
}
$env:ANONYMIZED_TELEMETRY = 'false'
$out = Join-Path $logs 'http.out.log'
$err = Join-Path $logs 'http.err.log'
$p = Start-Process -FilePath $exe -ArgumentList @('-m','windows_mcp','serve','--transport','streamable-http','--host','127.0.0.1','--port','8000') -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
Set-Content -LiteralPath $pidFile -Value $p.Id -Encoding ascii
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Seconds 1
    if ($p.HasExited) { Remove-Item -LiteralPath $pidFile -Force; throw "windows-mcp exited with code $($p.ExitCode); see $err" }
    if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
        Write-Output "running pid=$($p.Id) endpoint=http://127.0.0.1:8000/mcp/"
        exit 0
    }
}
throw "windows-mcp started (pid=$($p.Id)) but port 8000 is not listening yet; see $err"
