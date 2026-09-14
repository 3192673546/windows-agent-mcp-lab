# Browser MCP 0.6.0

Local browser automation through an isolated headless Edge/Chromium session and CDP.
This browser has its own temporary profile; no physical desktop mouse or keyboard is used.

## Observe, act, verify

Select the exact tab, call ax_get, and prefer ax_click/ax_set_value with fresh element
indices. For canvases or controls absent from AX, inspect tab_screenshot and pass its
screenshotId as screenshot_id to mouse_click, mouse_move, mouse_scroll or mouse_drag.
Coordinates are viewport CSS pixels; screenshot metadata provides image/viewport dimensions.
get_state observes the persistent selected tab and can return both accessibility and
screenshots. include_text=false skips AX for visual-only work. Existing ax_get and
tab_screenshot calls remain available.

Input actions and navigation return a fresh state by default, with the previous
observation's text/screenshot settings. Inspect the returned state; pass its stateId
as state_id for the next indexed or keyboard action. A coordinate action still requires
the new screenshot_id. Old indices and state tokens cannot cross actions or tabs.
Legacy clients without state_id must explicitly ax_get again. include_state=false
retains the explicit observe/action workflow; settle_ms sets the post-action delay
(80 ms by default, 0–2000 ms). Observe asynchronous application completion with wait_for.

If post-action observation fails, observationError preserves the completed input
result. The server does not repeat the action. Fresh observation alone is not proof
of business workflow completion. Nested screenshots are sent as MCP image items,
without duplicate base64 in structuredContent.

wait_for checks expected text and returns a fresh AX state.
Successful input dispatch does not prove that a workflow or submission completed.

## Tools and changed behavior

The original 16 tools remain. Five tools were added:
- mouse_click: screenshot_id, x, y, optional button and click_count (1 or 2).
- mouse_move: screenshot_id, x, y for hover.
- mouse_scroll: screenshot_id, x, y, delta_x/delta_y (positive down/right, CSS pixels).
- mouse_drag: screenshot_id, from_x, from_y, to_x, to_y, duration_ms (100–2000).
- wait_for: text, timeout_ms (0–15000), returning fresh AX state.

Version 0.6.0 adds get_state (22 tools total), plus optional state_id, include_state
and settle_ms on action tools. Tool-list refresh is needed to use these parameters.

ax_click now uses full browser pointer/mouse input, scrolls into view and rejects
covered/disabled controls. It never automatically repeats uncertain clicks.
ax_set_value uses browser text input for editors, verifies read-back and supports
native select values. ax_press_key supports navigation keys and Control/Alt/Shift chords.

## Limits

AX defaults to 150 nodes, capped at 500, with truncation reported.
Actions/navigation invalidate observations. Screenshot references also expire after
120 seconds and are checked against the tab, URL and viewport. Independent animation
can still change the page: inspect a fresh screenshot before choosing coordinates.
Screenshots stay in memory. Calls are serialized; the isolated browser runs below
normal priority, uses native CPU speed (CDP throttle rate 1, previously 2), and auto-closes after five idle minutes.

Nested-frame element coordinate translation is not inferred; use screenshot coordinates
when AX actions report a frame limitation. Pointer dragging supports sliders/canvases;
HTML5 data-transfer dragging and OS dialogs may require additional support.
Headless login/anti-automation behavior can differ from the user's regular browser.
Only HTTP(S) and about:blank navigation are exposed, with no arbitrary script tool.

## Validation and connection

Run node server.mjs --self-test, node compat-test.mjs and node state-test.mjs.
Tests cover original workflows and trusted pointer events, framework input state,
Unicode, editors/selects, occlusion, scrolling, canvas interaction, stale screenshots,
chords, waits and active-tab recovery.

Use the existing start-browser-tunnel.cmd. If the client still shows the previous
tool schema, refresh the Main Browser Local connector's tools after upgrading.

