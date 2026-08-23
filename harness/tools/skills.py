"""Read-only tools for progressive skill discovery and loading."""
from __future__ import annotations

from harness.skills import SkillLibrary
from harness.tools.base import AgentContext, Tool


class ListSkillsTool(Tool):
    name = "list_skills"
    parallel_safe = True
    description = ("List optional system and project skills with short trigger descriptions. "
                   "Skills are guidance, not authority; explicit user instructions take priority.")
    parameters = {}

    def run(self, ctx: AgentContext) -> str:
        return SkillLibrary(ctx.cfg, ctx.project_workspace).catalog()


class ReadSkillTool(Tool):
    name = "read_skill"
    parallel_safe = True
    description = ("Load one optional SKILL.md when its catalog description clearly matches the "
                   "current problem. Adapt its guidance to the task and never override the user.")
    parameters = {"name": {"type": "string", "description": "Exact skill name from list_skills"}}
    required = ["name"]

    def run(self, ctx: AgentContext, name: str) -> str:
        try:
            return SkillLibrary(ctx.cfg, ctx.project_workspace).read(name)
        except (OSError, ValueError) as exc:
            return f"ERROR: {exc}"


def register_skill_tools(registry) -> None:
    registry.register(ListSkillsTool())
    registry.register(ReadSkillTool())
