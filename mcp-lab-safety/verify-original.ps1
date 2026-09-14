$ErrorActionPreference = 'Stop'
$baselinePath = Join-Path $PSScriptRoot 'original-local-coding-agent-baseline.json'
$baseline = Get-Content -LiteralPath $baselinePath -Raw | ConvertFrom-Json
$failures = @()

foreach ($row in $baseline) {
    if (-not (Test-Path -LiteralPath $row.path)) {
        $failures += "MISSING: $($row.path)"
        continue
    }
    $item = Get-Item -LiteralPath $row.path
    $hash = (Get-FileHash -LiteralPath $row.path -Algorithm SHA256).Hash
    if ($item.Length -ne $row.length) {
        $failures += "LENGTH CHANGED: $($row.path) expected=$($row.length) actual=$($item.Length)"
    }
    if ($hash -ne $row.sha256) {
        $failures += "HASH CHANGED: $($row.path)"
    }
}

if ($failures.Count -gt 0) {
    Write-Host 'ORIGINAL PROJECT ISOLATION CHECK: FAIL'
    $failures | ForEach-Object { Write-Host $_ }
    exit 2
}

Write-Host 'ORIGINAL PROJECT ISOLATION CHECK: PASS'
exit 0
