$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$tc = Join-Path $root 'tunnel-client\bin\tunnel-client.exe'
$profileDir = Join-Path $root 'tunnel-profile'
$profile = 'browser-harness-chatgpt'
$profileFile = Join-Path $profileDir ($profile + '.yaml')

if (-not (Test-Path $profileFile)) {
    Write-Host 'Tunnel profile has not been configured yet.' -ForegroundColor Yellow
    Write-Host 'Run CONFIGURE-CHATGPT-TUNNEL.cmd first.'
    Read-Host 'Press Enter to close'
    exit 2
}

$env:BH_HOME = Join-Path $root 'state'
$env:BH_RECORD = '0'
$env:TUNNEL_CLIENT_PROFILE_DIR = $profileDir
$env:TUNNEL_CLIENT_PROFILE = $profile

Write-Host ''
Write-Host 'Paste your OpenAI Runtime API key for the Tunnel.' -ForegroundColor Cyan
Write-Host 'It is used only by tunnel-client and will not be saved to disk.'
$secure = Read-Host 'CONTROL_PLANE_API_KEY' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    if ([string]::IsNullOrWhiteSpace($plain)) { throw 'Runtime API key cannot be empty.' }
    $env:CONTROL_PLANE_API_KEY = $plain
} finally {
    if ($ptr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
    Remove-Variable secure -ErrorAction SilentlyContinue
    Remove-Variable plain -ErrorAction SilentlyContinue
}

Write-Host ''
Write-Host 'Checking tunnel configuration...' -ForegroundColor Cyan
& $tc doctor --profile $profile --profile-dir $profileDir --explain
if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host 'Doctor failed. Check Tunnel ID, Runtime API key, and Tunnel permissions (Read + Use).' -ForegroundColor Red
    Remove-Item Env:CONTROL_PLANE_API_KEY -ErrorAction SilentlyContinue
    Read-Host 'Press Enter to close'
    exit $LASTEXITCODE
}

Write-Host ''
Write-Host 'Starting ChatGPT <-> browser-harness tunnel...' -ForegroundColor Green
Write-Host 'Keep this window open while ChatGPT is using the browser.'
Write-Host 'Local status UI: http://127.0.0.1:8765/ui'
Write-Host ''
& $tc run --profile $profile --profile-dir $profileDir
$code = $LASTEXITCODE
Remove-Item Env:CONTROL_PLANE_API_KEY -ErrorAction SilentlyContinue
exit $code
