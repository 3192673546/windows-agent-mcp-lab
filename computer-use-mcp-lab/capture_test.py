"""WGC pixels, coordinate mapping and recovery against disposable windows only."""
from pathlib import Path
import base64, ctypes, json, subprocess, sys, time
from unittest.mock import patch
import server as s
import desktop_input as d
sys.path.insert(0,str(Path(__file__).with_name('capture_deps')))
import cv2
import numpy as np

checks=[];processes=[];before=d.foreground();window=None;cover=None
cursor=ctypes.wintypes.POINT();d.u.GetCursorPos(ctypes.byref(cursor))
d.u.SetWindowPos.argtypes=[ctypes.wintypes.HWND,ctypes.wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.wintypes.UINT]
def pin_cover():
    assert d.u.SetWindowPos(cover['id'],ctypes.c_void_p(-1),0,0,0,0,0x53)
    time.sleep(.08)
def check(name):checks.append(name);print('PASS '+name,flush=True)
def spawn(cover=False):
    proc=subprocess.Popen(['pwsh','-NoProfile','-File',str(Path(__file__).with_name('capture_target.ps1'))]+(['-Cover'] if cover else []),creationflags=s.CREATE_NO_WINDOW)
    processes.append(proc);s.LAB_PROCESS_IDS.add(proc.pid)
    title='Computer Use MCP Lab Target Capture'+(' Cover' if cover else '')
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        s._invalidate_window_list_cache()
        target=next((w for w in s.list_windows() if w['title']==title),None)
        if target:return target
        time.sleep(.1)
    raise AssertionError('Fixture did not start')
def observe(backend='wgc'):
    return s.get_window_state(window,include_text=False,include_screenshot=True,capture_backend=backend)
def pixels(state):
    return cv2.imdecode(np.frombuffer(base64.b64decode(state['screenshots'][0]['data']),dtype=np.uint8),cv2.IMREAD_UNCHANGED)
def rejected(fn,needle):
    try:fn()
    except Exception as e:assert needle in str(e),str(e)
    else:raise AssertionError('Unexpected success')
try:
    window=spawn();cover=spawn(True)
    pin_cover()
    capture_foreground=d.foreground()
    state=observe();shot=state['screenshots'][0];r=d.rect(window['id'])
    assert shot['captureMethod']=='WindowsGraphicsCapture' and shot['freshCaptureSession']
    assert pixels(state).shape[:2]==(r['height'],r['width'])
    assert (shot['originX'],shot['originY'])==(r['left'],r['top'])
    check('WGC image padded to exact legacy window origin and dimensions')
    cr=d.rect(cover['id']);screen_x=(cr['left']+cr['right'])//2;screen_y=(cr['top']+cr['bottom'])//2
    assert int(d.u.GetAncestor(d.u.WindowFromPoint(ctypes.wintypes.POINT(screen_x,screen_y)),2) or 0)==cover['id'],'Cover is not actually occluding the test point'
    x,y=screen_x-r['left'],screen_y-r['top']
    assert list(pixels(state)[y,x,:3])==[255,0,255],pixels(state)[y,x]
    assert d.foreground()==capture_foreground,'Capture changed foreground'
    cv2.imwrite(str(Path(__file__).with_name('capture-test-wgc.png')),pixels(state))
    check('occluded background pixels belong to target, without stealing focus')
    pid=s.CAPTURE_WORKER.proc.pid
    state2=observe()
    assert s.CAPTURE_WORKER.proc.pid==pid and state2['stateId']!=state['stateId']
    assert state2['screenshots'][0]['freshCaptureSession']
    check('capture worker reused while each request receives a new capture session')
    s.activate_window(window)
    pin_cover()
    state=observe()
    rejected(lambda:s.coordinate_action(window,state['stateId'],'click',x=x,y=y),'covered')
    check('WGC can see behind cover but clicks on covered coordinates remain rejected')
    state=observe()
    d.u.SetWindowPos(window['id'],None,r['left']+30,r['top']+20,r['width']+80,r['height']+40,0x14)
    rejected(lambda:s.coordinate_action(window,state['stateId'],'click',x=50,y=100),'moved/resized')
    state=observe();moved=d.rect(window['id'])
    assert pixels(state).shape[:2]==(moved['height'],moved['width'])
    assert state['screenshots'][0]['originX']==moved['left']
    check('move/resize rejects old coordinates and returns correctly mapped new frame')
    with patch.object(s.CAPTURE_WORKER,'capture',side_effect=RuntimeError('synthetic unavailable')):
        fallback=observe('auto')
        assert fallback['screenshots'][0]['captureMethod'] in {'PrintWindow','ForegroundScreen'}
        assert any('synthetic unavailable' in w for w in fallback['warnings'])
        rejected(lambda:observe('wgc'),'synthetic unavailable')
    check('automatic fallback is explicit; forced WGC does not silently fall back')
    d.u.ShowWindow(window['id'],6)
    state=s.get_window_state(window,include_text=False,include_screenshot=True)
    assert not state['screenshots'] and state['window']['minimized']
    check('minimized target never produces a misleading screenshot')
    s.CAPTURE_WORKER.close()
    assert s.CAPTURE_WORKER.proc is None
    check('capture worker shutdown releases the helper process')
    print(json.dumps({'ok':True,'checks':len(checks),'passed':checks},ensure_ascii=False,indent=2))
finally:
    if window and d.foreground() in {window['id'],cover['id'] if cover else None}:
        d.u.SetForegroundWindow(before);d.u.SetCursorPos(cursor.x,cursor.y)
    for proc in processes:
        proc.terminate()
        try:proc.wait(timeout=3)
        except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)
    s.UIA_WORKER.close();s.CAPTURE_WORKER.close()
