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
            proc = subprocess.run(
                argv, cwd=workdir, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
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
