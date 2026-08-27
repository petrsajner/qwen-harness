"""Tools for a visible, persistent operational task plan."""
from __future__ import annotations

import json

from harness.tools.base import AgentContext, Tool


class TaskPlanStatusTool(Tool):
    name = "task_plan_status"
    parallel_safe = True
    description = "Read the current persistent task plan, step states, and validation status."
    parameters = {}

    def run(self, ctx: AgentContext) -> str:
        if not ctx.task_plan:
            return "ERROR: task plan unavailable"
        return json.dumps(ctx.task_plan.load(), ensure_ascii=False, indent=2)


class SetTaskPlanTool(Tool):
    name = "set_task_plan"
    description = ("Create or replace the operational plan for a multi-step task. Keep steps "
                   "concrete and outcome-oriented; the first step becomes in_progress.")
    parameters = {
        "goal": {"type": "string", "description": "Concise task goal"},
        "steps": {
            "type": "array", "items": {"type": "string"},
            "description": "Ordered implementation and verification steps",
        },
    }
    required = ["goal", "steps"]

    def run(self, ctx: AgentContext, goal: str, steps: list[str]) -> str:
        if not ctx.task_plan:
            return "ERROR: task plan unavailable"
        return json.dumps(ctx.task_plan.set_plan(goal, steps), ensure_ascii=False, indent=2)


class UpdateTaskStepTool(Tool):
    name = "update_task_step"
    description = ("Update one task-plan step after meaningful progress. Completing a step "
                   "automatically starts the next pending step.")
    parameters = {
        "step_id": {"type": "integer"},
        "status": {
            "type": "string",
            "enum": ["pending", "in_progress", "completed", "skipped"],
        },
        "note": {"type": "string", "description": "Optional result or reason"},
    }
    required = ["step_id", "status"]

    def run(self, ctx: AgentContext, step_id: int, status: str,
            note: str = "") -> str:
        if not ctx.task_plan:
            return "ERROR: task plan unavailable"
        return json.dumps(
            ctx.task_plan.update_step(step_id, status, note),
            ensure_ascii=False, indent=2)


class RecordTaskValidationTool(Tool):
    name = "record_task_validation"
    description = ("Record a validation result that was inspected outside the automatic project "
                   "check tools, including a concise result summary.")
    parameters = {
        "label": {"type": "string"},
        "status": {"type": "string", "enum": ["passed", "failed", "skipped"]},
        "summary": {"type": "string"},
    }
    required = ["label", "status"]

    def run(self, ctx: AgentContext, label: str, status: str,
            summary: str = "") -> str:
        if not ctx.task_plan:
            return "ERROR: task plan unavailable"
        ctx.task_plan.record_validation(label, status, summary)
        return "OK: validation recorded"


def register_task_tools(registry) -> None:
    registry.register(TaskPlanStatusTool())
    registry.register(SetTaskPlanTool())
    registry.register(UpdateTaskStepTool())
    registry.register(RecordTaskValidationTool())
