"""Nástroje trvalé paměti - globální, režimová a projektová vrstva."""
from __future__ import annotations

from harness.safety import Risk
from harness.tools.base import AgentContext, Tool


class ReadMemoryTool(Tool):
    name = "read_memory"
    parallel_safe = True
    description = ("Read one full persistent memory layer. scope: 'global' = all work modes "
                   "and projects; 'mode' = the current work mode across projects; "
                   "'project' = the current project's facts. All active layers are already "
                   "included in the system prompt.")
    parameters = {
        "scope": {"type": "string", "enum": ["global", "mode", "project"],
                  "description": "Which memory to read"},
    }
    required = ["scope"]
    risk = Risk.SAFE

    def run(self, ctx: AgentContext, scope: str = "mode") -> str:
        from harness.memory import MemoryStore
        store = MemoryStore(ctx.cfg, ctx.project_workspace, ctx.work_mode)
        return store.read(scope) or "(prázdné)"


class SaveMemoryTool(Tool):
    name = "save_memory"
    description = ("Save a durable fact to persistent memory (one concise line). "
                   "scope 'project' = facts valid for this workspace; scope 'mode' = facts shared "
                   "by the current kind of work; scope 'global' = universal user facts and rules. "
                   "Use when the user asks to remember something, or when a fact is clearly worth "
                   "persisting for future sessions.")
    parameters = {
        "fact": {"type": "string", "description": "The fact to remember (one concise line)"},
        "scope": {"type": "string", "enum": ["global", "mode", "project"],
                  "description": "Which memory layer should own the fact"},
    }
    required = ["fact", "scope"]
    risk = Risk.SAFE  # append-only na pevnou cestu mimo uživatelské soubory

    def run(self, ctx: AgentContext, fact: str, scope: str = "mode") -> str:
        from harness.memory import MemoryStore
        store = MemoryStore(ctx.cfg, ctx.project_workspace, ctx.work_mode)
        return store.append(fact, scope)


def register_memory_tools(registry) -> None:
    registry.register(ReadMemoryTool())
    registry.register(SaveMemoryTool())
