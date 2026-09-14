"""Exercise real UIA and SendInput against a disposable lab app, never user documents."""
import ctypes
import json
import subprocess
import time
from pathlib import Path
import server as s
import desktop_input as d
checks=[]
def passed(name):
    checks.append(name); print("PASS "+name,flush=True)
def reject(fn, needle):
    try: fn()
    except Exception as exc:
        assert needle in str(exc), str(exc)
    else: raise AssertionError("Operation unexpectedly succeeded.")
target=subprocess.Popen(["pwsh.exe","-NoLogo","-NoProfile","-File",str(Path(__file__).with_name("test_target.ps1")),"-Visible"],
    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=s.CREATE_NO_WINDOW)
before=d.foreground()
s.LAB_PROCESS_IDS.add(target.pid)
cursor=ctypes.wintypes.POINT()
d.u.GetCursorPos(ctypes.byref(cursor))
window=None
try:
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        s._invalidate_window_list_cache()
        window=next((w for w in s.list_windows() if w["title"]=="Computer Use MCP Lab Target"),None)
        if window: break
        time.sleep(.1)
    assert window, "No test window"
    def observe(shot=False):
        return s.get_window_state(window,include_screenshot=shot,include_text=True,settle_ms=80)
    def element(state,aid):
        return next(e for e in state["accessibility"]["elements"] if e["automationId"]==aid)
    state=observe()
    assert all(element(state,aid) for aid in ["entry","applyButton","check","slider"]);passed("all sibling controls discovered")
    e=element(state,"check")
    assert "toggle" in e["actions"], e
    result=s.semantic_action(window,e["index"],"toggle")
    assert result["observedValue"]=="On",result
    passed("UIA toggle state changed")
    state=observe();e=element(state,"slider")
    assert "set_value" in e["actions"],e
    result=s.semantic_action(window,e["index"],"set_value","65")
    assert str(result["observedValue"])=="65",result;passed("UIA slider value changed")
    state=observe();e=element(state,"entry")
    result=s.semantic_action(window,e["index"],"set_value","UIA 文本")
    assert result["observedValue"]=="UIA 文本",result;passed("UIA ValuePattern read-back")
    reject(lambda:s.semantic_action(window,e["index"],"set_value","stale"),"fresh accessibility observation")
    passed("stale UIA reference rejected")
    s.activate_window(window)
    state=observe(True)
    assert state["screenshots"][0]["captureMethod"]=="WindowsGraphicsCapture";passed("foreground activation and screen capture")
    def coords(state,aid):
        e=element(state,aid);r=e["rect"];shot=state["screenshots"][0]
        return dict(x=r["left"]-shot["originX"]+r["width"]/2,y=r["top"]-shot["originY"]+r["height"]/2)
    s.coordinate_action(window,state["stateId"],"click",**coords(state,"entry"))
    reject(lambda:s.coordinate_action(window,state["stateId"],"click",**coords(state,"entry")),"fresh window state")
    passed("screenshot click and stale snapshot rejection")
    observe();s.keyboard_action(window,"press_key","Control+a")
    observe();s.keyboard_action(window,"type_text","兼容测试🙂")
    state=observe(True)
    assert element(state,"entry")["value"]=="兼容测试🙂",element(state,"entry")
    passed("SendInput Unicode with actual editor read-back")
    reject(lambda:s.coordinate_action(window,state["stateId"],"click",x=-1,y=10),"outside screenshot")
    passed("out-of-bounds input rejected")
    state=observe(True)
    s.coordinate_action(window,state["stateId"],"click",**coords(state,"applyButton"))
    state=observe()
    assert "Hello, 兼容测试🙂" in state["window"]["title"],state["window"]
    passed("coordinate button click verified through application title")
    state=observe(True);p=coords(state,"slider")
    s.coordinate_action(window,state["stateId"],"drag",from_x=p["x"]-50,from_y=p["y"],to_x=p["x"]+50,to_y=p["y"],duration_ms=120)
    state=observe(True)
    assert not (d.u.GetAsyncKeyState(1)&0x8000),"Left mouse remained pressed"
    passed("drag releases left mouse button")
    state=observe(True)
    result=s.call_tool("click_at",dict(window=window,screenshot_id=state["stateId"],**coords(state,"entry")))
    result=s.call_tool("press_key",dict(window=window,state_id=result["state"]["stateId"],key="Control+a"))
    result=s.call_tool("type_text",dict(window=window,state_id=result["state"]["stateId"],text="连续输入🙂"))
    state=result["state"]
    assert element(state,"entry")["value"]=="连续输入🙂"
    result=s.call_tool("click",dict(window=window,state_id=state["stateId"],element_index=element(state,"applyButton")["index"]))
    assert "Hello, 连续输入🙂" in result["state"]["window"]["title"]
    passed("coordinate-keyboard-semantic action chain uses returned states with actual read-back")
    print(json.dumps({"ok":True,"checks":len(checks),"passed":checks},ensure_ascii=False,indent=2))
finally:
    if window and d.foreground()==window["id"]:
        d.u.SetForegroundWindow(before)
        d.u.SetCursorPos(cursor.x,cursor.y)
    target.terminate()
    try: target.wait(timeout=3)
    except subprocess.TimeoutExpired: target.kill()
    s.UIA_WORKER.close()
    s.CAPTURE_WORKER.close()
