# ChatGPT 连接器接入（OpenAI Secure MCP Tunnel）

把本机的 MCP 服务注册为 ChatGPT 连接器（Connectors）的启动、配置与运维脚本。

所有 MCP 服务都只监听 `127.0.0.1`，由 OpenAI 官方 `tunnel-client` 建立**出站**隧道连接到 ChatGPT，本机不开放任何入站端口。

## 接入的服务

| 服务 | 来源 | MCP 传输 | 脚本位置 | Runtime API Key 的处理方式 |
|---|---|---|---|---|
| Browser MCP Lab | 本仓库 | stdio | [`../browser-mcp-lab/`](../browser-mcp-lab/) | profile 中以 `file:` 引用 `secrets/` |
| Computer Use MCP Lab | 本仓库 | stdio | [`../computer-use-mcp-lab/`](../computer-use-mcp-lab/) | 同上 |
| Local Coding Agent | 本仓库 | stdio，健康检查 `:8081` | [`../local-coding-agent/`](../local-coding-agent/) | 同上 |
| Windows-MCP | 第三方：[CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP) 0.8.5（MIT） | Streamable HTTP `127.0.0.1:8000` | [`windows-mcp/`](windows-mcp/) | `file:` 引用 `secrets/`，不另写副本 |
| browser-harness | 第三方：[browser-use/browser-harness](https://github.com/browser-use/browser-harness) 0.1.13（MIT） | stdio，状态页 `:8765` | [`browser-harness/`](browser-harness/) | 每次启动时输入，只存在于进程环境变量，不落盘 |

两个第三方项目这里只提供**接入脚本**，不包含它们的源码；请从上游安装。

## 准备工作

1. **tunnel-client v0.0.14**（OpenAI 发布，Apache-2.0）。Windows x64 包为 `tunnel-client-v0.0.14-windows-amd64.zip`，请用发布附带的 `SHA256SUMS` 校验：
   ```text
   784ab8da7b5a88f0109f1fd8aaf0a1c86067430b896dddf307ef7e3cc49fa1a5  tunnel-client-v0.0.14-windows-amd64.zip
   ```
   解压位置：
   - 三个 lab：`<lab>\tunnel-client-v0.0.14-windows-amd64\tunnel-client.exe`
   - Windows-MCP / browser-harness：`<安装目录>\tunnel-client\bin\tunnel-client.exe`
2. **Tunnel ID**：在 <https://platform.openai.com/settings/organization/tunnels> 创建或查看，格式为 `tunnel_` + 32 位小写十六进制。
3. **Runtime API Key**：在 <https://platform.openai.com/settings/organization/api-keys> 创建，需要对应 Tunnel 的 *Read + Use* 权限。
4. 隧道运行期间，在 <https://chatgpt.com/#settings/Connectors> 创建或刷新连接器。

## 各服务的使用方式

### 本仓库的三个 lab

```powershell
cd browser-mcp-lab            # 或 computer-use-mcp-lab / local-coding-agent
copy tunnel-profile.example.yaml tunnel-profile.yaml   # 按注释替换 <...> 占位符
# 把 Runtime API Key 保存到 secrets\openai-tunnel-key.txt
```

- Browser / Computer Use：`doctor-*-tunnel.cmd` 自检配置，`start-*-tunnel.cmd` 前台运行隧道（窗口保持打开）。
- Local Coding Agent：`start-chatgpt-local-coding-agent.cmd` 在后台启动，等待 `http://127.0.0.1:8081/readyz` 就绪后才报告成功；`status-local-coding-agent.ps1` 查看状态，`stop-local-coding-agent.ps1` 结束进程树。

### Windows-MCP

把 `windows-mcp\` 下的脚本放到 Windows-MCP 安装目录（包含 `runtime\venv\`、`tunnel-client\bin\`、`secrets\`），或者用 `-Root <安装目录>` 参数指定：

| 脚本 | 作用 |
|---|---|
| `start-http.ps1` | 以 Streamable HTTP 在 `127.0.0.1:8000` 启动 Windows-MCP，等端口监听后才返回 |
| `status-http.ps1` | 检查 PID、进程路径与端口；健康时退出码为 0 |
| `stop-http.ps1` | 核对 PID 属于本服务后，结束整个进程树 |
| `start-tunnel.ps1` | 读取 `secrets\tunnel-id.txt` / `secrets\runtime-api-key.txt`，生成 tunnel profile 并运行 `doctor` |
| `connect-tunnel.ps1` | 通过 `tunnel-client runtimes connect` 交给 tunnel-client 托管运行，并输出状态 |

### browser-harness

把 `browser-harness\` 下的文件放到 browser-harness 安装目录（包含 `.venv\` 和 `tunnel-client\bin\`）：

1. `OPEN-CHATGPT-SETUP-PAGES.cmd`：打开 Tunnel、API Key 与 Connectors 三个设置页面。
2. `CONFIGURE-CHATGPT-TUNNEL.cmd`：输入 Tunnel ID，生成 profile。
3. `START-CHATGPT-BROWSER.cmd`：先 `doctor` 自检，再运行隧道；Runtime API Key 以 `SecureString` 读入，用完即从环境变量中清除。
4. `ENTER-VALUES-AND-START.cmd`：把第 2、3 步合成一次完成。

## 安全设计

- **只监听本机**：MCP 服务与健康检查都绑定 `127.0.0.1`，隧道只建立出站连接。
- **凭据不进配置、不进仓库**：profile 里只写 `file:` / `env:` 引用；`secrets/`、`tunnel-profile.yaml`、`tunnel-id.txt` 与运行日志都已加入 `.gitignore`。
- **不误杀进程**：PID 文件配合可执行文件路径一起核对，PID 被其他程序复用时只清理 PID 文件，不结束该进程。
- **结束整个进程树**：venv 中的 `python.exe` 会再拉起一个真正的解释器进程来监听端口，只结束父进程会留下占着端口的子进程，所以统一用 `taskkill /T`。
- **确认就绪才报告成功**：启动脚本在端口监听或 `/readyz` 返回 ready 之后才报告成功，进程退出时立即报错。

## 迁移与环境注意事项

- pip / uv 生成的 `.exe` 包装器（`windows-mcp.exe`、`browser-harness-mcp.exe`）写死了绝对路径，移动安装目录后会失效。因此这里统一改用 venv 的 `python.exe -m windows_mcp` 和 `python.exe run-mcp.py` 启动。
- venv 的 `pyvenv.cfg` 里 `home` 同样是绝对路径，移动目录后要一起更新。
- 安装路径不要包含空格：Windows PowerShell 5.1 向原生程序传参时会丢掉内嵌的引号。
- Local Coding Agent 需要 PowerShell 7（`pwsh`），详见 [`../local-coding-agent/README.md`](../local-coding-agent/README.md)。

## 验证记录

2026-09-23 在安装目录的**副本**上验证：

- **Windows-MCP**：`status`（未运行）→ `start` → `status` → 再次 `start`（识别为已运行）→ MCP `initialize` 返回 HTTP 200 → `stop` 后没有残留进程，8000 端口已释放；从停止状态重新 `start` 同样成功。PID 被无关进程复用时，`stop` 不会结束该进程。
- **browser-harness**：`run-mcp.py` 完成 MCP `initialize`，`tools/list` 返回 23 个工具；`01-configure-tunnel.ps1` 生成的 profile 正确，非法 Tunnel ID 会被拒绝。
- **未验证的部分**：这次没有用真实密钥实际连接 OpenAI 隧道。其中 `connect-tunnel.ps1` 改为用 `file:` 引用密钥文件，写法与三个 lab profile 中已经在用的 `api_key: "file:..."` 一致，但尚未做联网验证。

---

<details>
<summary><b>English summary</b></summary>

Launch, configuration and supervision scripts that expose local MCP servers to ChatGPT Connectors through OpenAI's Secure MCP Tunnel (`tunnel-client` v0.0.14). Every MCP server binds to `127.0.0.1`; the tunnel only dials out. Profiles reference secrets via `file:` / `env:` only, and secrets, generated profiles and logs are git-ignored. Process scripts verify PID ownership by executable path and terminate the whole process tree.

Covers this repo's Browser / Computer Use / Local Coding Agent labs plus two third-party servers — [CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP) (MIT) and [browser-use/browser-harness](https://github.com/browser-use/browser-harness) (MIT) — for which only integration scripts are included, not upstream source.

</details>
