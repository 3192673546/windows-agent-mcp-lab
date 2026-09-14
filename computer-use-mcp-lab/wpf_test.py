import json, subprocess, time
from pathlib import Path
import server as s
target=subprocess.Popen(["pwsh.exe","-NoLogo","-NoProfile","-STA","-File",str(Path(__file__).with_name("test_target_wpf.ps1"))],
 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=s.CREATE_NO_WINDOW)
s.LAB_PROCESS_IDS.add(target.pid)
checks=[]
try:
 deadline=time.monotonic()+10;window=None
 while time.monotonic()<deadline:
  s._invalidate_window_list_cache()
  window=next((w for w in s.list_windows() if w["title"]=="Computer Use MCP Lab Target WPF Ready"),None)
  if window:break
  time.sleep(.1)
 assert window,"WPF test window missing"
 def observe():
  return s.get_window_state(window,include_screenshot=False,settle_ms=60)
 def control(aid):
  state=observe();e=next(e for e in state["accessibility"]["elements"] if e["automationId"]==aid)
  return e
 def run(aid,action,value=""):
  e=control(aid)
  result=s.semantic_action(window,e["index"],action,value)
  checks.append(action); print("PASS WPF "+action,flush=True)
  return result
 e=control("entry")
 raw=s.STATE_CACHE[window["id"]]["elements"][e["index"]]
 assert not raw["nativeWindowHandle"],raw
 # Existing public set_value/click must now work without a native HWND.
 s.set_value(window,e["index"],"无句柄🙂")
 observed=control("entry")
 assert observed["value"]=="无句柄🙂",observed
 checks.append("set_value without HWND")
 e=control("button")
 assert not s.STATE_CACHE[window["id"]]["elements"][e["index"]]["nativeWindowHandle"]
 s.click(window,e["index"])
 assert "无句柄🙂" in observe()["window"]["title"];checks.append("click without HWND")
 assert run("check","toggle")["observedValue"]=="On"
 assert run("slider","set_range_value","73")["observedValue"]==73
 assert run("expander","expand")["observedValue"]=="Expanded"
 assert run("expander","collapse")["observedValue"]=="Collapsed"
 assert run("choiceB","select")["observedValue"] is True
 print(json.dumps({"ok":True,"checks":len(checks),"passed":checks},ensure_ascii=False,indent=2))
finally:
 target.terminate()
 try:target.wait(timeout=3)
 except subprocess.TimeoutExpired:target.kill()
 s.UIA_WORKER.close()
