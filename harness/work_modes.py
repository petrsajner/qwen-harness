"""Jednotné uživatelské pracovní režimy a jejich capability mapování."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkModeSpec:
    id: str
    label: str
    agent_mode: str
    repo_snapshot: bool = False
    task_protocol: bool = False


WORK_MODES = {
    "discussion": WorkModeSpec("discussion", "Diskuze", "chat"),
    "research": WorkModeSpec("research", "Výzkum", "chat"),
    "writing": WorkModeSpec("writing", "Psaní", "agent", task_protocol=True),
    "development": WorkModeSpec(
        "development", "Vývoj", "agent", repo_snapshot=True, task_protocol=True),
    "computer": WorkModeSpec("computer", "Počítač", "computer", task_protocol=True),
}


def normalize_work_mode(value: str | None, legacy_mode: str | None = None) -> str:
    if value in WORK_MODES:
        return str(value)
    return {"chat": "discussion", "computer": "computer"}.get(
        str(legacy_mode), "development")


def mode_choices() -> list[tuple[str, str]]:
    return [(spec.label, spec.id) for spec in WORK_MODES.values()]
