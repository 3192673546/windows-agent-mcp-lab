from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class JsonLineProcess:
    def __init__(self, command: list[str], cwd: Path):
        self.proc = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )
        assert self.proc.stdin and self.proc.stdout and self.proc.stderr
        self.q: queue.Queue[Any] = queue.Queue()
        self.backlog: dict[Any, list[dict[str, Any]]] = {}
        self.stderr_lines: list[str] = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout
        for raw in self.proc.stdout:
            line = raw.strip()
            if not line:
                continue
            try:
                self.q.put(json.loads(line))
            except json.JSONDecodeError:
                self.q.put({"_non_json_stdout": line})

    def _read_stderr(self) -> None:
        assert self.proc.stderr
        for raw in self.proc.stderr:
            self.stderr_lines.append(raw.rstrip())

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def raw(self, line: str) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def wait_id(self, request_id: Any, timeout: float = 20.0) -> dict[str, Any]:
        cached = self.backlog.get(request_id)
        if cached:
            return cached.pop(0)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                msg = self.q.get(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"process exited {self.proc.returncode}; stderr={self.stderr_lines[-20:]}")
                continue
            mid = msg.get("id") if isinstance(msg, dict) else None
            if mid == request_id:
                return msg
            self.backlog.setdefault(mid, []).append(msg)
        raise TimeoutError(f"timed out waiting for id={request_id}; stderr={self.stderr_lines[-20:]}")

    def request(self, request_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        return self.wait_id(request_id)

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass


def assert_true(condition: Any, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def result_value(message: dict[str, Any]) -> Any:
    return message.get("result", {}).get("structuredContent")


def tool_call(client: JsonLineProcess, request_id: int, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    return client.request(request_id, "tools/call", {"name": name, "arguments": arguments or {}})


def test_protocol_basics(client: JsonLineProcess, label: str) -> int:
    checks = 0
    client.raw("{this-is-not-json")
    parse_error = client.wait_id(None)
    assert_true(parse_error.get("error", {}).get("code") == -32700, f"{label}: malformed JSON did not return -32700")
    checks += 1

    client.send([])  # type: ignore[arg-type]
    invalid = client.wait_id(None)
    assert_true(invalid.get("error", {}).get("code") == -32600, f"{label}: invalid request did not return -32600")
    checks += 1

    init_latest = client.request(1, "initialize", {"protocolVersion": "1900-01-01", "capabilities": {}, "clientInfo": {"name": "regression", "version": "1"}})
    assert_true(init_latest.get("result", {}).get("protocolVersion") == "2025-11-25", f"{label}: unsupported protocol did not negotiate latest supported version")
    checks += 1

    init_legacy = client.request(2, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "regression", "version": "1"}})
    assert_true(init_legacy.get("result", {}).get("protocolVersion") == "2025-06-18", f"{label}: supported protocol was not preserved")
    checks += 1

    ping = client.request(3, "ping")
    assert_true(ping.get("result") == {}, f"{label}: ping failed")
    checks += 1

    unknown_method = client.request(4, "totally/unknown")
    assert_true(unknown_method.get("error", {}).get("code") == -32601, f"{label}: unknown method did not return -32601")
    checks += 1

    unknown_tool = tool_call(client, 5, "definitely_not_a_tool")
    assert_true(unknown_tool.get("error", {}).get("code") == -32602, f"{label}: unknown tool did not return -32602")
    checks += 1
    return checks


def test_browser() -> int:
    client = JsonLineProcess(["node", str(ROOT / "browser-mcp-lab" / "server.mjs")], ROOT / "browser-mcp-lab")
    checks = 0
    try:
        checks += test_protocol_basics(client, "browser")
        tools_msg = client.request(10, "tools/list")
        tools = tools_msg.get("result", {}).get("tools", [])
        names = {t.get("name") for t in tools}
        required = {"browser_launch", "tabs_list", "tab_new", "tab_get", "tab_goto", "tab_reload", "tab_back", "tab_forward", "tab_close", "ax_get", "ax_click", "ax_set_value", "ax_type_text", "ax_press_key", "tab_screenshot", "browser_close"}
        assert_true(required <= names, f"browser: missing tools {sorted(required - names)}")
        assert_true("browser_eval" not in names and "browser_click" not in names, "browser: raw eval/coordinate legacy tools leaked into public surface")
        assert_true(all(t.get("outputSchema", {}).get("type") == "object" for t in tools), "browser: every ChatGPT tool must expose an object outputSchema")
        checks += 3

        # Exercise a true simultaneous launch burst from a stopped server.
        ids = list(range(100, 108))
        for rid in ids:
            client.send({"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {"name": "browser_launch", "arguments": {"url": "about:blank"}}})
        launch_results = [client.wait_id(rid, timeout=30) for rid in ids]
        assert_true(all(not item.get("result", {}).get("isError", False) for item in launch_results), "browser: concurrent launch burst had errors")
        structured = [result_value(item) for item in launch_results]
        ports = {item.get("port") for item in structured if isinstance(item, dict)}
        assert_true(len(ports) == 1, f"browser: concurrent launch created multiple ports: {ports}")
        checks += 2

        shot = tool_call(client, 120, "tab_screenshot")
        content_types = [item.get("type") for item in shot.get("result", {}).get("content", [])]
        assert_true("image" in content_types, "browser: screenshot was not emitted as MCP image content")
        assert_true(bool(result_value(shot).get("bytes")), "browser: screenshot metadata missing byte size")
        checks += 2

        blocked = tool_call(client, 121, "tab_goto", {"url": "file:///C:/Windows/win.ini"})
        assert_true(blocked.get("result", {}).get("isError") is True, "browser: file:// navigation was not rejected")
        checks += 1

        closed = tool_call(client, 122, "browser_close")
        assert_true(result_value(closed).get("closed") is True, "browser: close failed")
        checks += 1

        # Crash-recovery: kill the actual process that owns the CDP listen port.
        launch = tool_call(client, 123, "browser_launch", {"url": "about:blank"})
        launch_value = result_value(launch)
        crash_port = int(launch_value["port"])
        owner_query = subprocess.run(
            [
                "pwsh.exe", "-NoLogo", "-NoProfile", "-Command",
                f"$c=Get-NetTCPConnection -State Listen -LocalPort {crash_port} -ErrorAction Stop | Select-Object -First 1; $c.OwningProcess",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
        assert_true(owner_query.returncode == 0 and owner_query.stdout.strip().isdigit(), f"browser: could not resolve CDP owner for port {crash_port}: {owner_query.stderr}")
        owner_pid = int(owner_query.stdout.strip())
        subprocess.run(
            ["taskkill.exe", "/PID", str(owner_pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
        time.sleep(0.8)
        dead_tabs = tool_call(client, 124, "tabs_list")
        assert_true(dead_tabs.get("result", {}).get("isError") is True, "browser: calls did not fail closed after forced browser crash")
        recovered = tool_call(client, 125, "browser_launch", {"url": "about:blank"})
        recovered_value = result_value(recovered)
        assert_true(not recovered.get("result", {}).get("isError", False), f"browser: relaunch after forced crash failed: {recovered}")
        assert_true(int(recovered_value.get("port") or 0) > 0 and int(recovered_value["port"]) != crash_port, "browser: crash recovery reused the dead CDP endpoint")
        recovered_tabs = tool_call(client, 126, "tabs_list")
        assert_true(bool((result_value(recovered_tabs) or {}).get("tabs")), "browser: no usable tab after crash recovery")
        checks += 4
    finally:
        try:
            tool_call(client, 199, "browser_close")
        except Exception:
            pass
        client.close()
    return checks


def raw_uia_windows() -> list[dict[str, Any]]:
    proc = subprocess.run(
        ["pwsh.exe", "-NoLogo", "-NoProfile", "-File", str(ROOT / "computer-use-mcp-lab" / "uia_helper.ps1"), "-Operation", "list"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=CREATE_NO_WINDOW,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    value = json.loads(proc.stdout.strip() or "[]")
    return value if isinstance(value, list) else [value]


def test_computer_use() -> int:
    computer_dir = ROOT / "computer-use-mcp-lab"
    client = JsonLineProcess([sys.executable, str(computer_dir / "server.py")], computer_dir)
    target: subprocess.Popen[Any] | None = None
    checks = 0
    before_temp_shots = {p.name for p in (computer_dir / "artifacts").glob("state-*.png")}
    try:
        checks += test_protocol_basics(client, "computer")
        tools_msg = client.request(10, "tools/list")
        tools = tools_msg.get("result", {}).get("tools", [])
        names = {t.get("name") for t in tools}
        expected = {"list_windows", "get_window", "restore_window", "get_window_state", "click", "set_value"}
        assert_true(names == expected, f"computer: unexpected public tool surface {sorted(names)}")
        assert_true(all(t.get("outputSchema", {}).get("type") == "object" for t in tools), "computer: every ChatGPT tool must expose an object outputSchema")
        click_schema = next(t for t in tools if t.get("name") == "click").get("inputSchema", {})
        assert_true(click_schema.get("required") == ["window", "element_index"], "computer: click does not require semantic element_index")
        assert_true("x" not in click_schema.get("properties", {}) and "y" not in click_schema.get("properties", {}), "computer: coordinate input leaked into tool schema")
        assert_true(not ({"activate_window", "press_key", "drag", "type_text", "perform_secondary_action", "ui_invoke", "ui_set_value"} & names), "computer: foreground/legacy tools leaked into public surface")
        restore_schema = next(t for t in tools if t.get("name") == "restore_window").get("inputSchema", {})
        assert_true("window" in restore_schema.get("properties", {}), "computer: restore_window does not require a target window")
        checks += 6

        listed = tool_call(client, 11, "list_windows")
        windows = (result_value(listed) or {}).get("windows", [])
        blocked_apps = {"process:msedge", "process:chrome", "process:firefox", "process:ChatGPT", "process:WindowsTerminal", "process:cmd", "process:powershell", "process:pwsh"}
        leaked = [w for w in windows if w.get("app") in blocked_apps and not str(w.get("title") or "").startswith("Computer Use MCP Lab Target")]
        assert_true(not leaked, f"computer: blocked windows leaked from list_windows: {leaked}")
        checks += 1

        raw = raw_uia_windows()
        blocked_processes = {"msedge", "chrome", "firefox", "chatgpt", "windowsterminal", "cmd", "powershell", "pwsh", "sechealthui", "credentialuibroker", "logonui"}
        blocked_raw = next((row for row in raw if str(row.get("processName") or "").lower() in blocked_processes and not str(row.get("name") or "").startswith("Computer Use MCP Lab Target") and int(row.get("nativeWindowHandle") or 0) > 0), None)
        if blocked_raw:
            denied = tool_call(client, 12, "get_window", {"window": {"id": int(blocked_raw["nativeWindowHandle"]), "app": f"process:{blocked_raw.get('processName')}", "title": blocked_raw.get("name") or ""}})
            assert_true(denied.get("result", {}).get("isError") is True and "refuses this target" in denied.get("result", {}).get("content", [{}])[0].get("text", ""), "computer: direct blocked-window handle bypass succeeded")
            checks += 1

        target = subprocess.Popen(
            ["pwsh.exe", "-NoLogo", "-NoProfile", "-File", str(computer_dir / "test_target.ps1")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
        target_window = None
        for attempt in range(30):
            listed = tool_call(client, 30 + attempt, "list_windows")
            target_window = next((w for w in (result_value(listed) or {}).get("windows", []) if str(w.get("title") or "").startswith("Computer Use MCP Lab Target")), None)
            if target_window:
                break
            time.sleep(0.1)
        assert_true(target_window is not None, "computer: background test window was not discoverable")
        checks += 1

        restored = tool_call(client, 69, "restore_window", {"window": target_window})
        restored_value = result_value(restored)
        assert_true(not restored.get("result", {}).get("isError", False), "computer: restore_window failed on safe target")
        assert_true(restored_value.get("verification", {}).get("foregroundUnchanged") is True, "computer: restore_window did not prove foreground preservation")
        assert_true(restored_value.get("requiresReobserve") is True, "computer: restore_window did not require re-observation")
        checks += 3

        state = tool_call(client, 70, "get_window_state", {"window": target_window, "include_screenshot": True, "include_text": True, "max_elements": 100})
        assert_true(not state.get("result", {}).get("isError", False), "computer: get_window_state failed")
        types = [item.get("type") for item in state.get("result", {}).get("content", [])]
        assert_true("image" in types, "computer: screenshot was not emitted as MCP image content")
        structured = result_value(state)
        assert_true(structured.get("observation", {}).get("quality") == "semantic", "computer: lab target did not produce semantic observation quality")
        public_elements = structured["accessibility"].get("elements") or []
        assert_true(any("set_value" in (item.get("actions") or []) for item in public_elements), "computer: editable element did not advertise set_value capability")
        assert_true(any("click" in (item.get("actions") or []) for item in public_elements), "computer: button element did not advertise click capability")
        tree = structured["accessibility"]["tree"].splitlines()
        assert_true(not any("automationId='Close'" in line or "automationId='Minimize'" in line or "automationId='Maximize'" in line for line in tree), "computer: system titlebar controls leaked into semantic tree")
        entry_index = next(i for i, line in enumerate(tree) if "automationId='entry'" in line)
        button_index = next(i for i, line in enumerate(tree) if "automationId='applyButton'" in line)
        checks += 6

        unicode_value = "协议回归-测试-🙂-" + ("长文本" * 200)
        set_result = tool_call(client, 71, "set_value", {"window": target_window, "element_index": entry_index, "value": unicode_value})
        assert_true(not set_result.get("result", {}).get("isError", False), f"computer: set_value failed for Unicode/long text: {set_result}")
        set_value_result = result_value(set_result)
        assert_true(set_value_result.get("requiresReobserve") is True and set_value_result.get("verification", {}).get("outcomeVerified") is True, "computer: set_value did not return verified/reobserve semantics")
        stale = tool_call(client, 72, "click", {"window": target_window, "element_index": button_index})
        assert_true(stale.get("result", {}).get("isError") is True and "fresh accessibility observation" in stale.get("result", {}).get("content", [{}])[0].get("text", ""), "computer: stale element_index was accepted")
        checks += 3

        fresh = tool_call(client, 73, "get_window_state", {"window": target_window, "include_screenshot": False, "include_text": True, "max_elements": 100})
        fresh_tree = result_value(fresh)["accessibility"]["tree"].splitlines()
        fresh_button = next(i for i, line in enumerate(fresh_tree) if "automationId='applyButton'" in line)
        clicked = tool_call(client, 74, "click", {"window": target_window, "element_index": fresh_button})
        assert_true(not clicked.get("result", {}).get("isError", False), "computer: semantic click failed")
        clicked_value = result_value(clicked)
        assert_true(clicked_value.get("requiresReobserve") is True and clicked_value.get("verification", {}).get("outcomeVerified") is False, "computer: click did not distinguish delivery from application-level verification")
        current = None
        for attempt in range(20):
            current = tool_call(client, 200 + attempt, "get_window", {"window": target_window})
            if str(result_value(current).get("title") or "").startswith("Computer Use MCP Lab Target - Hello, 协议回归-测试-🙂"):
                break
            time.sleep(0.05)
        assert_true(current is not None and str(result_value(current).get("title") or "").startswith("Computer Use MCP Lab Target - Hello, 协议回归-测试-🙂"), "computer: semantic click did not apply long Unicode value within 1s")
        checks += 3

        # Read-only polling stress: repeated discovery must not crash or mutate desktop state.
        for rid in range(80, 100):
            response = tool_call(client, rid, "list_windows")
            assert_true(not response.get("result", {}).get("isError", False), f"computer: repeated list_windows failed at id={rid}: {response}")
        checks += 20

        # Window-disappearance race: a fresh observation must not authorize an
        # action after the target process/window has already disappeared.
        disappearing = tool_call(client, 300, "get_window_state", {"window": target_window, "include_screenshot": False, "include_text": True, "max_elements": 100})
        disappearing_tree = result_value(disappearing)["accessibility"]["tree"].splitlines()
        disappearing_entry = next(i for i, line in enumerate(disappearing_tree) if "automationId='entry'" in line)
        target.terminate()
        target.wait(timeout=3)
        target = None
        time.sleep(0.15)
        vanished_action = tool_call(client, 301, "set_value", {"window": target_window, "element_index": disappearing_entry, "value": "must-not-apply"})
        assert_true(vanished_action.get("result", {}).get("isError") is True, "computer: stale observation acted on a window that had already disappeared")
        survived = tool_call(client, 302, "list_windows")
        assert_true(not survived.get("result", {}).get("isError", False), "computer: server did not survive target-window disappearance")
        checks += 2
    finally:
        if target is not None:
            try:
                target.terminate()
                target.wait(timeout=3)
            except Exception:
                try:
                    target.kill()
                except Exception:
                    pass
        client.close()

    after_temp_shots = {p.name for p in (computer_dir / "artifacts").glob("state-*.png")}
    assert_true(after_temp_shots <= before_temp_shots, f"computer: temporary state screenshots leaked on disk: {sorted(after_temp_shots - before_temp_shots)}")
    checks += 1
    return checks


def main() -> int:
    started = time.monotonic()
    browser_checks = test_browser()
    print(f"BROWSER_PROTOCOL_REGRESSION PASS checks={browser_checks}")
    computer_checks = test_computer_use()
    print(f"COMPUTER_PROTOCOL_REGRESSION PASS checks={computer_checks}")
    print(json.dumps({"ok": True, "browserChecks": browser_checks, "computerChecks": computer_checks, "totalChecks": browser_checks + computer_checks, "seconds": round(time.monotonic() - started, 2)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

