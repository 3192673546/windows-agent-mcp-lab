param(
    [string]$TunnelId
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$tc = Join-Path $root 'tunnel-client\bin\tunnel-client.exe'
$python = Join-Path $root '.venv\Scripts\python.exe'
$entry = Join-Path $root 'run-mcp.py'
# Launch through the venv's python.exe + run-mcp.py rather than the pip-generated
# browser-harness-mcp.exe: the .exe wrapper embeds an absolute path and breaks when the folder is moved.
# Windows PowerShell 5.1 drops embedded quotes when passing arguments to native programs,
# so the install path must not contain spaces.
$mcpForTunnel = '{0} {1}' -f ($python -replace '\\','/'), ($entry -replace '\\','/')
if ($mcpForTunnel.Split(' ').Count -ne 2) { throw "Install path must not contain spaces: $root" }
$profileDir = Join-Path $root 'tunnel-profile'
$profile = 'browser-harness-chatgpt'

if (-not (Test-Path $tc)) { throw "tunnel-client not found: $tc" }
if (-not (Test-Path $python)) { throw "browser-harness venv not found: $python" }
if (-not (Test-Path $entry)) { throw "run-mcp.py not found: $entry" }

if (-not $TunnelId) {
    Write-Host ''
    Write-Host 'Paste your OpenAI Tunnel ID.' -ForegroundColor Cyan
    Write-Host 'Format: tunnel_ followed by 32 lowercase hex characters.'
    $TunnelId = (Read-Host 'CONTROL_PLANE_TUNNEL_ID').Trim()
}

if ($TunnelId -notmatch '^tunnel_[0-9a-f]{32}$') {
    throw 'Invalid Tunnel ID. Expected: tunnel_ + 32 lowercase hexadecimal characters.'
}

New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
& $tc init `
    --sample sample_mcp_stdio_local `
    --profile $profile `
    --profile-dir $profileDir `
    --tunnel-id $TunnelId `
    --mcp-command $mcpForTunnel `
    --health-listen-addr '127.0.0.1:8765' `
    --force
if ($LASTEXITCODE -ne 0) { throw "tunnel-client init failed with exit code $LASTEXITCODE" }

Set-Content -Encoding ASCII -Path (Join-Path $root 'tunnel-id.txt') -Value $TunnelId
Write-Host ''
Write-Host 'Configuration saved.' -ForegroundColor Green
Write-Host "Profile: $profile"
Write-Host "Profile directory: $profileDir"
Write-Host ''
Write-Host 'Next: double-click START-CHATGPT-BROWSER.cmd'
Write-Host 'It will ask for CONTROL_PLANE_API_KEY at runtime; the key is NOT stored on disk.'
