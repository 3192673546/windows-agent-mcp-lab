"""Private stdio worker for public Windows.Graphics.Capture; no MCP or input API.

Each request starts a fresh capture session so no cached frame can predate an action.
The parent keeps this process alive (imports are reused) and enforces a hard deadline.
"""
from pathlib import Path
import base64
import ctypes
from ctypes import wintypes as W
import json
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).with_name('capture_deps')))
import cv2
import numpy as np
from windows_capture import WindowsCapture
import desktop_input as d

sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')
dwm = ctypes.WinDLL('dwmapi')
dwm.DwmGetWindowAttribute.argtypes = [W.HWND, W.DWORD, ctypes.c_void_p, W.DWORD]
dwm.DwmGetWindowAttribute.restype = ctypes.c_long

def geometry(hwnd, expected_pid):
    pid = W.DWORD()
    d.u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value != expected_pid or d.u.IsIconic(hwnd):
        raise RuntimeError('Capture target disappeared, changed process, or was minimized.')
    outer = d.rect(hwnd)
    r = W.RECT()
    if dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        raise RuntimeError('DWM frame bounds unavailable; cannot map screenshot coordinates.')
    visual = dict(left=r.left, top=r.top, right=r.right, bottom=r.bottom,
        width=r.right-r.left, height=r.bottom-r.top)
    if not 0 < outer['width'] * outer['height'] <= 4_000_000:
        raise RuntimeError('Window screenshot exceeds the 4 megapixel limit.')
    return outer, visual

def capture(hwnd, expected_pid):
    outer, visual = geometry(hwnd, expected_pid)
    ready = threading.Event()
    result = {}
    # Optional session properties unsupported by older Windows are left at their
    # OS default. HWND is exact; no substring title or monitor-wide capture.
    session = WindowsCapture(window_hwnd=hwnd, cursor_capture=False, draw_border=None)

    @session.event
    def on_frame_arrived(frame, control):
        try:
            if frame.width * frame.height > 4_000_000:
                raise RuntimeError('Captured frame exceeds the 4 megapixel limit.')
            result.update(pixels=frame.frame_buffer.copy(), width=frame.width,
                height=frame.height, frameTime100ns=frame.timespan)
        except Exception as exc:
            result['error'] = str(exc)
        finally:
            control.stop()
            ready.set()

    @session.event
    def on_closed():
        ready.set()

    controller = session.start_free_threaded()
    if not ready.wait(1.5):
        # Parent timeout terminates this isolated process if native shutdown hangs.
        controller.stop()
        raise RuntimeError('No fresh WGC frame arrived within 1500 ms.')
    controller.wait()
    if 'pixels' not in result:
        raise RuntimeError(result.get('error', 'Capture target closed before a frame arrived.'))
    if geometry(hwnd, expected_pid) != (outer, visual):
        raise RuntimeError('Window moved or resized during capture; reobserve.')

    pixels = result['pixels']
    size = result['width'], result['height']
    if size == (visual['width'], visual['height']):
        dx, dy = visual['left']-outer['left'], visual['top']-outer['top']
        if dx < 0 or dy < 0 or dx+size[0] > outer['width'] or dy+size[1] > outer['height']:
            raise RuntimeError('DWM bounds do not fit the window; cannot map screenshot coordinates.')
        # Pad the invisible resize border rather than scaling pixels. This keeps
        # legacy screenshot x/y exactly relative to GetWindowRect.
        image = np.zeros((outer['height'], outer['width'], 4), dtype=np.uint8)
        image[dy:dy+size[1], dx:dx+size[0]] = pixels
    elif size == (outer['width'], outer['height']):
        image = pixels
    else:
        raise RuntimeError(f'Unexpected WGC frame dimensions {size}; cannot map screenshot coordinates.')
    ok, encoded = cv2.imencode('.png', image, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    if not ok or encoded.nbytes > 5_000_000:
        raise RuntimeError('Could not encode screenshot within the 5 MB limit.')
    return {'ok': True, 'data': base64.b64encode(encoded).decode('ascii'),
        'bytes': int(encoded.nbytes), 'mimeType': 'image/png',
        'captureMethod': 'WindowsGraphicsCapture', 'freshSession': True,
        'frameTime100ns': result['frameTime100ns'], 'visualRect': visual,
        'window': {'rect': outer, 'processId': expected_pid}}

for line in sys.stdin:
    try:
        if len(line) > 4096:
            raise ValueError('Capture request too large.')
        request = json.loads(line)
        started = time.perf_counter()
        response = capture(int(request['window_id']), int(request['process_id']))
        response['captureMs'] = round((time.perf_counter()-started)*1000, 1)
    except Exception as exc:
        response = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
    print(json.dumps(response, separators=(',', ':')), flush=True)
