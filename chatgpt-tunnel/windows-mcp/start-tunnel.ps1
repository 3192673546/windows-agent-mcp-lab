# Generate the tunnel-client profile for Windows-MCP (remote MCP at 127.0.0.1:8000) and run doctor.
# Reads secrets\tunnel-id.txt and secrets\runtime-api-key.txt; both stay out of version control.
param([string]$Root = $PSScriptRoot)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path -LiteralPath $Root).Path
$exe = Join-Path $Root 'tunnel-client\bin\tunnel-client.exe'
$idFile = Join-Path $Root 'secrets\tunnel-id.txt'
$keyFile = Join-Path $Root 'secrets\runtime-api-key.txt'
if (-not (Test-Path -LiteralPath $exe)) { throw "tunnel-client not found: $exe" }
if (-not (Test-Path -LiteralPath $idFile)) { throw 'Missing secrets\tunnel-id.txt' }
if (-not (Test-Path -LiteralPath $keyFile)) { throw 'Missing secrets\runtime-api-key.txt' }
$tunnelId = (Get-Content -LiteralPath $idFile -Raw).Trim()
$apiKey = (Get-Content -LiteralPath $keyFile -Raw).Trim()
if (-not $tunnelId -or $tunnelId -notmatch '^tunnel_[0-9a-f]{32}$') { throw 'Please fill secrets\tunnel-id.txt with tunnel_ + 32 hex characters' }
if (-not $apiKey -or $apiKey -eq 'PASTE_RUNTIME_API_KEY_HERE') { throw 'Please fill secrets\runtime-api-key.txt' }
$profileDir = Join-Path $Root 'tunnel-profile'
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
$env:CONTROL_PLANE_API_KEY = $apiKey
try {
    & $exe init --force --sample sample_mcp_remote_no_auth --profile windows-mcp --profile-dir $profileDir --tunnel-id $tunnelId --mcp-server-url 'http://127.0.0.1:8000/mcp/' --health-listen-addr '127.0.0.1:0'
    if ($LASTEXITCODE -ne 0) { throw "tunnel-client init failed with exit code $LASTEXITCODE" }
    & $exe doctor --profile windows-mcp --profile-dir $profileDir --explain
    if ($LASTEXITCODE -ne 0) { throw "tunnel-client doctor failed with exit code $LASTEXITCODE" }
} finally {
    Remove-Item Env:CONTROL_PLANE_API_KEY -ErrorAction SilentlyContinue
}
