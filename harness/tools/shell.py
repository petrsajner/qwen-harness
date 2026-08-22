"""Shell nástroj - run_command s volbou shellu (bash/powershell/cmd)."""
from __future__ import annotations

import re
import shutil
import subprocess

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
            bash = shutil.which("bash") or r"C:\Program Files\Git\bin\bash.exe"
            if not shutil.which("bash") and not shutil.which(bash):
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


def register_shell_tools(registry) -> None:
    registry.register(RunCommandTool())
