# Local Coding Agent 0.2.0

供 ChatGPT/MCP 客户端调用的 Windows 本地编程工具服务。保留原有七个工具名，模型负责规划、选择工具、理解失败并决定验证步骤。

## 与 Codex 对齐的部分

- 搜索与命令执行：PowerShell、rg、Git、Python、Node 等；每次命令是独立进程，使用临时 UTF-8 `.ps1`，不受 Windows 命令行长度影响。
- 交互终端：使用 pywinpty 的原生 ConPTY 接口，支持标准输入和终止子进程树，不创建可见终端窗口。
- 补丁定位：直接随项目提供 OpenAI Agents SDK **0.22.2** 的 `agents/apply_diff.py`，重命名为 `openai_apply_diff.py`，算法文件保持原样，许可证见 `LICENSE.openai-agents`。官方来源：https://developers.openai.com/api/docs/guides/tools-apply-patch 。它不是 Codex 桌面端的完整内部实现。
- 项目规则：读取 `CODEX_HOME`（默认 `~/.codex`）中的全局规则和 `config.toml` 的 `project_doc_fallback_filenames`、`project_doc_max_bytes`；按 Git 根目录到目标目录的顺序读取规则。没有 Git 根目录时，显式 workspace 作为根；目标在 workspace 外则只使用目标目录。忽略空规则文件，优先 override，限制合计 UTF-8 字节数。参考：https://developers.openai.com/codex/guides/agents-md 。
- 验证反馈：保留退出码、标准输出和错误输出。模型必须查看最终结果，不能把进程已启动当成任务已完成。

## 七个工具

| 工具 | 用途 |
|---|---|
| `workspace` | 查看/设置默认项目，检查工具路径与项目规则 |
| `exec_command` | 启动命令，最多等待 30 秒，返回可继续读取的会话 |
| `read_process` | 分页读取会话输出并查看状态 |
| `write_stdin` | 输入文本；空文本可只读取输出 |
| `kill_process` | 终止该会话的整个 Windows 子进程树 |
| `powershell` | 保留旧版一次性接口；超时终止并返回已产生的输出 |
| `apply_patch` | 添加、更新、移动、删除文本文件；支持 `cwd`、`dry_run` |

## 推荐调用顺序

1. `workspace(path="C:/project")` 确定项目。修改文件前调用 `workspace(set_default=false, inspect_paths=["src/example.py"])` 获取子目录规则。
2. 用 `exec_command(command="rg ...", cwd="C:/project")` 查看代码和工作区状态。
3. 用 `apply_patch(patch="*** Begin Patch\n...", cwd="C:/project")` 修改。需要预览时先传 `dry_run=true`。
4. 运行与修改相关的检查/测试并查看 diff，根据证据继续调整。

## 返回值和兼容行为

- `running=true` 时 `ok=true` 只表示启动成功。任务完成应查看 `running=false`、`exit_code` 和输出。
- `has_more_output=true` 表示还有未读输出；即使命令已经结束也要继续用 `session_id` 读取。
- `output_complete=false` 表示输出读取线程尚未结束，稍后继续读取。完成且输出读完时，初次执行接口可以返回 `session_id=null`。
- 输出按需写入临时文件，分页只消耗实际返回的字符；已读完的缓存释放磁盘空间。已退出的会话在闲置 30 分钟后可被回收，服务重启后会话不再存在。
- UTF-8 管道采用增量解码，支持跨字节块的中文/emoji。Python 子进程默认使用 UTF-8；调用者已设置编码环境变量时遵循该设置。
- PowerShell 非终止错误默认提升为停止执行；原生命令最终退出码传递给调用者。如果非零退出属于预期并已被脚本处理，显式 `exit 0` 或将 `$global:LASTEXITCODE=0`。这项约定用于消除原先的假成功，不能保证与所有 Codex shell 配置完全相同。
- `workspace()` 的默认目录仍然是整个服务进程共享的。多个任务共用服务时，命令和补丁必须显式传 `cwd`，检查别的项目时用 `set_default=false`。它不提供 Codex 的任务/工作树隔离。
- 补丁先校验全部片段和编码，再提交；单个文件通过临时文件替换，提交中的 I/O 错误尝试恢复已改文件，并报告恢复失败。它不是可抗断电的文件系统事务，失败后可能留下新建的空目录。
- 保留 Windows 文本编码和 CRLF 的兼容行为；拒绝覆盖已有 Add/Move 目标。除官方核心算法外，这些文件操作策略由本服务实现。
- 本服务保留原来的 Windows 用户权限和绝对路径访问；不会复制 Codex 的账号、模型、审批系统、沙箱或私有服务。

## 安装与测试

沿用 `.venv`、`requirements.txt` 和原来的 tunnel profile。没有引入完整 Agents SDK 或新的运行时依赖；官方算法是只依赖标准库的随附文件。

```powershell
.\.venv\Scripts\python.exe -X utf8 .\test_server.py
```

本机存在 Codex CLI 时，测试还会将四个定位/多片段案例与该二进制的实际补丁结果比较。该内部启动参数仅用于测试，不是生产依赖。没有本机 Codex 时该对照测试会显示跳过。

命令执行优先使用 PowerShell 7（`pwsh`）。只有 Windows PowerShell 5.1 时，涉及原生命令内嵌双引号的 4 项测试会失败，因为 5.1 传参时会丢掉这些引号。

## 接入 ChatGPT

1. 复制 `tunnel-profile.example.yaml` 为 `tunnel-profile.yaml`，按注释替换 `<...>` 占位符。
2. 把 Runtime API Key 保存到 `secrets\openai-tunnel-key.txt`。`secrets/` 和 `tunnel-profile.yaml` 都已被 git 忽略。
3. 双击 `start-chatgpt-local-coding-agent.cmd`，隧道在后台运行，`http://127.0.0.1:8081/readyz` 就绪后才报告成功，日志写入 `.runtime\`。
4. 用 `status-local-coding-agent.ps1` 查看状态，用 `stop-local-coding-agent.ps1` 停止。停止前会核对 PID 属于本项目的 tunnel-client，再结束整个进程树。

tunnel-client 的获取与校验见 [`../chatgpt-tunnel/README.md`](../chatgpt-tunnel/README.md)。

重新启动原隧道后旧调用方式仍可使用；如客户端未显示新增可选参数，请刷新该连接的工具列表。
