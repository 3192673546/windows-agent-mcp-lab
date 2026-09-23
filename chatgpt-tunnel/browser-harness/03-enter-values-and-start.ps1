$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
Write-Host '=== ChatGPT Browser Harness Tunnel Setup ===' -ForegroundColor Cyan
Write-Host ''
Write-Host 'Step 1: Paste CONTROL_PLANE_TUNNEL_ID, then press Enter.' -ForegroundColor Yellow
& '.\01-configure-tunnel.ps1'
if ($LASTEXITCODE -ne 0) { Write-Host 'Tunnel ID configuration failed.' -ForegroundColor Red; Read-Host 'Press Enter to close'; exit 1 }
Write-Host ''
Write-Host 'Step 2: Paste CONTROL_PLANE_API_KEY when prompted.' -ForegroundColor Yellow
Write-Host 'The key is used only in this process and is not written to disk.' -ForegroundColor DarkGray
& '.\02-start-chatgpt-tunnel.ps1'
