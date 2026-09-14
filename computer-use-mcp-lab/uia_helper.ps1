param(
    [string]$Operation = '',
    [string]$TitleContains = '',
    [string]$Value = '',
    [int]$ProcessId = 0,
    [int]$WindowId = 0,
    [long]$NativeWindowHandle = 0,
    [int]$MaxElements = 100,
    [switch]$Server
)

$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
[Console]::InputEncoding = $utf8
$OutputEncoding = $utf8
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Drawing
$script:ObservedElements = @{}

# UI Automation property reads are cross-process calls. Cache the small set of
# control properties we actually expose so each TreeWalker hop can fetch them
# in one batch instead of Control-Info issuing several Current.* calls.
$ControlCacheRequest = New-Object System.Windows.Automation.CacheRequest
$ControlCacheRequest.TreeScope = [System.Windows.Automation.TreeScope]::Element
$ControlCacheRequest.AutomationElementMode = [System.Windows.Automation.AutomationElementMode]::Full
$ControlCacheRequest.Add([System.Windows.Automation.AutomationElement]::NameProperty)
$ControlCacheRequest.Add([System.Windows.Automation.AutomationElement]::AutomationIdProperty)
$ControlCacheRequest.Add([System.Windows.Automation.AutomationElement]::ControlTypeProperty)
$ControlCacheRequest.Add([System.Windows.Automation.AutomationElement]::LocalizedControlTypeProperty)
$ControlCacheRequest.Add([System.Windows.Automation.AutomationElement]::NativeWindowHandleProperty)
$ControlCacheRequest.Add([System.Windows.Automation.AutomationElement]::IsEnabledProperty)
$ControlCacheRequest.Add([System.Windows.Automation.AutomationElement]::HasKeyboardFocusProperty)
$ControlCacheRequest.Add([System.Windows.Automation.AutomationElement]::IsPasswordProperty)
$ControlCacheRequest.Add([System.Windows.Automation.AutomationElement]::BoundingRectangleProperty)

Add-Type @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class NativeCapture {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")]
    public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);
    [DllImport("user32.dll")]
    public static extern bool IsWindow(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")]
    public static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [DllImport("user32.dll")]
    public static extern IntPtr GetAncestor(IntPtr hWnd, uint gaFlags);
    [DllImport("user32.dll")]
    public static extern IntPtr GetParent(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern int GetDlgCtrlID(IntPtr hWnd);
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, IntPtr wParam, string lParam, uint flags, uint timeout, out IntPtr result);
    [DllImport("user32.dll", SetLastError=true)]
    public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam, uint flags, uint timeout, out IntPtr result);
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, IntPtr wParam, StringBuilder lParam, uint flags, uint timeout, out IntPtr result);
}
'@
try { [void][NativeCapture]::SetProcessDpiAwarenessContext([IntPtr](-4)) } catch {}

function Bound-String($value, [int]$maxChars) {
    $text = [string]$value
    if ($text.Length -le $maxChars) { return $text }
    return $text.Substring(0, $maxChars)
}

function Find-Window([string]$needle, [int]$processId, [int]$windowId) {
    if ($windowId -gt 0) {
        $hwnd = [IntPtr]$windowId
        if (-not [NativeCapture]::IsWindow($hwnd)) { throw "WindowId $windowId is stale or invalid" }
        if ([NativeCapture]::GetAncestor($hwnd, 2) -ne $hwnd) { throw "WindowId $windowId is not a top-level window" }
        $win = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
        if ($null -eq $win) { throw "WindowId $windowId has no UI Automation element" }
        $title = $win.Current.Name
        if ($processId -gt 0 -and $win.Current.ProcessId -ne $processId) { throw "WindowId $windowId does not belong to processId $processId" }
        if ($needle -and $title.IndexOf($needle, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) { throw "WindowId $windowId does not match title '$needle'" }
        return $win
    }
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $wins = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
    $matches = @()
    foreach ($win in $wins) {
        if ($windowId -gt 0 -and $win.Current.NativeWindowHandle -ne $windowId) { continue }
        $title = $win.Current.Name
        if ($processId -gt 0 -and $win.Current.ProcessId -ne $processId) { continue }
        if ($windowId -gt 0 -and (-not $needle -or $title.IndexOf($needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0)) {
            $matches += $win
            continue
        }
        if (-not $title) { continue }
        if ($needle -and $title.IndexOf($needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            $matches += $win
        }
    }
    if ($matches.Count -eq 0) {
        throw "No automation window matched title='$needle' processId=$processId windowId=$windowId"
    }
    if ($windowId -gt 0 -and $matches.Count -eq 1) { return $matches[0] }
    $exact = @($matches | Where-Object { $_.Current.Name.Equals($needle, [System.StringComparison]::OrdinalIgnoreCase) })
    if ($exact.Count -eq 1) { return $exact[0] }
    if ($matches.Count -eq 1) { return $matches[0] }
    $descriptions = @($matches | ForEach-Object { "'$($_.Current.Name)' pid=$($_.Current.ProcessId)" }) -join '; '
    throw "Ambiguous window match for '$needle': $descriptions. Supply a more specific title or process_id."
}

function Window-Info($el) {
    $procId = $el.Current.ProcessId
    $processName = $null
    if ($null -ne $script:ProcessNameCache -and $script:ProcessNameCache.ContainsKey($procId)) {
        $processName = $script:ProcessNameCache[$procId]
    } else {
        try { $processName = [System.Diagnostics.Process]::GetProcessById($procId).ProcessName } catch {}
        if ($null -ne $script:ProcessNameCache) { $script:ProcessNameCache[$procId] = $processName }
    }
    $hwnd = [IntPtr]$el.Current.NativeWindowHandle
    $info = [ordered]@{
        name = Bound-String $el.Current.Name 1000
        processId = $procId
        processName = $processName
        nativeWindowHandle = $el.Current.NativeWindowHandle
        isVisible = if ($hwnd -ne [IntPtr]::Zero) { [NativeCapture]::IsWindowVisible($hwnd) } else { $false }
        isMinimized = if ($hwnd -ne [IntPtr]::Zero) { [NativeCapture]::IsIconic($hwnd) } else { $false }
    }
    return $info
}

function Control-Info($el) {
    $patterns = @($el.GetSupportedPatterns() | ForEach-Object { $_.ProgrammaticName })
    $reference = [guid]::NewGuid().ToString('N')
    $script:ObservedElements[$reference] = @{ Element=$el; Root=$WindowId }
    $rect = $el.Cached.BoundingRectangle
    $value = $null
    if (-not $el.Cached.IsPassword -and $patterns -contains 'ValuePatternIdentifiers.Pattern') {
        try { $value = Bound-String $el.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value 1000 } catch {}
    }
    [ordered]@{
        elementRef = $reference
        patterns = $patterns
        value = $value
        rect = if (-not $rect.IsEmpty) { @{left=$rect.Left;top=$rect.Top;width=$rect.Width;height=$rect.Height} } else { $null }
        name = Bound-String $el.Cached.Name 500
        automationId = Bound-String $el.Cached.AutomationId 500
        controlType = $el.Cached.ControlType.ProgrammaticName
        localizedControlType = Bound-String $el.Cached.LocalizedControlType 100
        nativeWindowHandle = $el.Cached.NativeWindowHandle
        isEnabled = $el.Cached.IsEnabled
        hasKeyboardFocus = $el.Cached.HasKeyboardFocus
        isPassword = $el.Cached.IsPassword
    }
}

function Assert-NativeChild([IntPtr]$hwnd, [IntPtr]$expectedRoot) {
    if ($hwnd -eq [IntPtr]::Zero -or -not [NativeCapture]::IsWindow($hwnd)) {
        throw 'Native control handle is stale or invalid.'
    }
    $root = [NativeCapture]::GetAncestor($hwnd, 2) # GA_ROOT
    if ($root -ne $expectedRoot) {
        throw "Native control no longer belongs to the observed target window (expected=$expectedRoot actual=$root)."
    }
}

function Get-ControlDescendantsLimited($root, [int]$maxElements) {
    $script:TreeReadFailed = $false
    $limit = [Math]::Max(1, $maxElements)
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $script:LastTreeView = 'ControlView'
    try {
        $rootProcessName = [System.Diagnostics.Process]::GetProcessById($root.Current.ProcessId).ProcessName
        if ($rootProcessName -eq 'electron') {
            $walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
            $script:LastTreeView = 'RawView'
        }
    } catch {}
    $result = [System.Collections.Generic.List[System.Windows.Automation.AutomationElement]]::new()
    $stack = [System.Collections.Generic.Stack[System.Windows.Automation.AutomationElement]]::new()
    # Seed every top-level sibling. Previously only the first child's subtree
    # was visited, silently hiding most controls in ordinary windows.
    $roots = [System.Collections.Generic.List[System.Windows.Automation.AutomationElement]]::new()
    try { $first = $walker.GetFirstChild($root, $ControlCacheRequest) } catch { $script:TreeReadFailed = $true; return ,$result }
    while ($null -ne $first -and $roots.Count -lt $limit) {
        $roots.Add($first)
        try { $first = $walker.GetNextSibling($first, $ControlCacheRequest) } catch { $script:TreeReadFailed = $true; break }
    }
    for ($i=$roots.Count-1; $i -ge 0; $i--) { $stack.Push($roots[$i]) }
    while ($stack.Count -gt 0 -and $result.Count -lt $limit) {
        $current = $stack.Pop()
        $result.Add($current)
        if ($result.Count -ge $limit) { break }
        $children = [System.Collections.Generic.List[System.Windows.Automation.AutomationElement]]::new()
        $childBudget = [Math]::Max(0, $limit - $result.Count - $stack.Count)
        if ($childBudget -le 0) { continue }
        try { $child = $walker.GetFirstChild($current, $ControlCacheRequest) } catch { $script:TreeReadFailed = $true; continue }
        while ($null -ne $child -and $children.Count -lt $childBudget) {
            $children.Add($child)
            try { $child = $walker.GetNextSibling($child, $ControlCacheRequest) } catch { $script:TreeReadFailed = $true; break }
        }
        for ($i = $children.Count - 1; $i -ge 0; $i--) {
            $stack.Push($children[$i])
        }
    }
    return ,$result
}

function Invoke-Operation {
switch ($Operation) {
    'list' {
        $script:ProcessNameCache = @{}
        try {
            $root = [System.Windows.Automation.AutomationElement]::RootElement
            $wins = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
            $out = @()
            foreach ($win in $wins) {
                try {
                    if ($win.Current.Name) { $out += Window-Info $win }
                } catch {
                    # Top-level windows can disappear while UIA is enumerating them.
                    # Skip only the stale window instead of failing the whole snapshot.
                    continue
                }
            }
            ConvertTo-Json -InputObject $out -Depth 6 -Compress
        } finally {
            $script:ProcessNameCache = $null
        }
    }
    'window' {
        $window = Find-Window $TitleContains $ProcessId $WindowId
        Window-Info $window | ConvertTo-Json -Depth 6 -Compress
    }
    'tree' {
        $window = Find-Window $TitleContains $ProcessId $WindowId
        foreach($key in @($script:ObservedElements.Keys)) {
            if ($script:ObservedElements[$key].Root -eq $WindowId) { $script:ObservedElements.Remove($key) }
        }
        if ($script:ObservedElements.Count -gt 4000) { $script:ObservedElements.Clear() }
        # One extra node distinguishes a complete list from a clipped list.
        $all = Get-ControlDescendantsLimited $window ($MaxElements + 1)
        $out = @()
        $errors = @()
        for ($i = 0; $i -lt [Math]::Min($all.Count, $MaxElements); $i++) {
            try { $out += Control-Info $all[$i] } catch { $errors += $_.Exception.Message; continue }
        }
        [ordered]@{ window = Window-Info $window; view = $script:LastTreeView; elements = $out; truncated = ($all.Count -gt $MaxElements); incomplete = ($script:TreeReadFailed -or $errors.Count -gt 0); warnings = @($errors | Select-Object -Unique -First 3) } | ConvertTo-Json -Depth 7 -Compress
    }
    'restore_no_activate' {
        $window = Find-Window $TitleContains $ProcessId $WindowId
        $hwnd = [IntPtr]$window.Current.NativeWindowHandle
        if ($hwnd -eq [IntPtr]::Zero -or -not [NativeCapture]::IsWindow($hwnd)) { throw 'Target window handle is stale or invalid.' }
        $before = [NativeCapture]::GetForegroundWindow()
        $wasVisible = [NativeCapture]::IsWindowVisible($hwnd)
        $wasMinimized = [NativeCapture]::IsIconic($hwnd)
        [void][NativeCapture]::ShowWindow($hwnd, 4) # SW_SHOWNOACTIVATE
        Start-Sleep -Milliseconds 50
        $after = [NativeCapture]::GetForegroundWindow()
        if ($after -ne $before) { throw "Background restore safety violation: foreground changed from $before to $after" }
        $nowVisible = [NativeCapture]::IsWindowVisible($hwnd)
        $nowMinimized = [NativeCapture]::IsIconic($hwnd)
        if (-not $nowVisible -or $nowMinimized) { throw 'SW_SHOWNOACTIVATE did not restore the target window.' }
        [ordered]@{
            ok = $true
            action = 'SW_SHOWNOACTIVATE'
            foregroundUnchanged = $true
            foregroundBefore = [long]$before
            foregroundAfter = [long]$after
            wasVisible = $wasVisible
            wasMinimized = $wasMinimized
            window = Window-Info $window
        } | ConvertTo-Json -Depth 7 -Compress
    }
    'set_text_background' {
        if ($Value.Length -gt 100000) { throw 'Background text value exceeds 100000 characters.' }
        $window = Find-Window $TitleContains $ProcessId $WindowId
        $root = [IntPtr]$window.Current.NativeWindowHandle
        $hwnd = [IntPtr]$NativeWindowHandle
        Assert-NativeChild $hwnd $root
        $result = [IntPtr]::Zero
        $ok = [NativeCapture]::SendMessageTimeout($hwnd, 0x000C, [IntPtr]::Zero, $Value, 0x0002, 1500, [ref]$result) # WM_SETTEXT, SMTO_ABORTIFHUNG
        if ($ok -eq [IntPtr]::Zero) { throw 'Background WM_SETTEXT failed or timed out.' }
        $capacity = [Math]::Max(2, $Value.Length + 2)
        $buffer = New-Object System.Text.StringBuilder($capacity)
        $verifyResult = [IntPtr]::Zero
        $verifyOk = [NativeCapture]::SendMessageTimeout($hwnd, 0x000D, [IntPtr]$capacity, $buffer, 0x0002, 1500, [ref]$verifyResult) # WM_GETTEXT
        if ($verifyOk -eq [IntPtr]::Zero) { throw 'Background WM_GETTEXT verification failed or timed out.' }
        $observed = $buffer.ToString()
        if ($observed -ne $Value) { throw 'Background text write verification failed.' }
        [ordered]@{ ok = $true; action = 'WM_SETTEXT'; nativeWindowHandle = $NativeWindowHandle; value = $observed } | ConvertTo-Json -Depth 5 -Compress
    }
    'click_button_background' {
        $window = Find-Window $TitleContains $ProcessId $WindowId
        $root = [IntPtr]$window.Current.NativeWindowHandle
        $hwnd = [IntPtr]$NativeWindowHandle
        Assert-NativeChild $hwnd $root
        $parent = [NativeCapture]::GetParent($hwnd)
        if ($parent -eq [IntPtr]::Zero) { throw 'Native button has no parent window.' }
        $controlId = [NativeCapture]::GetDlgCtrlID($hwnd)
        $wParam = [IntPtr]($controlId -band 0xFFFF) # BN_CLICKED = 0 in HIWORD
        $result = [IntPtr]::Zero
        $ok = [NativeCapture]::SendMessageTimeout($parent, 0x0111, $wParam, $hwnd, 0x0002, 1500, [ref]$result) # WM_COMMAND
        if ($ok -eq [IntPtr]::Zero) { throw 'Background WM_COMMAND/BN_CLICKED failed or timed out.' }
        [ordered]@{ ok = $true; action = 'WM_COMMAND/BN_CLICKED'; nativeWindowHandle = $NativeWindowHandle; controlId = $controlId } | ConvertTo-Json -Depth 5 -Compress
    }
    'focused' {
        $window = Find-Window $TitleContains $ProcessId $WindowId
        $el = [System.Windows.Automation.AutomationElement]::FocusedElement
        $parent = $el; $belongs = $false
        for($depth=0; $depth -lt 80 -and $null -ne $parent; $depth++) {
            if ($parent.Current.NativeWindowHandle -eq $WindowId) { $belongs=$true; break }
            $parent = [System.Windows.Automation.TreeWalker]::RawViewWalker.GetParent($parent)
        }
        if ($belongs) {
            [ordered]@{ belongsToTarget=$true; runtimeId=($el.GetRuntimeId() -join ','); password=$el.Current.IsPassword } | ConvertTo-Json -Compress
        } else { '{"belongsToTarget":false}' }
    }
    'uia_action' {
        $window = Find-Window $TitleContains $ProcessId $WindowId
        $saved = $script:ObservedElements[$ElementRef]
        if ($null -eq $saved -or $saved.Root -ne $WindowId) { throw 'UIA element reference expired; reobserve.' }
        $el = $saved.Element
        if ($el.Current.ProcessId -ne $window.Current.ProcessId) { throw 'UIA element process changed.' }
        if (-not $el.Current.IsEnabled -or $el.Current.IsPassword) { throw 'Element is disabled or protected.' }
        # Verify current ancestry; the element may have moved or been recycled.
        $parent = $el
        $belongs = $false
        for($depth=0; $depth -lt 80 -and $null -ne $parent; $depth++) {
            if ($parent.Current.NativeWindowHandle -eq $WindowId) { $belongs=$true; break }
            $parent = [System.Windows.Automation.TreeWalker]::RawViewWalker.GetParent($parent)
        }
        if (-not $belongs) { throw 'UIA element no longer belongs to target.' }
        $observed = $null
        switch($Action) {
            'click' { $el.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() }
            'set_value' {
                $p = $el.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
                if ($p.Current.IsReadOnly) { throw 'UIA value is read-only.' }
                $p.SetValue($Value)
                $observed = $p.Current.Value
                if ($observed -cne $Value) { throw 'UIA value read-back mismatch; reobserve.' }
            }
            'toggle' {
                $p = $el.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
                $p.Toggle(); $observed = [string]$p.Current.ToggleState
            }
            'expand' {
                $p = $el.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
                $p.Expand(); $observed = [string]$p.Current.ExpandCollapseState
            }
            'collapse' {
                $p = $el.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
                $p.Collapse(); $observed = [string]$p.Current.ExpandCollapseState
            }
            'select' {
                $p = $el.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
                $p.Select(); $observed = $p.Current.IsSelected
            }
            'set_range_value' {
                $p = $el.GetCurrentPattern([System.Windows.Automation.RangeValuePattern]::Pattern)
                $number = [double]::Parse($Value,[Globalization.CultureInfo]::InvariantCulture)
                if ([double]::IsNaN($number) -or [double]::IsInfinity($number) -or $p.Current.IsReadOnly -or $number -lt $p.Current.Minimum -or $number -gt $p.Current.Maximum) { throw 'Range value unavailable or out of bounds.' }
                $p.SetValue($number); $observed = $p.Current.Value
            }
            default { throw 'Unsupported UIA action.' }
        }
        $script:ObservedElements.Remove($ElementRef)
        [ordered]@{ ok=$true; method='UIAutomation'; action=$Action; observedValue=$observed } | ConvertTo-Json -Depth 6 -Compress
    }
    'screenshot' {
        $window = Find-Window $TitleContains $ProcessId $WindowId
        $hwnd = [IntPtr]$window.Current.NativeWindowHandle
        if ($hwnd -eq [IntPtr]::Zero) { throw 'Target window has no native HWND.' }
        $rect = New-Object NativeCapture+RECT
        if (-not [NativeCapture]::GetWindowRect($hwnd, [ref]$rect)) { throw 'GetWindowRect failed.' }
        $width = [Math]::Max(1, $rect.Right - $rect.Left)
        $height = [Math]::Max(1, $rect.Bottom - $rect.Top)
        if (([int64]$width * [int64]$height) -gt 4000000) { throw 'Window screenshot exceeds 4 megapixel safety limit.' }
        $bmp = New-Object System.Drawing.Bitmap($width, $height)
        $graphics = [System.Drawing.Graphics]::FromImage($bmp)
        $captureMethod = 'PrintWindow'
        if ([NativeCapture]::GetForegroundWindow() -eq $hwnd) {
            try {
                $graphics.CopyFromScreen($rect.Left,$rect.Top,0,0,[System.Drawing.Size]::new($width,$height))
                $ok=$true; $captureMethod='ForegroundScreen'
            } catch { $ok=$false }
        } else {
            $hdc = $graphics.GetHdc()
            try { $ok = [NativeCapture]::PrintWindow($hwnd, $hdc, 2) }
            finally { $graphics.ReleaseHdc($hdc) }
        }
        if (-not $ok) { $graphics.Dispose(); $bmp.Dispose(); throw 'PrintWindow failed.' }
        $stream = New-Object System.IO.MemoryStream
        try {
            $bmp.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
            $bytes = $stream.ToArray()
            if ($bytes.Length -gt 5000000) { throw 'Window screenshot exceeds 5 MB safety limit.' }
            $data = [Convert]::ToBase64String($bytes)
        } finally {
            $stream.Dispose()
            $graphics.Dispose()
            $bmp.Dispose()
        }
        $windowInfo = Window-Info $window
        $windowInfo.rect = [ordered]@{
            left = [int]$rect.Left
            top = [int]$rect.Top
            right = [int]$rect.Right
            bottom = [int]$rect.Bottom
            width = [int]$width
            height = [int]$height
        }
        [ordered]@{ ok = $true; data = $data; bytes = $bytes.Length; mimeType = 'image/png'; captureMethod=$captureMethod; window = $windowInfo } | ConvertTo-Json -Depth 7 -Compress
    }
    default { throw "Unknown operation: $Operation" }
}
}

if ($Server) {
    while ($null -ne ($line = [Console]::In.ReadLine())) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try {
            $request = $line | ConvertFrom-Json
            $Operation = [string]$request.operation
            $ElementRef = [string]$request.element_ref
            $Action = [string]$request.action
            $TitleContains = [string]$request.title_contains
            $Value = [string]$request.value
            $ProcessId = if ($null -ne $request.process_id) { [int]$request.process_id } else { 0 }
            $WindowId = if ($null -ne $request.window_id) { [int]$request.window_id } else { 0 }
            $NativeWindowHandle = if ($null -ne $request.native_window_handle) { [long]$request.native_window_handle } else { 0 }
            $MaxElements = if ($null -ne $request.max_elements) { [int]$request.max_elements } else { 100 }
            $response = Invoke-Operation
            [Console]::Out.WriteLine([string]$response)
        } catch {
            $failure = [ordered]@{ __uia_error = $_.Exception.Message } | ConvertTo-Json -Compress
            [Console]::Out.WriteLine($failure)
        }
        [Console]::Out.Flush()
    }
    exit 0
}

if (-not $Operation) { throw 'Operation is required unless -Server is used.' }
Invoke-Operation
