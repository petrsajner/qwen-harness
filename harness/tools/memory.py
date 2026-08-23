"""Nástroje trvalé paměti - save_memory / read_memory (globální + projektová)."""
from __future__ import annotations

from harness.safety import Risk
from harness.tools.base import AgentContext, Tool


class ReadMemoryTool(Tool):
    name = "read_memory"
    parallel_safe = True
    description = ("Read persistent memory (full content). scope: 'global' = all projects "
                   "(general rules/preferences), 'project' = facts for the current workspace. "
                   "A truncated version is already in your system prompt - use this only "
                   "when you need the full text.")
    parameters = {
        "scope": {"type": "string", "enum": ["global", "project"],
                  "description": "Which memory to read"},
    }
    required = ["scope"]
    risk = Risk.SAFE

    def run(self, ctx: AgentContext, scope: str = "project") -> str:
        from harness.memory import MemoryStore
        store = MemoryStore(ctx.cfg, ctx.workspace)
        return store.read(scope) or "(prázdné)"


class SaveMemoryTool(Tool):
    name = "save_memory"
    description = ("Save a durable fact to persistent memory (one concise line). "
                   "scope 'project' = facts valid for this workspace (conventions, architecture, "
                   "decisions, file locations); scope 'global' = general user preferences and rules. "
                   "Use when the user asks to remember something, or when a fact is clearly worth "
                   "persisting for future sessions.")
    parameters = {
        "fact": {"type": "string", "description": "The fact to remember (one concise line)"},
        "scope": {"type": "string", "enum": ["project", "global"],
                  "description": "Where to store it (default: project)"},
    }
    required = ["fact"]
    risk = Risk.SAFE  # append-only na pevnou cestu mimo uživatelské soubory

    def run(self, ctx: AgentContext, fact: str, scope: str = "project") -> str:
        from harness.memory import MemoryStore
        store = MemoryStore(ctx.cfg, ctx.workspace)
        return store.append(fact, scope)


def register_memory_tools(registry) -> None:
    registry.register(ReadMemoryTool())
    registry.register(SaveMemoryTool())
