$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot '.runtime\tunnel.pid'
$exe = Join-Path $PSScriptRoot 'tunnel-client-v0.0.14-windows-amd64\tunnel-client.exe'
$running = $false
if (Test-Path -LiteralPath $pidFile) {
    $proc = Get-Process -Id ([int](Get-Content -LiteralPath $pidFile)) -ErrorAction SilentlyContinue
    $running = [bool]($proc -and $proc.Path -eq $exe)
}
$ready = $false
if ($running) {
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8081/readyz' -UseBasicParsing -TimeoutSec 3
        $ready = $r.StatusCode -eq 200 -and $r.Content -match 'ready'
    } catch {}
}
[pscustomobject]@{Project=$PSScriptRoot; Running=$running; TunnelReady=$ready; HealthPort=8081}
