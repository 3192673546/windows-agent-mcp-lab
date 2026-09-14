# Computer Use MCP 0.8.0

Windows UI Automation plus screenshot/keyboard/mouse automation using public Windows
APIs. This implementation does not import Codex's private runtime or copy its binaries.

## Observe, act, verify

Start with list_windows/get_window, then get_window_state.
Use only actions advertised by fresh element_index values.
click/set_value retain native background messages for supported Button/Edit controls;
controls without HWNDs can use UIA InvokePattern/ValuePattern.
perform_action supports observed click, set_value, toggle, expand, collapse, select,
and set_range_value patterns. UIA provider actions may change focus.

For sparse/custom-drawn controls, call activate_window, then
get_window_state(include_screenshot=true). Pass the screenshot id as screenshot_id to
click_at, scroll or drag. Coordinates are physical pixels within the target screenshot.
Input moves the physical cursor and uses SendInput. Keyboard input requires fresh,
unchanged native and UIA focus. Unicode text does not use the shared clipboard.

Every action invalidates the prior state. MCP actions now return a fresh `state`
by default, using the previous observation's text/screenshot settings. Inspect it to
verify application results. Pass its `stateId` as `state_id` for the next indexed or
keyboard action; coordinates already require the fresh `screenshot_id`. This prevents
old integer indices silently binding to new controls. Legacy clients lacking `state_id`
must explicitly call get_window_state again. `include_state=false` disables automatic
capture; `settle_ms` controls the post-action delay (default 80 ms, maximum 2000 ms).
An `observationError` preserves the executed action's result: reobserve without repeating
the write. A fresh snapshot alone does not prove an asynchronous workflow completed.
A delivered click or input reports outcomeVerified=false.

## Tools

list_windows, get_window, restore_window, get_window_state, click, set_value,
activate_window, perform_action, click_at, scroll, drag, type_text, press_key.

restore_window stays non-activating. activate_window explicitly changes foreground.
The server rejects stale screenshots (120 seconds), changed process/window rectangle,
lost foreground, covered coordinates, changed keyboard focus and password fields.
Windows may block activation or input to higher integrity applications.

## Capture and observation

Screenshots stay in memory. The default capture path uses Windows.Graphics.Capture
through the public windows-capture library, selecting the exact observed HWND and
checking its process identity. This is our own integration of the public Windows API.
A persistent isolated worker reuses imports; each request starts a new capture session
so an old buffered frame cannot be returned after an action. Calls have a 3-second
parent deadline, and the helper exits after five idle minutes.

WGC excludes invisible resize borders. The server pads those borders back to the
GetWindowRect canvas using verified DWM bounds, without scaling pixels. Screenshot
coordinates therefore retain the original physical-pixel origin. A moved/resized
window, unsupported dimensions or lost target identity cannot authorize stale input.
WGC can see an occluded target, while physical input still rejects covered points.

capture_backend=auto prefers WGC and exposes captureFallbackReason if compatibility
capture is needed (PrintWindow in background, screen pixels in foreground).
capture_backend=wgc requires WGC; capture_backend=legacy explicitly uses compatibility
capture. Minimized windows must first be restored. Auto mode briefly backs off after
WGC errors to avoid repeatedly paying a timeout. WGC prioritizes capture reliability;
fresh-session capture may cost more than PrintWindow on a given application.

Dependencies are scoped to capture_deps; no global Python packages are required:

    python -m pip install --only-binary=:all: --target capture_deps -r capture-requirements.txt

UIA traversal includes sibling controls and is limited to 150 elements by default,
500 maximum. Python/PowerShell transport uses UTF-8 in both directions.
Observation warnings expose UIA provider failures. `treeTruncated` reports omitted
nodes; `treeIncomplete` reports provider errors. A missing control in either case is
not evidence that the control does not exist. `include_text=false` skips tree traversal
and retains window/focus checks, making screenshot-only observations faster.
`observation.timingMs` reports local capture cost, excluding connector/network/model time.

## Scope and lifecycle

Existing browser, terminal, authentication, security, password-manager and
Codex/ChatGPT target filters remain. A regression-test title alone is not an exception:
test shell-hosted windows must belong to explicitly registered child process IDs.
No shell execution, arbitrary UIA invocation, app launching, registry access or
arbitrary script execution is exposed.

The PowerShell/.NET helper starts lazily, serializes operations and exits after
five idle minutes. Use the existing start-computer-use-tunnel.cmd for the connection.

## Validation

python server.py --self-test
python compat_test.py
python wpf_test.py
python performance_test.py
python capture_test.py

The compatibility suite briefly displays a disposable WinForms window for foreground
input and restores the previous foreground/cursor if the test still owns focus.
The WPF suite verifies controls without HWNDs and additional UIA patterns.
No user document is used.

Refresh Main Computer Use Local tools if the client still displays the old six-tool schema.

