"""Základní infrastruktura nástrojů (tools) pro agenta."""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.safety import Risk


@dataclass
class AgentContext:
    """Sdílený kontext předávaný nástrojům při vykonávání."""

    cfg: Any                       # harness.config.Config
    session: Any                   # harness.session.Session
    workspace: Path = field(default_factory=Path.cwd)
    project_workspace: Path | None = None
    work_mode: str = "development"
    pending_images: list[Path] = field(default_factory=list)  # obrázky k přiložení do další zprávy
    changes: Any = None            # harness.changes.ChangeJournal
    processes: Any = None          # harness.processes.ProcessManager
    repo_index: Any = None         # harness.repo_index.RepoIndex
    research: Any = None           # harness.research.ResearchLedger
    task_plan: Any = None          # harness.task_plan.TaskPlanStore
    browser: Any = None            # harness.browser.BrowserSession
    code_index: Any = None         # harness.code_index.CodeIndex
    abort_flag: Any = None         # threading.Event shared with the active agent run

    def resolve(self, path: str) -> Path:
        """Relativní cesty řeší od workspace, absolutní ponechá."""
        p = Path(path)
        return p if p.is_absolute() else (self.workspace / p)


class Tool:
    name: str = ""
    description: str = ""
    parameters: dict = {}          # JSON schema vlastností
    risk: Risk = Risk.SAFE
    required: list[str] = []
    parallel_safe: bool = False

    def run(self, ctx: AgentContext, **kwargs) -> str:
        raise NotImplementedError

    # ------------------------------------------------------------------
    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    **({"required": self.required} if self.required else {}),
                },
            },
        }


class ToolOutcome(str):
    """String-compatible result with a machine-readable execution outcome."""
    def __new__(cls, text, *, status="completed", tool=""):
        value = super().__new__(cls, str(text))
        value.status = status
        value.tool = tool
        return value


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    # ------------------------------------------------------------------
    def execute(self, name: str, arguments: dict, ctx: AgentContext) -> str:
        """Vykoná nástroj; výjimky zachytí a vrátí jako text (model na ně reaguje)."""
        tool = self.get(name)
        if tool is None:
            return ToolOutcome(f"ERROR: Unknown tool '{name}'. Available: {', '.join(self.names())}", status="error", tool=name)
        try:
            result = tool.run(ctx, **arguments)
            if isinstance(result, ToolOutcome):
                return result
            return ToolOutcome(result, status="error" if str(result).startswith("ERROR") else "completed", tool=name)
        except TypeError as e:
            return ToolOutcome(f"ERROR: Invalid arguments for {name}: {e}", status="error", tool=name)
        except Exception as e:
            tb = traceback.format_exc(limit=3)
            return ToolOutcome(f"ERROR in {name}: {type(e).__name__}: {e}\n{tb}", status="error", tool=name)


def truncate(text: str, limit: int = 20000, label: str = "output",
             head_ratio: float = 0.35) -> str:
    """Inteligentní ořez výstupu: uchová začátek i konec (head+tail).

    Zabrání ztrátě chybových hlášek, test výsledků a stack trace na konci logu.
    """
    if len(text) <= limit:
        return text
    head_len = int(limit * head_ratio)
    tail_len = max(0, limit - head_len)
    head = text[:head_len]
    tail = text[-tail_len:] if tail_len > 0 else ""
    omitted = len(text) - (head_len + tail_len)
    omitted_lines = text[head_len:len(text) - tail_len].count("\n")
    lines_info = f"~{omitted_lines} lines / " if omitted_lines > 0 else ""
    return f"{head}\n\n... [{label} truncated: {lines_info}{omitted:,} chars omitted] ...\n\n{tail}"
