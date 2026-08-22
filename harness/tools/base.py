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
    pending_images: list[Path] = field(default_factory=list)  # obrázky k přiložení do další zprávy
    changes: Any = None            # harness.changes.ChangeJournal
    processes: Any = None          # harness.processes.ProcessManager
    repo_index: Any = None         # harness.repo_index.RepoIndex
    research: Any = None           # harness.research.ResearchLedger

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
            return f"ERROR: Unknown tool '{name}'. Available: {', '.join(self.names())}"
        try:
            return tool.run(ctx, **arguments)
        except TypeError as e:
            return f"ERROR: Invalid arguments for {name}: {e}"
        except Exception as e:
            tb = traceback.format_exc(limit=3)
            return f"ERROR in {name}: {type(e).__name__}: {e}\n{tb}"


def truncate(text: str, limit: int = 20000, label: str = "output") -> str:
    """Ořízne dlouhý výstup s hláškou pro model."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [{label} truncated, {len(text) - limit} chars omitted]"
