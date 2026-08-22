"""Shell nástroj - run_command s volbou shellu (bash/powershell/cmd)."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from harness.safety import Risk
from harness.tools.base import AgentContext, Tool, truncate

# Minimální pojistka proti catastrofickým příkazem (potvrzování řeší safety vrstva,
# tohle je jen záchranná síť pro auto režim).
BLOCKED_PATTERNS = [
    r"\bformat\s+[a-z]:",
    r"\brm\s+(-[a-z]+\s+)*/(\s|$)",          # rm -rf /
    r"\brd\s+/s\s+/q\s+[a-z]:\\?$",
    r"\bdel\s+/[sq].*\\windows\b",
    r"\bmkfs\b",
    r"shutdown\s+/s",
    r"Remove-Item\s+-Recurse\s+-Force\s+[A-Z]:\\\s*$",
]


def _is_windows_bash_shim(path: Path) -> bool:
    normalized = str(path).replace("/", "\\").lower()
    return normalized.endswith("\\windows\\system32\\bash.exe") \
        or "\\windowsapps\\bash.exe" in normalized


@lru_cache(maxsize=1)
def find_bash() -> str | None:
    """Najde skutečný Git Bash na Windows, jinak běžný bash z PATH."""
    if sys.platform != "win32":
        return shutil.which("bash")

    candidates: list[Path] = []
    git = shutil.which("git")
    if git:
        git_path = Path(git)
        candidates.extend([
            git_path.parent.parent / "bin" / "bash.exe",
            git_path.parent / "bash.exe",
        ])

    import os
    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        base = os.environ.get(env_name)
        if not base:
            continue
        root = Path(base)
        if env_name == "LocalAppData":
            root /= "Programs"
        candidates.append(root / "Git" / "bin" / "bash.exe")

    discovered = shutil.which("bash")
    if discovered:
        candidates.append(Path(discovered))

    for candidate in candidates:
        if candidate.is_file() and not _is_windows_bash_shim(candidate):
            return str(candidate)
    return None


# Příkazy, které pouze čtou (nepotřebují potvrzení ani v supervised režimu).
SAFE_CMDS = {"ls", "dir", "cat", "type", "head", "tail", "grep", "find", "wc", "file",
             "stat", "du", "df", "pwd", "whoami", "which", "where", "tree", "echo",
             "date", "uname", "hostname", "ipconfig"}
# Git subcommandy, které pouze čtou (bez dvojznačných: branch/tag/remote/config umí i zapisovat).
SAFE_GIT_SUB = {"status", "log", "diff", "show", "blame", "rev-parse",
                "ls-files", "describe", "shortlog", "help"}
# Klíčová slova, která cokoliv mění - nesmí se vyskytnout v žádném segmentu.
UNSAFE_RE = re.compile(
    r"\b(rm|del|rd|move|mv|cp|copy|touch|mkdir|rmdir|chmod|chown|kill|taskkill|"
    r"curl|wget|invoke-webrequest|invoke-restmethod|start-process|iex|invoke-expression|"
    r"install|uninstall|remove-item|new-item|set-item|clear-item|out-file|"
    r"tee|sed|awk|tr|xargs|sudo|npm|pip)\b",
    re.IGNORECASE,
)


def is_read_only_command(command: str) -> bool:
    """Konzervativní heuristika: je příkaz čistě čtecí?

    Pravidla: žádné přesměrování (<, >), substituce (`, $()), nebezpečná klíčová
    slova; každý segment (oddělený |, &&, ||, ;) musí začínat bezpečným příkazem.
    """
    if not command or not command.strip():
        return False
    if "<" in command or ">" in command or "`" in command or "$(" in command:
        return False
    segments = re.split(r"\||&&|\|\||;|\r?\n", command)
    for seg in segments:
        toks = seg.strip().split()
        if not toks:
            continue
        if UNSAFE_RE.search(seg):
            return False
        base = toks[0].lower().lstrip("(").rstrip(")")
        if base not in SAFE_CMDS and base != "git":
            return False
        if base == "git":
            sub = next((t for t in toks[1:] if not t.startswith("-")), None)
            if sub is None or sub.lower() not in SAFE_GIT_SUB:
                return False
    return True


class RunCommandTool(Tool):
    name = "run_command"
    description = (
        "Run a shell command and return stdout+stderr with exit code. "
        "shells: 'bash' (Git Bash - preferred, default), 'powershell', 'cmd'. "
        "Avoid interactive commands (they would hang); use timeouts."
    )
    parameters = {
        "command": {"type": "string", "description": "Command to execute"},
        "shell": {"type": "string", "enum": ["bash", "powershell", "cmd"], "description": "Which shell to use (default: bash)"},
        "cwd": {"type": "string", "description": "Working directory (optional)"},
        "timeout": {"type": "integer", "description": "Timeout in seconds (optional)"},
    }
    required = ["command"]
    risk = Risk.WRITE

    def risk_for(self, args: dict) -> Risk:
        """Čtecí příkazy (ls, cat, grep, git log...) nepotřebují potvrzení."""
        return Risk.SAFE if is_read_only_command(str(args.get("command", ""))) else Risk.WRITE

    def run(self, ctx: AgentContext, command: str, shell: str = "bash", cwd: str | None = None, timeout: int | None = None) -> str:
        for pat in BLOCKED_PATTERNS:
            if re.search(pat, command, re.IGNORECASE):
                return f"ERROR: Command blocked by safety guard (matched {pat!r}). If you really need this, ask the user to run it manually."

        timeout = timeout or ctx.cfg.agent.get("shell_timeout", 60)
        workdir = str(ctx.resolve(cwd)) if cwd else str(ctx.workspace)

        if shell == "powershell":
            argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
        elif shell == "cmd":
            argv = ["cmd", "/c", command]
        else:  # bash (Git Bash)
            bash = find_bash()
            if not bash:
                return "ERROR: bash not found - use shell='powershell' instead"
            argv = [bash, "-lc", command]

        try:
            import sys as _sys
            proc = subprocess.run(
                argv, cwd=workdir, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
                # bez konzole - pythonw rodic by jinak blikal černým CMD oknem
                creationflags=0x08000000 if _sys.platform == "win32" else 0,
            )
        except subprocess.TimeoutExpired:
            return f"ERROR: Command timed out after {timeout}s: {command}"
        except FileNotFoundError as e:
            return f"ERROR: {e}"

        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        parts = [f"$ {command}", f"[exit code: {proc.returncode}]"]
        if out:
            parts.append(truncate(out, 15_000, "stdout"))
        if err:
            parts.append("STDERR:\n" + truncate(err, 5_000, "stderr"))
        if not out and not err:
            parts.append("(no output)")
        return "\n".join(parts)


class StartCommandTool(Tool):
    name = "start_command"
    description = ("Start a long-running command in the background. Returns process_id immediately; "
                   "use poll_command for incremental output and terminate_command to stop it.")
    parameters = {
        "command": {"type": "string"},
        "shell": {"type": "string", "enum": ["bash", "powershell", "cmd"]},
        "cwd": {"type": "string"},
        "timeout": {"type": "integer", "description": "Hard timeout in seconds (default 900)"},
    }
    required = ["command"]
    risk = Risk.WRITE

    def risk_for(self, args: dict) -> Risk:
        return Risk.SAFE if is_read_only_command(str(args.get("command", ""))) else Risk.WRITE

    def run(self, ctx: AgentContext, command: str, shell: str = "bash",
            cwd: str | None = None, timeout: int = 900) -> str:
        import json
        if not ctx.processes:
            return "ERROR: process manager unavailable"
        try:
            item = ctx.processes.start(command, shell, ctx.resolve(cwd) if cwd else ctx.workspace,
                                       max(0, int(timeout)))
            return json.dumps({"process_id": item.id, "status": "running",
                               "command": command}, ensure_ascii=False)
        except (OSError, ValueError) as exc:
            return f"ERROR: cannot start command: {exc}"


class PollCommandTool(Tool):
    name = "poll_command"
    description = "Read new output and status from a process started by start_command."
    parameters = {
        "process_id": {"type": "string"},
        "cursor": {"type": "integer", "description": "Cursor returned by previous poll (default 0)"},
        "max_chars": {"type": "integer", "description": "Maximum new output characters (default 20000)"},
    }
    required = ["process_id"]

    def run(self, ctx: AgentContext, process_id: str, cursor: int = 0,
            max_chars: int = 20_000) -> str:
        import json
        return json.dumps(ctx.processes.poll(process_id, cursor, max_chars), ensure_ascii=False)


class SendStdinTool(Tool):
    name = "send_stdin"
    description = "Write text to the stdin of a running background process. Include newline when needed."
    parameters = {"process_id": {"type": "string"}, "text": {"type": "string"}}
    required = ["process_id", "text"]
    risk = Risk.WRITE

    def run(self, ctx: AgentContext, process_id: str, text: str) -> str:
        import json
        return json.dumps(ctx.processes.send_stdin(process_id, text), ensure_ascii=False)


class TerminateCommandTool(Tool):
    name = "terminate_command"
    description = "Terminate a background command and its child process tree."
    parameters = {"process_id": {"type": "string"}}
    required = ["process_id"]
    risk = Risk.WRITE

    def run(self, ctx: AgentContext, process_id: str) -> str:
        import json
        return json.dumps(ctx.processes.terminate(process_id), ensure_ascii=False)


def register_shell_tools(registry) -> None:
    registry.register(RunCommandTool())
    registry.register(StartCommandTool())
    registry.register(PollCommandTool())
    registry.register(SendStdinTool())
    registry.register(TerminateCommandTool())
