$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
$exe = Join-Path $repo 'tunnel-client-v0.0.14-windows-amd64\tunnel-client.exe'
$profile = Join-Path $repo 'tunnel-profile.yaml'
$state = Join-Path $repo '.runtime'
if (-not (Test-Path -LiteralPath $profile)) { throw 'Missing tunnel-profile.yaml. Copy tunnel-profile.example.yaml and fill in the placeholders.' }
if (-not (Test-Path -LiteralPath (Join-Path $repo 'secrets\openai-tunnel-key.txt'))) { throw 'Missing secrets\openai-tunnel-key.txt (Runtime API key).' }
New-Item -ItemType Directory -Path $state -Force | Out-Null
$pidFile = Join-Path $state 'tunnel.pid'
if (Test-Path -LiteralPath $pidFile) {
    $prior = Get-Process -Id ([int](Get-Content -LiteralPath $pidFile)) -ErrorAction SilentlyContinue
    if ($prior -and $prior.Path -eq $exe) {
        Write-Output 'Local Coding Agent is already running.'
        & (Join-Path $repo 'status-local-coding-agent.ps1')
        exit 0
    }
}
$occupied = Get-NetTCPConnection -LocalPort 8081 -State Listen -ErrorAction SilentlyContinue
if ($occupied) { throw 'Port 8081 is occupied. Stop the previous connector before starting this project.' }
$env:MCP_WORKSPACE = $repo
$env:PYTHONUTF8 = '1'
$child = Start-Process -FilePath $exe -WorkingDirectory $repo -WindowStyle Hidden -ArgumentList @('run','--profile-file', ('"' + $profile + '"')) -RedirectStandardOutput (Join-Path $state 'tunnel.stdout.log') -RedirectStandardError (Join-Path $state 'tunnel.stderr.log') -PassThru
Set-Content -LiteralPath $pidFile -Value $child.Id -Encoding ascii
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Seconds 1
    $child.Refresh()
    if ($child.HasExited) { throw 'Tunnel exited. Inspect .runtime logs.' }
    try {
        $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8081/readyz' -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200 -and $response.Content -match 'ready') {
            Write-Output 'Local Coding Agent tunnel is ready.'
            exit 0
        }
    } catch {}
}
throw 'Tunnel started but did not become ready. Inspect .runtime logs.'
