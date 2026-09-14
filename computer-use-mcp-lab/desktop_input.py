"""Windows input backend. Only called after the MCP server validates an observed target."""
import ctypes
from ctypes import wintypes as W
import math
import time

u = ctypes.WinDLL("user32", use_last_error=True)
ULONG_PTR = ctypes.c_size_t

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx",W.LONG),("dy",W.LONG),("mouseData",W.DWORD),("dwFlags",W.DWORD),("time",W.DWORD),("dwExtraInfo",ULONG_PTR)]
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk",W.WORD),("wScan",W.WORD),("dwFlags",W.DWORD),("time",W.DWORD),("dwExtraInfo",ULONG_PTR)]
class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg",W.DWORD),("wParamL",W.WORD),("wParamH",W.WORD)]
class INPUTUNION(ctypes.Union):
    _fields_ = [("mi",MOUSEINPUT),("ki",KEYBDINPUT),("hi",HARDWAREINPUT)]
class INPUT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type",W.DWORD),("value",INPUTUNION)]
class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize",W.DWORD),("flags",W.DWORD),("hwndActive",W.HWND),("hwndFocus",W.HWND),
                ("hwndCapture",W.HWND),("hwndMenuOwner",W.HWND),("hwndMoveSize",W.HWND),("hwndCaret",W.HWND),("rcCaret",W.RECT)]
u.SendInput.argtypes=[W.UINT,ctypes.POINTER(INPUT),ctypes.c_int]; u.SendInput.restype=W.UINT
u.GetForegroundWindow.restype=W.HWND
u.GetWindowRect.argtypes=[W.HWND,ctypes.POINTER(W.RECT)]
u.SetForegroundWindow.argtypes=[W.HWND]; u.SetForegroundWindow.restype=W.BOOL
u.ShowWindow.argtypes=[W.HWND,ctypes.c_int]
u.IsIconic.argtypes=[W.HWND]; u.IsWindow.argtypes=[W.HWND]
u.GetAncestor.argtypes=[W.HWND,W.UINT]; u.GetAncestor.restype=W.HWND
u.WindowFromPoint.argtypes=[W.POINT]; u.WindowFromPoint.restype=W.HWND
u.GetWindowThreadProcessId.argtypes=[W.HWND,ctypes.POINTER(W.DWORD)]; u.GetWindowThreadProcessId.restype=W.DWORD
u.GetGUIThreadInfo.argtypes=[W.DWORD,ctypes.POINTER(GUITHREADINFO)]; u.GetGUIThreadInfo.restype=W.BOOL
try:
    u.SetProcessDpiAwarenessContext.argtypes=[W.HANDLE]
    u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except (AttributeError,OSError):
    pass

def rect(hwnd):
    r=W.RECT()
    if not u.IsWindow(hwnd) or not u.GetWindowRect(hwnd,ctypes.byref(r)): raise RuntimeError("Target window disappeared.")
    return dict(left=r.left,top=r.top,right=r.right,bottom=r.bottom,width=r.right-r.left,height=r.bottom-r.top)

def foreground():
    return int(u.GetForegroundWindow() or 0)

def require_foreground(hwnd):
    if foreground()!=hwnd: raise RuntimeError("Target is not foreground. Call activate_window, then reobserve before input.")

def activate(hwnd):
    if u.IsIconic(hwnd): u.ShowWindow(hwnd,9)
    else: u.ShowWindow(hwnd,5)
    u.SetForegroundWindow(hwnd)
    deadline=time.monotonic()+1
    while foreground()!=hwnd and time.monotonic()<deadline: time.sleep(.025)
    require_foreground(hwnd)

def focus(hwnd):
    thread=u.GetWindowThreadProcessId(hwnd,None)
    info=GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    if not thread or not u.GetGUIThreadInfo(thread,ctypes.byref(info)): raise RuntimeError("Cannot verify target keyboard focus.")
    return int(info.hwndFocus or 0)

def send(items):
    batch=(INPUT*len(items))(*items)
    count=u.SendInput(len(batch),batch,ctypes.sizeof(INPUT))
    if count!=len(batch): raise RuntimeError("Windows rejected or partially delivered input; reobserve. Higher integrity apps may block input.")

def key_event(vk=0,scan=0,flags=0):
    return INPUT(type=1,ki=KEYBDINPUT(vk,scan,flags,0,0))

def mouse_event(flags,data=0,x=0,y=0):
    return INPUT(type=0,mi=MOUSEINPUT(x,y,data & 0xffffffff,flags,0,0))

def point(hwnd,x,y):
    require_foreground(hwnd)
    r=rect(hwnd)
    if isinstance(x,bool) or isinstance(y,bool) or not isinstance(x,(float,int)) or not isinstance(y,(float,int)) or not math.isfinite(x) or not math.isfinite(y):
        raise ValueError("Coordinates must be finite numbers.")
    if not 0<=x<r["width"] or not 0<=y<r["height"]: raise ValueError("Point is outside screenshot bounds.")
    sx,sy=round(r["left"]+x),round(r["top"]+y)
    hit=u.WindowFromPoint(W.POINT(sx,sy))
    if int(u.GetAncestor(hit,2) or 0)!=hwnd: raise RuntimeError("Point is covered by another window or outside the desktop; reobserve.")
    return sx,sy

def move_screen(hwnd,sx,sy):
    require_foreground(hwnd)
    left,top,width,height=(u.GetSystemMetrics(i) for i in (76,77,78,79))
    if width<=1 or height<=1: raise RuntimeError("Desktop dimensions unavailable.")
    send([mouse_event(0x8000|0x4000|0x0001,x=round((sx-left)*65535/(width-1)),y=round((sy-top)*65535/(height-1)))])

def click(hwnd,x,y,button="left",click_count=1):
    if button not in ("left","right","middle") or type(click_count) is not int or click_count not in (1,2): raise ValueError("Invalid click options.")
    sx,sy=point(hwnd,x,y); move_screen(hwnd,sx,sy)
    down,up={"left":(2,4),"right":(8,16),"middle":(32,64)}[button]
    for _ in range(click_count):
        require_foreground(hwnd)
        try: send([mouse_event(down),mouse_event(up)])
        except Exception:
            send([mouse_event(up)])
            raise

def scroll(hwnd,x,y,delta_y=0,delta_x=0):
    if any(type(v) is not int or abs(v)>12000 for v in (delta_x,delta_y)): raise ValueError("Wheel deltas must be integers within +/-12000.")
    sx,sy=point(hwnd,x,y); move_screen(hwnd,sx,sy)
    items=[]
    if delta_y: items.append(mouse_event(0x0800,-delta_y))
    if delta_x: items.append(mouse_event(0x1000,delta_x))
    if items: require_foreground(hwnd); send(items)

def drag(hwnd,from_x,from_y,to_x,to_y,duration_ms=500):
    if type(duration_ms) is not int or not 100<=duration_ms<=2000: raise ValueError("duration_ms must be 100..2000.")
    start=point(hwnd,from_x,from_y); end=point(hwnd,to_x,to_y)
    move_screen(hwnd,*start)
    try:
        require_foreground(hwnd); send([mouse_event(2)])
        for i in range(1,21):
            require_foreground(hwnd)
            sx=round(start[0]+(end[0]-start[0])*i/20); sy=round(start[1]+(end[1]-start[1])*i/20)
            r=rect(hwnd)
            point(hwnd,sx-r["left"],sy-r["top"])
            move_screen(hwnd,sx,sy); time.sleep(duration_ms/20000)
    finally:
        # Always release our pressed button, even when the user changes focus.
        send([mouse_event(4)])

def type_text(hwnd,text,expected_focus):
    if not isinstance(text,str) or len(text)>100000: raise ValueError("Text must be a string of at most 100000 characters.")
    data=text.encode("utf-16-le")
    for offset in range(0,len(data),64):
        require_foreground(hwnd)
        if focus(hwnd)!=expected_focus: raise RuntimeError("Keyboard focus changed during typing; reobserve before continuing.")
        events=[]
        for i in range(offset,min(offset+64,len(data)),2):
            unit=int.from_bytes(data[i:i+2],"little")
            events += [key_event(scan=unit,flags=4),key_event(scan=unit,flags=4|2)]
        send(events)

def press_key(hwnd,key,expected_focus):
    aliases={"Enter":13,"Return":13,"Tab":9,"Escape":27,"BackSpace":8,"Backspace":8,"Delete":46,
      "Home":36,"End":35,"PageUp":33,"PageDown":34,"Up":38,"Down":40,"Left":37,"Right":39,"Space":32}
    parts=key.split("+")
    modifiers=[]
    for p in parts[:-1]:
        if p not in ("Control","Ctrl","Alt","Shift"): raise ValueError("Unsupported modifier.")
        modifiers.append({"Control":17,"Ctrl":17,"Alt":18,"Shift":16}[p])
    name=parts[-1]
    vk=aliases.get(name) or (ord(name.upper()) if len(name)==1 and name.isascii() and name.isalnum() else None)
    if not vk: raise ValueError("Unsupported key. Use type_text for literal Unicode text.")
    require_foreground(hwnd)
    if focus(hwnd)!=expected_focus: raise RuntimeError("Keyboard focus changed; reobserve.")
    events=[key_event(vk=m) for m in modifiers]+[key_event(vk=vk),key_event(vk=vk,flags=2)]+[key_event(vk=m,flags=2) for m in reversed(modifiers)]
    try: send(events)
    except Exception:
        send([key_event(vk=vk,flags=2)]+[key_event(vk=m,flags=2) for m in reversed(modifiers)])
        raise
