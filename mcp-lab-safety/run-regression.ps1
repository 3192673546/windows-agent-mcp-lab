param(
    [switch]$Smoke,
    [switch]$Stress
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Assert-Original {
    & pwsh -NoLogo -NoProfile -File (Join-Path $PSScriptRoot 'verify-original.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Original local-coding-agent isolation check failed.' }
}

function Assert-NoForbiddenComputerInput {
    $files = @(
        (Join-Path $root 'computer-use-mcp-lab\server.py'),
        (Join-Path $root 'computer-use-mcp-lab\uia_helper.ps1')
    )
    $patterns = 'SetForegroundWindow|SetCursorPos|SendInput|mouse_event|keybd_event|\.SetValue\(|InvokePattern|TogglePattern|SelectionItemPattern|ExpandCollapsePattern'
    $matches = Select-String -Path $files -Pattern $patterns -CaseSensitive:$false
    if ($matches) {
        $matches | Format-Table Path,LineNumber,Line -AutoSize | Out-String | Write-Host
        throw 'Foreground/physical-input API found in Computer Use runtime.'
    }
    $helper = Get-Content (Join-Path $root 'computer-use-mcp-lab\uia_helper.ps1') -Raw
    $server = Get-Content (Join-Path $root 'computer-use-mcp-lab\server.py') -Raw
    if ($server -notmatch '"restore_window"') { throw 'Safe restore_window tool is missing.' }
    if ($helper -notmatch 'ShowWindow\(\$hwnd,\s*4\)') { throw 'restore_window must use SW_SHOWNOACTIVATE only.' }
    if ($helper -notmatch 'GetForegroundWindow\(\)' -or $helper -notmatch 'foreground changed') { throw 'restore_window foreground-preservation verification is missing.' }
    Write-Host 'STATIC_BACKGROUND_INPUT_GUARD PASS'
}

function Assert-LowResourceArchitecture {
    $computerServer = Get-Content (Join-Path $root 'computer-use-mcp-lab\server.py') -Raw
    $computerHelper = Get-Content (Join-Path $root 'computer-use-mcp-lab\uia_helper.ps1') -Raw
    $browserServer = Get-Content (Join-Path $root 'browser-mcp-lab\server.mjs') -Raw
    $browserProfile = Get-Content (Join-Path $root 'browser-mcp-lab\tunnel-profile.yaml') -Raw
    $computerProfile = Get-Content (Join-Path $root 'computer-use-mcp-lab\tunnel-profile.yaml') -Raw

    if ($computerServer -match 'subprocess\.run\s*\(') { throw 'Computer Use regressed to per-call subprocess.run helper launches.' }
    if ($computerServer -notmatch '"-Server"') { throw 'Computer Use persistent UIA worker mode is missing.' }
    if ($computerServer -notmatch 'include_screenshot:\s*bool\s*=\s*False') { throw 'Computer Use screenshot default must remain off.' }
    if ($computerServer -notmatch 'min\(int\(max_elements\),\s*500\)') { throw 'Computer Use UIA observation hard cap must remain 500.' }
    if ($computerHelper -notmatch 'CacheRequest' -or $computerHelper -notmatch 'GetFirstChild\(\$root,\s*\$ControlCacheRequest\)') { throw 'Computer Use UIA property batching/cache request is missing.' }
    if ($browserServer -match '--disable-gpu') { throw 'Browser GPU acceleration was disabled, increasing CPU rendering pressure.' }
    if ($browserServer -match 'writeFileSync\s*\(') { throw 'Browser screenshot/file write path reappeared.' }
    if ($browserServer -notmatch 'toolCallQueue') { throw 'Browser tool-call serialization is missing.' }
    if ($browserServer -notmatch 'scheduleIdleClose') { throw 'Browser idle cleanup is missing.' }
    if ($browserServer -match "Accessibility\.enable") { throw 'Browser must not keep the Accessibility CDP domain continuously enabled.' }
    if ($browserServer -notmatch 'CPU_THROTTLE_RATE\s*=\s*2') { throw 'Browser renderer CPU throttle is missing.' }
    if ($browserServer -notmatch 'Math\.min\(Number\(max_elements\).*500\)') { throw 'Browser AX observation hard cap must remain 500.' }
    foreach ($profile in @($browserProfile, $computerProfile)) {
        if ($profile -notmatch 'max_concurrent_requests:\s*1') { throw 'Tunnel MCP concurrency must remain capped at 1.' }
        if ($profile -notmatch 'max_inflight_requests:\s*2') { throw 'Tunnel control-plane prefetch must remain capped at 2.' }
        if ($profile -notmatch 'level:\s*warn') { throw 'Tunnel logging must remain warning-only for low-overhead operation.' }
        if ($profile -notmatch 'log_buffer_events:\s*200') { throw 'Tunnel admin log buffer must remain capped at 200 events.' }
    }
    Write-Host 'STATIC_LOW_RESOURCE_GUARD PASS'
}

function Assert-NoLabProcesses {
    $processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
    $browserLeaks = @($processes | Where-Object {
        $_.Name -match '^(msedge|chrome)\.exe$' -and $_.CommandLine -match 'browser-mcp-lab-'
    })
    $computerLeaks = @($processes | Where-Object {
        $_.CommandLine -match 'computer-use-mcp-lab[\\/](test_target|uia_helper)\.ps1'
    })
    if ($browserLeaks.Count -gt 0 -or $computerLeaks.Count -gt 0) {
        Write-Host 'Browser leaks:'
        $browserLeaks | Select-Object ProcessId,Name,CommandLine | Format-Table -Wrap | Out-String | Write-Host
        Write-Host 'Computer target leaks:'
        $computerLeaks | Select-Object ProcessId,Name,CommandLine | Format-Table -Wrap | Out-String | Write-Host
        throw 'Lab process leak detected.'
    }
    Write-Host 'PROCESS_LEAK_GUARD PASS'
}

function Assert-NewTunnelsStoppedForLocalTests {
    $processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
    $active = @($processes | Where-Object {
        $_.Name -eq 'tunnel-client.exe' -and
        ($_.ExecutablePath -like '*\browser-mcp-lab\*' -or $_.ExecutablePath -like '*\computer-use-mcp-lab\*')
    })
    if ($active.Count -gt 0) {
        $active | Select-Object ProcessId,ExecutablePath,CommandLine | Format-Table -Wrap | Out-String | Write-Host
        throw 'Stop the Browser/Computer Use tunnel clients before local Smoke/Stress testing to avoid overlapping workloads.'
    }
    Write-Host 'NEW_TUNNEL_OVERLAP_GUARD PASS'
}

Write-Host '=== BASELINE ==='
Assert-Original
Assert-NoForbiddenComputerInput
Assert-LowResourceArchitecture

Write-Host '=== SYNTAX ==='
& node --check .\browser-mcp-lab\server.mjs
if ($LASTEXITCODE -ne 0) { throw 'Browser syntax check failed.' }
& python -m py_compile .\computer-use-mcp-lab\server.py .\mcp-lab-safety\protocol_regression.py
if ($LASTEXITCODE -ne 0) { throw 'Python syntax check failed.' }
Write-Host 'SYNTAX PASS'

if (-not $Smoke -and -not $Stress) {
    Write-Host 'STATIC-ONLY REGRESSION PASS'
    Write-Host 'Use -Smoke for one lightweight functional round or -Stress for the full stress/protocol suite.'
    exit 0
}

Assert-NewTunnelsStoppedForLocalTests

# Clean only lab-owned stale temporary browser profiles before the stress run.
Get-ChildItem -LiteralPath $env:TEMP -Directory -Filter 'browser-mcp-lab-*' -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$rounds = if ($Stress) { 3 } else { 1 }
$label = if ($Stress) { 'FUNCTIONAL STRESS' } else { 'LIGHTWEIGHT SMOKE' }
Write-Host "=== $label`: $rounds ROUND(S) ==="
for ($round = 1; $round -le $rounds; $round++) {
    Write-Host "--- round $round browser ---"
    & node .\browser-mcp-lab\server.mjs --self-test
    if ($LASTEXITCODE -ne 0) { throw "Browser self-test failed in round $round." }
    Assert-NoLabProcesses
    Assert-Original

    Write-Host "--- round $round computer ---"
    & python .\computer-use-mcp-lab\server.py --self-test
    if ($LASTEXITCODE -ne 0) { throw "Computer Use self-test failed in round $round." }
    Assert-NoLabProcesses
    Assert-Original
}

if ($Stress) {
    Write-Host '=== JSON-RPC / MCP PROTOCOL REGRESSION ==='
    & python .\mcp-lab-safety\protocol_regression.py
    if ($LASTEXITCODE -ne 0) { throw 'Protocol regression failed.' }
} else {
    Write-Host 'Skipping heavy protocol/chaos regression in -Smoke mode.'
}

Write-Host '=== FINAL LEAK / ISOLATION CHECK ==='
Start-Sleep -Milliseconds 500
Assert-NoLabProcesses
$tempProfiles = @(Get-ChildItem -LiteralPath $env:TEMP -Directory -Filter 'browser-mcp-lab-*' -ErrorAction SilentlyContinue)
if ($tempProfiles.Count -gt 0) {
    $tempProfiles | Select-Object FullName | Format-Table | Out-String | Write-Host
    throw 'Temporary browser profile leak detected.'
}
Write-Host 'TEMP_PROFILE_GUARD PASS'
Assert-NoForbiddenComputerInput
Assert-LowResourceArchitecture
Assert-Original

Write-Host 'ALL_REGRESSIONS PASS'
