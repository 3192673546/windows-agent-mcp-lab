from __future__ import annotations

import atexit
import codecs
import difflib
import locale
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from openai_apply_diff import apply_diff

try:
    from winpty import Backend, PTY
except Exception:
    Backend = None
    PTY = None


SERVER_INSTRUCTIONS = """
You are connected to a local Windows coding-agent harness with full access allowed by the Windows user that started it.
Treat the current workspace as the default project context, not as a permission boundary.
Call workspace() near the start of project work to learn the project root, available developer tools, and applicable AGENTS.md instructions.
Use exec_command() as the primary command runner for rg, Git, Python, Node/npm, builds, tests, and shell tasks.
Commands passed to exec_command() are PowerShell script bodies; native commands such as git, rg, python, node, and npm can be invoked directly.
Prefer rg for project search. Prefer apply_patch() for precise code/file edits.
For long-running commands, use the returned session_id with read_process(), write_stdin(), and kill_process().
Use tty=true for interactive terminal programs that need Windows ConPTY. Commands run without a visible console window.
Use explicit cwd in exec_command/apply_patch when switching projects or sharing this server; workspace() changes a process-wide default.
Before editing, call workspace(set_default=false, inspect_paths=[...]) to read instructions for target files, including nested directories.
If has_more_output is true, keep reading the returned session_id even after running becomes false. Output is retained until read or session expiration.
ok=true with running=true means the process started, not that the command succeeded. Inspect final exit_code, stderr and test results.
Each command starts a fresh PowerShell process; shell variables and cd do not persist between exec_command calls.
PowerShell errors stop execution. Native command exit codes are propagated; for an intentionally handled failure, explicitly exit 0 or reset LASTEXITCODE.
After an error, inspect the returned evidence and adjust the next operation instead of blindly repeating mutations.
This server supplies execution tools; planning, approvals, task persistence and deciding what to verify remain the responsibility of the calling agent.
Obey applicable AGENTS.md or AGENTS.override.md instructions before editing files under their scope.
After edits, run relevant checks/tests and inspect git diff when appropriate.
Use powershell() as the compatibility/fallback one-shot tool for Windows-specific operations.
Absolute paths and paths outside the workspace are allowed. The workspace does not restrict machine access.
""".strip()

mcp = MCPServer(
    name="Local Coding Agent",
    version="0.2.0",
    description="Codex-style local coding tools for Windows.",
    instructions=SERVER_INSTRUCTIONS,
)

DEFAULT_MAX_OUTPUT = 100_000
MAX_OUTPUT_LIMIT = 250_000
SESSION_RETENTION_SECONDS = 1800


def _augment_path() -> None:
    candidates: list[Path] = [
        Path(r"C:\Program Files\Git\cmd"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links",
    ]
    packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if packages.is_dir():
        for exe in packages.glob("BurntSushi.ripgrep.MSVC_*/**/rg.exe"):
            candidates.append(exe.parent)

    existing = os.environ.get("PATH", "").split(os.pathsep)
    lowered = {p.lower() for p in existing if p}
    for candidate in candidates:
        value = str(candidate)
        if candidate.is_dir() and value.lower() not in lowered:
            existing.insert(0, value)
            lowered.add(value.lower())
    os.environ["PATH"] = os.pathsep.join(existing)


_augment_path()

_workspace_lock = threading.RLock()
_workspace = Path(
    os.path.expandvars(
        os.path.expanduser(os.environ.get("MCP_WORKSPACE") or str(Path.home()))
    )
).resolve()
if not _workspace.is_dir():
    _workspace = Path.home().resolve()


def _clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(int(value), high))


def _truncate(text: str, limit: int = DEFAULT_MAX_OUTPUT) -> str:
    limit = _clamp_int(limit, 1000, MAX_OUTPUT_LIMIT)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[output truncated]"


def _shell_exe() -> str:
    shell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if not shell:
        raise RuntimeError("PowerShell executable not found.")
    return shell


def _resolve_dir(path: str | None) -> Path:
    with _workspace_lock:
        base = _workspace
    if not path:
        return base

    if "\x00" in path:
        raise ValueError("Path contains a null byte.")

    expanded = Path(os.path.expandvars(os.path.expanduser(path)))
    if not expanded.is_absolute():
        expanded = base / expanded
    resolved = expanded.resolve()
    if not resolved.is_dir():
        raise ValueError(f"Working directory does not exist: {resolved}")
    return resolved


def _resolve_path(path: str, base: Path | None = None) -> Path:
    if "\x00" in path:
        raise ValueError("Path contains a null byte.")
    with _workspace_lock:
        default_base = _workspace
    expanded = Path(os.path.expandvars(os.path.expanduser(path)))
    if not expanded.is_absolute():
        expanded = (base or default_base) / expanded
    return expanded.resolve(strict=False)


def _tool_paths() -> dict[str, str | None]:
    return {
        "powershell": _shell_exe(),
        "git": shutil.which("git.exe") or shutil.which("git"),
        "rg": shutil.which("rg.exe") or shutil.which("rg"),
        "python": shutil.which("python.exe") or shutil.which("python"),
        "node": shutil.which("node.exe") or shutil.which("node"),
        "npm": shutil.which("npm.cmd") or shutil.which("npm.ps1") or shutil.which("npm"),
    }


def _instruction_settings() -> tuple[Path, list[str], int]:
    config_dir = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    config = config_dir / 'config.toml'
    settings = tomllib.loads(config.read_text(encoding='utf-8-sig')) if config.is_file() else {}
    fallbacks = settings.get('project_doc_fallback_filenames', [])
    if not isinstance(fallbacks, list) or not all(isinstance(v, str) for v in fallbacks):
        raise ValueError('project_doc_fallback_filenames must be a list of filenames')
    for name in fallbacks:
        if not name or Path(name).name != name or '/' in name or '\\' in name:
            raise ValueError('Instruction fallback names must be simple filenames')
    limit = int(settings.get('project_doc_max_bytes', 32768))
    return config_dir, ['AGENTS.override.md', 'AGENTS.md', *fallbacks], max(0, limit)


def _limit_agents(entries: list[dict[str, str]], limit: int) -> list[dict[str, str]]:
    result = []
    for entry in entries:
        if limit <= 0:
            break
        data = entry['content'].encode('utf-8')
        content = data[:limit].decode('utf-8', errors='ignore')
        result.append({**entry, 'content': content})
        limit -= len(content.encode('utf-8'))
    return result


def _agents_for_directory(directory: Path, root: Path | None = None) -> list[dict[str, str]]:
    # Capture the root once. A later workspace() call cannot change an in-flight lookup.
    if root is None:
        with _workspace_lock:
            root = _workspace
    directory = directory.resolve()
    root = root.resolve()
    repo = next((p for p in (directory, *directory.parents) if (p / '.git').exists()), None)
    if repo is not None:
        root = repo
    try:
        directory.relative_to(root)
    except ValueError:
        # Absolute paths remain allowed and must not silently lose instruction context.
        root = directory
    config_dir, names, limit = _instruction_settings()
    chain = [config_dir, root] if config_dir != root else [root]
    current = root
    for part in directory.relative_to(root).parts:
        current = current / part
        chain.append(current)
    results = []
    for folder in chain:
        candidates = names[:2] if folder == config_dir else names
        for name in candidates:
            chosen = folder / name
            if chosen.is_file():
                content = chosen.read_text(encoding='utf-8-sig', errors='replace')
                if content.strip():
                    results.append({'path': str(chosen), 'content': content})
                    break
    return _limit_agents(results, limit)


def _agents_for_paths(paths: list[Path], root: Path) -> list[dict[str, str]]:
    entries = {}
    for path in paths:
        directory = path if path.is_dir() else path.parent
        for entry in _agents_for_directory(directory, root):
            entries[entry['path']] = entry
    return _limit_agents(list(entries.values()), _instruction_settings()[2])


@mcp.tool(title="Workspace Context")
def workspace(
    path: str | None = None,
    set_default: bool = True,
    inspect_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Get project/tool context. Inspect target-file AGENTS before edits.

    path selects a directory. set_default=false inspects it without changing the
    process-wide default. inspect_paths accepts files/directories relative to path.
    The workspace is a default context, never a filesystem permission boundary.
    """
    global _workspace
    try:
        current = _resolve_dir(path)
        paths = [_resolve_path(value, current) for value in (inspect_paths or [])]
        agents = _agents_for_paths([current, *paths], current)
        if path is not None and set_default:
            with _workspace_lock:
                _workspace = current
        return {
            'ok': True, 'version': '0.2.0', 'workspace': str(current),
            'permission_boundary': False, 'default_scope': 'server_process',
            'agents': agents,
            'tools': _tool_paths(), 'tty_backend': 'ConPTY',
            'patch_engine': 'OpenAI Agents SDK 0.22.2 apply_diff',
            'instructions': 'Read target-file rules before editing. Use explicit cwd for concurrent projects.',
        }
    except Exception as exc:
        return {'ok': False, 'error': repr(exc)}


def _create_script(command: str) -> Path:
    fd, raw_path = tempfile.mkstemp(prefix="local-coding-agent-", suffix=".ps1")
    os.close(fd)
    path = Path(raw_path)
    prelude = (
        "[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()\n"
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()\n"
        "$OutputEncoding = [Console]::OutputEncoding\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$global:LASTEXITCODE = 0\n"
    )
    # Explicit exit remains authoritative; the final native failure is otherwise lost by -File.
    epilogue = "\nif (-not $?) { if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; exit 1 }\nexit $LASTEXITCODE\n"
    path.write_text(prelude + command + epilogue, encoding="utf-8-sig", newline="\n")
    return path


def _cleanup_script(path: str | Path | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )


@dataclass
class ProcessSession:
    session_id: str
    command: str
    cwd: str
    process: Any
    backend: str
    script_path: str
    created_at: float = field(default_factory=time.time)
    last_access: float = field(default_factory=time.time)
    lock: threading.RLock = field(default_factory=threading.RLock)
    reader_threads: list[threading.Thread] = field(default_factory=list)
    agents: list[dict[str, str]] = field(default_factory=list)
    spools: dict[str, Any] = field(default_factory=dict)
    positions: dict[str, int] = field(default_factory=lambda: {'stdout': 0, 'stderr': 0})
    pending: dict[str, int] = field(default_factory=lambda: {'stdout': 0, 'stderr': 0})

    @property
    def pid(self) -> int | None:
        return getattr(self.process, 'pid', None)

    def append(self, stream: str, text: str) -> None:
        if not text:
            return
        with self.lock:
            if stream not in self.spools:
                self.spools[stream] = tempfile.TemporaryFile(mode='w+t', encoding='utf-8', newline='')
            spool = self.spools[stream]
            spool.seek(0, os.SEEK_END)
            spool.write(text)
            spool.flush()
            self.pending[stream] += len(text)

    def has_output(self) -> bool:
        with self.lock:
            return any(self.pending.values())

    def drain(self, max_chars: int) -> tuple[str, str]:
        limit = _clamp_int(max_chars, 1000, MAX_OUTPUT_LIMIT)
        self.last_access = time.time()
        output = []
        with self.lock:
            for stream in ('stdout', 'stderr'):
                spool = self.spools.get(stream)
                if spool is None or self.pending[stream] == 0:
                    output.append('')
                    continue
                spool.seek(self.positions[stream])
                chunk = spool.read(limit)
                self.positions[stream] = spool.tell()
                self.pending[stream] -= len(chunk)
                output.append(chunk)
                if self.pending[stream] == 0:
                    # Reclaim consumed output; a long-lived process must not retain its entire history.
                    spool.seek(0)
                    spool.truncate(0)
                    self.positions[stream] = 0
        return output[0], output[1]

    def running(self) -> bool:
        if self.backend == 'pty':
            return bool(self.process.isalive())
        return self.process.poll() is None

    def exit_code(self) -> int | None:
        if self.running():
            return None
        status = self.process.exitstatus if self.backend == 'pty' else self.process.poll()
        return int(status) if status is not None else None

    def write(self, text: str) -> None:
        self.last_access = time.time()
        if not self.running():
            raise RuntimeError('Process is no longer running.')
        if self.backend == 'pty':
            self.process.write(text)
        else:
            if self.process.stdin is None:
                raise RuntimeError('Process stdin is unavailable.')
            self.process.stdin.write(text.encode('utf-8'))
            self.process.stdin.flush()

    def wait_readers(self, timeout: float = 0.4) -> None:
        if self.running():
            return
        deadline = time.monotonic() + timeout
        for thread in self.reader_threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))
        _cleanup_script(self.script_path)

    def close(self) -> None:
        self.wait_readers()
        with self.lock:
            for spool in self.spools.values():
                spool.close()
            self.spools.clear()
        if self.backend == 'pipe' and self.process.stdin is not None:
            self.process.stdin.close()
        if self.backend == 'pty':
            self.process.close(force=True)
        _cleanup_script(self.script_path)


_sessions: dict[str, ProcessSession] = {}
_sessions_lock = threading.RLock()


def _pipe_reader(session: ProcessSession, stream_name: str, pipe: Any) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    try:
        reader = getattr(pipe, "read1", pipe.read)
        while True:
            chunk = reader(4096)
            if not chunk:
                break
            session.append(stream_name, decoder.decode(chunk))
        session.append(stream_name, decoder.decode(b"", final=True))
    except Exception as exc:
        session.append("stderr", f"\n[{stream_name} reader error: {exc!r}]\n")
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def _pty_reader(session: ProcessSession) -> None:
    quiet_since = None
    try:
        while True:
            try:
                chunk = session.process.read(4096)
            except EOFError:
                break
            except Exception as exc:
                if session.running():
                    session.append("stderr", f"\n[TTY reader error: {exc!r}]\n")
                break
            if chunk:
                session.append("stdout", str(chunk))
                quiet_since = None
            else:
                if not session.running():
                    if quiet_since is None:
                        quiet_since = time.monotonic()
                    elif time.monotonic() - quiet_since >= 0.15:
                        break
                time.sleep(0.01)
    finally:
        if not session.running():
            _cleanup_script(session.script_path)


def _prune_sessions() -> None:
    cutoff = time.time() - SESSION_RETENTION_SECONDS
    with _sessions_lock:
        stale: list[str] = []
        for sid, session in _sessions.items():
            if (not session.running() and session.last_access < cutoff
                    and all(not t.is_alive() for t in session.reader_threads)):
                stale.append(sid)
        for sid in stale:
            session = _sessions.pop(sid, None)
            if session is not None:
                session.close()


def _process_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault('PYTHONIOENCODING', 'utf-8')
    env.setdefault('PYTHONUTF8', '1')
    return env


class _ConPtyProcess:
    """Small adapter over native ConPTY, avoiding PtyProcess's extra socket bridge."""

    def __init__(self, argv: list[str], cwd: Path, rows: int, cols: int):
        self.pty = PTY(_clamp_int(cols, 20, 400), _clamp_int(rows, 10, 200), backend=Backend.ConPTY)
        env = '\0'.join(f'{k}={v}' for k, v in _process_env().items()) + '\0'
        self.pty.spawn(argv[0], cwd=str(cwd), env=env, cmdline=' ' + subprocess.list2cmdline(argv[1:]))
        self.pid = self.pty.pid
        self.final_code = None

    def isalive(self) -> bool:
        return self.pty is not None and self.pty.isalive()

    @property
    def exitstatus(self):
        if self.pty is not None:
            self.final_code = self.pty.get_exitstatus()
        return self.final_code

    def read(self, size: int) -> str:
        return self.pty.read(blocking=False) if self.pty is not None else ''

    def write(self, text: str) -> None:
        self.pty.write(text)

    def close(self, force: bool = False) -> None:
        self.final_code = self.exitstatus
        self.pty = None


def _new_session(
    command: str,
    cwd: Path,
    tty: bool,
    rows: int = 30,
    cols: int = 120,
) -> ProcessSession:
    _prune_sessions()
    agents = _agents_for_directory(cwd)
    shell = _shell_exe()
    script_path = _create_script(command)

    try:
        if tty:
            if PTY is None:
                raise RuntimeError(
                    "TTY requested but pywinpty is not installed in the MCP environment."
                )
            process = _ConPtyProcess(
                [shell, "-NoLogo", "-NoProfile", "-File", str(script_path)],
                cwd=cwd, rows=rows, cols=cols,
            )
            backend = "pty"
        else:
            process = subprocess.Popen(
                [shell, "-NoLogo", "-NoProfile", "-File", str(script_path)],
                cwd=str(cwd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=_creation_flags(),
                env=_process_env(),
            )
            backend = "pipe"
    except Exception:
        _cleanup_script(script_path)
        raise

    session = ProcessSession(
        session_id="proc_" + secrets.token_hex(6),
        command=command,
        cwd=str(cwd),
        process=process,
        backend=backend,
        script_path=str(script_path),
        agents=agents,
    )

    if backend == "pty":
        thread = threading.Thread(target=_pty_reader, args=(session,), daemon=True)
        session.reader_threads.append(thread)
        thread.start()
    else:
        if process.stdout is not None:
            thread = threading.Thread(
                target=_pipe_reader,
                args=(session, "stdout", process.stdout),
                daemon=True,
            )
            session.reader_threads.append(thread)
            thread.start()
        if process.stderr is not None:
            thread = threading.Thread(
                target=_pipe_reader,
                args=(session, "stderr", process.stderr),
                daemon=True,
            )
            session.reader_threads.append(thread)
            thread.start()

    with _sessions_lock:
        _sessions[session.session_id] = session
    return session


def _get_session(session_id: str) -> ProcessSession:
    with _sessions_lock:
        session = _sessions.get(session_id)
    if session is None:
        raise ValueError(f"Unknown process session: {session_id}")
    return session


def _session_result(session: ProcessSession, max_output_chars: int = DEFAULT_MAX_OUTPUT) -> dict[str, Any]:
    running = session.running()
    if not running:
        session.wait_readers()
    stdout, stderr = session.drain(max_output_chars)
    code = session.exit_code() if not running else None
    complete = not running and all(not t.is_alive() for t in session.reader_threads)
    return {
        'ok': True if running else code == 0,
        'session_id': session.session_id,
        'running': running, 'exit_code': code, 'pid': session.pid,
        'tty': session.backend == 'pty', 'backend': 'ConPTY' if session.backend == 'pty' else 'pipe',
        'cwd': session.cwd, 'stdout': stdout, 'stderr': stderr,
        'has_more_output': session.has_output(), 'output_complete': complete,
        'applicable_agents': session.agents,
    }


@mcp.tool(title="Execute Command")
def exec_command(
    command: str,
    cwd: str | None = None,
    yield_time_ms: int = 10_000,
    tty: bool = False,
    rows: int = 30,
    cols: int = 120,
    max_output_chars: int = DEFAULT_MAX_OUTPUT,
) -> dict[str, Any]:
    """
    Execute a PowerShell script body in the workspace or a supplied cwd.

    Native commands such as git, rg, python, node, npm, and build/test commands
    can be invoked directly. The command is written to a temporary .ps1 and run
    with pwsh -File, so large/multiline scripts and PowerShell here-strings are
    supported without Windows command-line length issues.

    Waits up to yield_time_ms (max 30s). If still running, returns session_id.
    Set tty=true for interactive/TUI programs that need Windows ConPTY.
    """
    try:
        working_dir = _resolve_dir(cwd)
        session = _new_session(command, working_dir, tty, rows, cols)
        wait_ms = _clamp_int(yield_time_ms, 0, 30_000)
        deadline = time.time() + wait_ms / 1000

        while session.running() and time.time() < deadline:
            time.sleep(0.05)

        result = _session_result(session, max_output_chars)
        if not result["running"] and not result["has_more_output"] and result["output_complete"]:
            result["session_id"] = None
        return result
    except Exception as exc:
        return {"ok": False, "error": repr(exc)}


@mcp.tool(title="Read Process")
def read_process(
    session_id: str,
    wait_ms: int = 0,
    max_output_chars: int = DEFAULT_MAX_OUTPUT,
) -> dict[str, Any]:
    """
    Read new stdout/stderr from a process session and report its current status.

    wait_ms may wait up to 30 seconds for new output or process completion.
    Reads consume only the returned characters. Keep reading while has_more_output
    is true, even after process exit. Sessions expire after 30 minutes of inactivity.
    """
    try:
        session = _get_session(session_id)
        deadline = time.time() + _clamp_int(wait_ms, 0, 30_000) / 1000
        while (
            session.running()
            and not session.has_output()
            and time.time() < deadline
        ):
            time.sleep(0.05)
        return _session_result(session, max_output_chars)
    except Exception as exc:
        return {"ok": False, "error": repr(exc), "session_id": session_id}


@mcp.tool(title="Write Process Stdin")
def write_stdin(
    session_id: str,
    text: str = "",
    append_newline: bool = False,
    wait_ms: int = 250,
    max_output_chars: int = DEFAULT_MAX_OUTPUT,
) -> dict[str, Any]:
    """
    Send text to a running process/TTY session, then optionally wait for output.

    Use append_newline=true for answering line-oriented prompts. Control
    characters such as Ctrl-C may be sent in text when the client can encode
    them; kill_process() is the reliable way to terminate a process tree.
    """
    try:
        session = _get_session(session_id)
        newline = "\r" if session.backend == "pty" else "\n"
        payload = text + (newline if append_newline else "")
        if payload:
            session.write(payload)
        wait_until = time.time() + _clamp_int(wait_ms, 0, 30_000) / 1000
        while (
            session.running()
            and not session.has_output()
            and time.time() < wait_until
        ):
            time.sleep(0.05)
        return _session_result(session, max_output_chars)
    except Exception as exc:
        return {"ok": False, "error": repr(exc), "session_id": session_id}


def _terminate_process_tree(session: ProcessSession) -> None:
    if os.name == 'nt' and session.pid:
        # Kill the tree BEFORE closing ConPTY; otherwise descendants can escape cleanup.
        result = subprocess.run(
            ['taskkill', '/PID', str(session.pid), '/T', '/F'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=10,
        )
        if result.returncode and session.running():
            raise RuntimeError(f'taskkill failed with exit code {result.returncode}')
    elif session.running():
        session.process.terminate()


def _shutdown_sessions() -> None:
    with _sessions_lock:
        sessions = list(_sessions.values())
        _sessions.clear()
    for session in sessions:
        try:
            if session.running():
                _terminate_process_tree(session)
            session.close()
        except Exception:
            pass


atexit.register(_shutdown_sessions)


@mcp.tool(title="Kill Process")
def kill_process(
    session_id: str,
    wait_ms: int = 1000,
    max_output_chars: int = DEFAULT_MAX_OUTPUT,
) -> dict[str, Any]:
    """Terminate a running process session (including its child process tree)."""
    try:
        session = _get_session(session_id)
        was_running = session.running()
        if was_running:
            _terminate_process_tree(session)
        deadline = time.time() + _clamp_int(wait_ms, 0, 10_000) / 1000
        while session.running() and time.time() < deadline:
            time.sleep(0.05)
        result = _session_result(session, max_output_chars)
        result["killed"] = was_running and not result["running"]
        result["already_exited"] = not was_running
        result["ok"] = not result["running"]
        return result
    except Exception as exc:
        return {"ok": False, "error": repr(exc), "session_id": session_id}


def _run_one_shot(command: str, cwd: Path, timeout_seconds: int, max_output_chars: int) -> dict[str, Any]:
    session = _new_session(command, cwd, False)
    timeout = _clamp_int(timeout_seconds, 1, 600)
    deadline = time.monotonic() + timeout
    while session.running() and time.monotonic() < deadline:
        time.sleep(0.05)
    timed_out = session.running()
    if timed_out:
        _terminate_process_tree(session)
        session.process.wait(timeout=10)
    result = _session_result(session, max_output_chars)
    if timed_out:
        result.update(ok=False, timed_out=True, error=f'Command timed out after {timeout} seconds.')
    if result['output_complete'] and not result['has_more_output']:
        result['session_id'] = None
    return result


@mcp.tool(title="PowerShell Compatibility")
def powershell(
    command: str,
    cwd: str | None = None,
    timeout_seconds: int = 120,
    max_output_chars: int = DEFAULT_MAX_OUTPUT,
) -> dict[str, Any]:
    """
    Compatibility/fallback one-shot PowerShell tool with full Windows-user access.

    Prefer exec_command() for coding-agent work, long-running processes, TTY
    interaction, Git, rg, Python, Node/npm, builds, and tests.
    """
    try:
        working_dir = _resolve_dir(cwd)
        return _run_one_shot(
            command,
            working_dir,
            timeout_seconds,
            max_output_chars,
        )
    except Exception as exc:
        return {"ok": False, "error": repr(exc)}


def _decode_text_data(data: bytes, path: Path) -> tuple[str, str]:
    if data.startswith(b'\xff\xfe'):
        return data.decode('utf-16'), 'utf-16'
    if data.startswith(b'\xfe\xff'):
        return data.decode('utf-16'), 'utf-16-be-bom'
    if b"\x00" in data[:8192]:
        raise ValueError(f"Refusing to patch binary/non-text file: {path}")

    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"

    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        preferred = locale.getpreferredencoding(False) or "utf-8"
        for encoding in (preferred, "gb18030", "cp1252"):
            try:
                return data.decode(encoding), encoding
            except (UnicodeDecodeError, LookupError):
                continue
    raise ValueError(f"Unable to decode text file safely: {path}")


def _decode_text_file(path: Path) -> tuple[str, str]:
    return _decode_text_data(path.read_bytes(), path)


def _encode_text_data(text: str, encoding: str) -> bytes:
    if encoding == 'utf-16-be-bom':
        return b'\xfe\xff' + text.encode('utf-16-be')
    return text.encode(encoding)


def _split_patch_lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _parse_patch(patch: str) -> list[dict[str, Any]]:
    lines = _split_patch_lines(patch.strip("\ufeff"))
    while lines and lines[-1] == "":
        lines.pop()

    if not lines or lines[0].strip() != "*** Begin Patch":
        raise ValueError("Patch must start with '*** Begin Patch'.")
    if lines[-1].strip() != "*** End Patch":
        raise ValueError("Patch must end with '*** End Patch'.")

    operations: list[dict[str, Any]] = []
    i = 1
    end = len(lines) - 1

    def is_operation_header(line: str) -> bool:
        return (
            line.startswith("*** Add File: ")
            or line.startswith("*** Delete File: ")
            or line.startswith("*** Update File: ")
        )

    while i < end:
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        kind: str
        path: str
        if line.startswith("*** Add File: "):
            kind, path = "add", line[len("*** Add File: "):].strip()
        elif line.startswith("*** Delete File: "):
            kind, path = "delete", line[len("*** Delete File: "):].strip()
        elif line.startswith("*** Update File: "):
            kind, path = "update", line[len("*** Update File: "):].strip()
        else:
            raise ValueError(f"Unexpected patch line: {line}")

        if not path:
            raise ValueError(f"Missing path in patch header: {line}")
        i += 1

        move_to: str | None = None
        if kind == "update" and i < end and lines[i].startswith("*** Move to: "):
            move_to = lines[i][len("*** Move to: "):].strip()
            if not move_to:
                raise ValueError("Move target cannot be empty.")
            i += 1

        body: list[str] = []
        while i < end and not is_operation_header(lines[i]):
            body.append(lines[i])
            i += 1

        while body and body[-1] == "":
            body.pop()

        operations.append(
            {
                "kind": kind,
                "path": path,
                "move_to": move_to,
                "body": body,
            }
        )

    if not operations:
        raise ValueError("Patch contains no file operations.")
    return operations


def _apply_update_body(original: str, body: list[str], path: Path) -> str:
    if not body:
        raise ValueError(f'Empty update hunk: {path}')
    try:
        return apply_diff(original, '\n'.join(body))
    except ValueError as exc:
        raise ValueError(f'{path}: {exc}') from exc


def _add_body_to_text(body: list[str]) -> str:
    result = apply_diff('', '\n'.join(body), mode='create')
    # Codex Add File operations terminate nonempty files with a newline.
    return result + '\n' if body else ''


def _unified_diff(
    before: str | None,
    after: str | None,
    before_name: str,
    after_name: str,
) -> str:
    before_lines = [] if before is None else before.splitlines()
    after_lines = [] if after is None else after.splitlines()
    return "\n".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=before_name,
            tofile=after_name,
            lineterm="",
        )
    )


_patch_lock = threading.RLock()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix='.local-coding-patch-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            shutil.copymode(path, temp_name)
        os.replace(temp_name, path)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def _commit_files(changes: dict[Path, bytes | None], originals: dict[Path, bytes | None]) -> None:
    committed = []
    try:
        # Write move destinations before removing their sources.
        order = [p for p, data in changes.items() if data is not None]
        order += [p for p, data in changes.items() if data is None]
        for path in order:
            if changes[path] is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(path, changes[path])
            committed.append(path)
    except Exception as exc:
        rollback_errors = []
        for path in reversed(committed):
            try:
                if originals[path] is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_write(path, originals[path])
            except Exception as rollback_exc:
                rollback_errors.append(f'{path}: {rollback_exc!r}')
        detail = f'; rollback errors: {rollback_errors}' if rollback_errors else '; file contents rolled back'
        raise RuntimeError(f'Patch commit failed: {exc!r}{detail}') from exc


@mcp.tool(title="Apply Patch")
def apply_patch(
    patch: str,
    max_diff_chars: int = DEFAULT_MAX_OUTPUT,
    cwd: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Apply a Codex-style structured patch to local text files.

    Supported operations:
      *** Add File: path
      *** Update File: path
      *** Move to: new_path   (immediately after Update File)
      *** Delete File: path

    Relative paths are resolved from cwd or the current workspace. dry_run validates
    and previews without writing. Read target AGENTS using workspace first. Absolute/outside
    paths are allowed. The patch is staged in memory first so parse/hunk errors
    do not leave a partially applied multi-file patch.
    """
    try:
        base = _resolve_dir(cwd)
        operations = _parse_patch(patch)

        virtual: dict[Path, str | None] = {}
        encodings: dict[Path, str] = {}
        originals: dict[Path, str | None] = {}
        touched_order: list[Path] = []
        original_bytes: dict[Path, bytes | None] = {}

        def remember(path: Path) -> None:
            if path not in originals:
                original_bytes[path] = path.read_bytes() if path.exists() else None
                if original_bytes[path] is not None:
                    text, encoding = _decode_text_data(original_bytes[path], path)
                    originals[path] = text
                    encodings[path] = encoding
                else:
                    originals[path] = None
            if path not in touched_order:
                touched_order.append(path)

        def get_text(path: Path) -> str:
            if path in virtual:
                value = virtual[path]
                if value is None:
                    raise ValueError(f"File was deleted earlier in this patch: {path}")
                return value
            if path in originals and originals[path] is not None:
                return originals[path]
            if not path.is_file():
                raise ValueError(f"File does not exist: {path}")
            text, encoding = _decode_text_file(path)
            encodings.setdefault(path, encoding)
            return text

        for operation in operations:
            kind = operation["kind"]
            source = _resolve_path(operation["path"], base)
            remember(source)

            if kind == "add":
                currently_exists = (
                    virtual[source] is not None
                    if source in virtual
                    else source.exists()
                )
                if currently_exists:
                    raise ValueError(f"Cannot add file because it already exists: {source}")
                virtual[source] = _add_body_to_text(operation["body"])
                encodings[source] = "utf-8"
                continue

            if kind == "delete":
                if operation["body"]:
                    raise ValueError("Delete File cannot contain a body.")
                get_text(source)
                virtual[source] = None
                continue

            original_text = get_text(source)
            updated_text = _apply_update_body(
                original_text,
                operation["body"],
                source,
            )

            move_to = operation.get("move_to")
            if move_to:
                destination = _resolve_path(move_to, base)
                remember(destination)
                destination_exists = (
                    virtual[destination] is not None
                    if destination in virtual
                    else destination.exists()
                )
                if destination != source and destination_exists:
                    raise ValueError(
                        f"Cannot move to destination because it already exists: {destination}"
                    )
                virtual[source] = None
                virtual[destination] = updated_text
                encodings[destination] = encodings.get(source, "utf-8")
            else:
                virtual[source] = updated_text

        diffs: list[str] = []
        changed_files: list[dict[str, Any]] = []

        # Validate rules, encodings and all file states before the first write.
        agent_entries = _agents_for_paths(touched_order, base)
        changes: dict[Path, bytes | None] = {}
        for path in touched_order:
            if path not in virtual or originals.get(path) == virtual[path]:
                continue
            new_text, old_text = virtual[path], originals.get(path)
            changes[path] = None if new_text is None else _encode_text_data(new_text, encodings.get(path, 'utf-8'))
            action = 'delete' if new_text is None else 'add' if old_text is None else 'update'
            changed_files.append({'path': str(path), 'action': action})
            diffs.append(_unified_diff(old_text, new_text,
                str(path) if old_text is not None else '/dev/null',
                str(path) if new_text is not None else '/dev/null'))

        if not dry_run:
            with _patch_lock:
                for path in changes:
                    actual = path.read_bytes() if path.exists() else None
                    if actual != original_bytes[path]:
                        raise ValueError(f'File changed during patch preparation: {path}')
                _commit_files(changes, original_bytes)


        return {
            "ok": True,
            "changed_files": changed_files,
            "dry_run": dry_run,
            "patch_engine": "OpenAI Agents SDK 0.22.2",
            "diff": _truncate(
                "\n".join(part for part in diffs if part),
                max_diff_chars,
            ),
            "applicable_agents": agent_entries,
        }
    except Exception as exc:
        return {"ok": False, "error": repr(exc)}


if __name__ == "__main__":
    mcp.run()
