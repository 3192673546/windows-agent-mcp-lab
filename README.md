# Windows Agent MCP Lab

面向 Windows 本地 AI Agent 的 MCP / Computer Use / Coding Tooling 实验仓库。

这个仓库整理了我在本地 Agent 工具链上的几组工程实践，重点不是“封装几个 MCP 工具”，而是处理真实自动化系统里更难的部分：**状态一致性、可验证执行、安全边界、Windows UI 自动化、浏览器交互、长进程管理与回归测试**。

## 项目组成

### 1. Browser MCP Lab

**Node.js · CDP · Accessibility Tree · MCP**

为隔离的 Edge / Chromium 会话提供浏览器自动化能力。

- 使用 Accessibility Tree 进行结构化观察与元素操作。
- 每次输入或导航后使旧状态失效，通过 `stateId` / `screenshotId` 防止旧索引误操作新页面。
- 支持语义点击、文本输入、真实鼠标事件、滚动、拖拽、等待条件与截图。
- 对危险 URL、失效截图、跨 Tab 状态复用等场景进行显式拒绝。
- 自测覆盖 12 个核心流程。

### 2. Computer Use MCP Lab

**Python · Windows UI Automation · Windows.Graphics.Capture · MCP**

针对 Windows 桌面应用实现 observe-act-verify 自动化链路。

- 结合 Win32 / UIA 结构化观察与截图坐标操作。
- 使用 `stateId`、窗口身份、前台状态、截图时效等约束阻止 stale action。
- 对密码输入框、被遮挡坐标、窗口变化、焦点变化等风险场景做安全拒绝。
- 使用 Windows.Graphics.Capture 获取指定 HWND 画面，并提供兼容性回退路径。
- 自测覆盖 8 个核心流程，并包含 WPF、性能和截图回归测试。

### 3. Local Coding Agent

**Python · PowerShell · ConPTY · Git · Apply Patch**

供 ChatGPT / MCP 客户端调用的 Windows 本地编程工具服务。

- 支持 PowerShell、Git、Python、Node 等命令执行及长进程会话管理。
- 使用 ConPTY 提供交互终端，支持分页读取输出、stdin 输入与进程树终止。
- 实现 workspace 规则解析、cwd 隔离、输出完整性与失败状态反馈。
- `apply_patch` 支持 dry-run、移动 / 删除 / 更新文件与失败恢复策略。
- 完整测试覆盖 Unicode、CRLF、长命令、进程会话、补丁与规则加载行为。

> `local-coding-agent/openai_apply_diff.py` 来自 OpenAI Agents SDK 0.22.2 的 `agents/apply_diff.py`，保持原算法并按 MIT License 分发；许可证见同目录 `LICENSE.openai-agents`。其余工程代码与适配逻辑为本仓库整理的本地实验实现。

### 4. MCP Safety / Protocol Regression

用于验证接口兼容、安全边界和行为回归的测试集合。

- 保存原始协议基线并执行行为对比。
- 回归 stale state、错误传播、进程状态、工具 schema 等关键路径。
- 避免“工具调用成功 = 任务成功”的假阳性，强调最终状态验证。

### 5. ChatGPT 连接器接入

**OpenAI Secure MCP Tunnel · PowerShell · Windows 进程管理**

把上面的 MCP 服务，以及两个第三方 MCP 服务（CursorTouch Windows-MCP、browser-use browser-harness），通过 OpenAI 官方 `tunnel-client` 注册为 ChatGPT 连接器。详见 [`chatgpt-tunnel/`](chatgpt-tunnel/)。

- 服务只监听 `127.0.0.1`，隧道只建立出站连接；配置文件只写 `file:` / `env:` 形式的密钥引用。
- 启动脚本在端口监听或 `/readyz` 就绪之后才报告成功；停止脚本先核对 PID 属于本服务，再结束整个进程树。
- 第三方项目只提供接入脚本，不包含上游源码。

## 设计原则

1. **Observe → Act → Verify**：任何写操作都必须基于新鲜观察，动作之后重新确认状态。
2. **Fail Closed**：目标身份、窗口、截图、焦点或状态不一致时拒绝继续执行。
3. **No Blind Retry**：写操作完成但观察失败时只重新观察，不自动重复可能产生副作用的动作。
4. **Least Capability**：Browser / Computer Use 服务不暴露任意 shell；Coding Agent 单独承载代码执行能力。
5. **Evidence-based Completion**：进程启动、鼠标点击或输入送达不等价于业务任务完成。

## 本地验证

```powershell
# Browser MCP
cd browser-mcp-lab
node server.mjs --self-test

# Computer Use MCP
cd ..\computer-use-mcp-lab
python server.py --self-test

# Local Coding Agent
cd ..\local-coding-agent
python -X utf8 test_server.py
```

整理发布前，本机结果：Browser MCP 12/12 self-test 通过，Computer Use MCP 8/8 self-test 通过，Local Coding Agent 测试通过。

> Local Coding Agent 优先使用 PowerShell 7（`pwsh`）执行命令。如果只有 Windows PowerShell 5.1，38 项测试中有 4 项会失败（`python_unicode`、`pipe_interactive_input`、`conpty_interaction`、`kill_process_tree`），原因是 5.1 向原生程序传参时会丢掉内嵌的双引号。

## 安全说明

公开仓库不包含：

- API Token / Cookie / 登录凭据
- `secrets/`、运行态 `runtime/`、截图/测试产物 `artifacts/`
- 浏览器用户 Profile
- 真实的 Tunnel ID、Runtime API Key，以及生成的 tunnel profile（仓库只提供 `tunnel-profile.example.yaml` 模板）
- Python 虚拟环境、依赖缓存和二进制运行文件（包括 `tunnel-client`）

这些内容只存在于本地运行环境中。

---

<details>
<summary><b>English summary</b></summary>

A Windows-focused Agent tooling lab covering MCP browser automation, desktop Computer Use, local coding tools and protocol/safety regression tests. The projects focus on stale-state prevention, observe-act-verify workflows, fail-closed safety checks, Windows UI automation, CDP browser control, long-running process sessions and testable tool execution.

`chatgpt-tunnel/` holds the scripts that expose these servers — plus the third-party CursorTouch Windows-MCP and browser-use browser-harness — to ChatGPT Connectors through OpenAI's Secure MCP Tunnel, with loopback-only servers, secret references instead of inline keys, and PID-verified process-tree shutdown.

`local-coding-agent/openai_apply_diff.py` is derived unchanged from OpenAI Agents SDK 0.22.2 and distributed under its MIT license. Other lab code and integrations are presented as local experimental implementations.

</details>
