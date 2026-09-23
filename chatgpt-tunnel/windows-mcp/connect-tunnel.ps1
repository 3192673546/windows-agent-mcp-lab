# Connect Windows-MCP to ChatGPT through tunnel-client's managed runtime, then print its status.
# The runtime is given a file: reference to secrets\runtime-api-key.txt, so no extra copy of the key is written.
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
if (-not $tunnelId -or $tunnelId -notmatch '^tunnel_[0-9a-f]{32}$') { throw 'Invalid tunnel id' }
if (-not $apiKey -or $apiKey -eq 'PASTE_RUNTIME_API_KEY_HERE') { throw 'Missing runtime API key' }
$keyRef = 'file:' + ($keyFile -replace '\\', '/')
$env:CONTROL_PLANE_API_KEY = $apiKey
try {
    & $exe runtimes connect --alias windows-mcp --profile windows-mcp --profile-dir (Join-Path $Root 'tunnel-profile') --tunnel-id $tunnelId --runtime-api-key $keyRef --mcp-server-url 'http://127.0.0.1:8000/mcp/' --json
    if ($LASTEXITCODE -ne 0) { throw "tunnel-client runtimes connect failed with exit code $LASTEXITCODE" }
    & $exe runtimes status windows-mcp --json
} finally {
    Remove-Item Env:CONTROL_PLANE_API_KEY -ErrorAction SilentlyContinue
}
