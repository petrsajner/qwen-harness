"""Persistent operational task plan for long-running agent work."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any


STEP_STATUSES = {"pending", "in_progress", "completed", "skipped"}
VALIDATION_COMMAND_RE = re.compile(
    r"(?:^|\s)(?:pytest|unittest|ctest|ruff|mypy|pyright|tsc|"
    r"cargo\s+test|go\s+test|dotnet\s+(?:test|build)|mvn\s+test|gradle\s+test|"
    r"npm\s+(?:test|run\s+(?:test|check|lint|typecheck|build))|"
    r"pnpm\s+(?:test|run\s+(?:test|check|lint|typecheck|build)))(?:\s|$)",
    re.IGNORECASE,
)


class TaskPlanStore:
    """Small durable task ledger stored next to the active chat."""

    def __init__(self, session):
        self.session = session

    @property
    def path(self) -> Path:
        return self.session.dir / "task-plan.json"

    def begin(self, goal: str) -> dict[str, Any]:
        now = time.time()
        data = {
            "goal": str(goal or "").strip()[:1000],
            "status": "active",
            "steps": [],
            "validations": [],
            "validation_processes": {},
            "diff_reviewed": False,
            "review_nudged": False,
            "notes": [],
            "created": now,
            "updated": now,
        }
        self._write(data)
        return data

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("steps", [])
                data.setdefault("validations", [])
                data.setdefault("validation_processes", {})
                data.setdefault("active_paths", [])
                data.setdefault("notes", [])
                data.setdefault("diff_reviewed", False)
                data.setdefault("review_nudged", False)
                return data
        except (OSError, ValueError):
            pass
        return {}

    def set_plan(self, goal: str, steps: list[str]) -> dict[str, Any]:
        data = self.load() or self.begin(goal)
        cleaned = [str(step).strip() for step in (steps or []) if str(step).strip()]
        data["goal"] = str(goal or data.get("goal") or "").strip()[:1000]
        data["steps"] = [
            {"id": index, "text": text[:500],
             "status": "in_progress" if index == 1 else "pending", "note": ""}
            for index, text in enumerate(cleaned, 1)
        ]
        data["status"] = "active"
        data["updated"] = time.time()
        self._write(data)
        return data

    def update_step(self, step_id: int, status: str, note: str = "") -> dict[str, Any]:
        if status not in STEP_STATUSES:
            raise ValueError(f"Unknown task step status: {status}")
        data = self.load()
        steps = data.get("steps") or []
        selected = next((step for step in steps if int(step.get("id", 0)) == int(step_id)), None)
        if selected is None:
            raise ValueError(f"Task step {step_id} does not exist")
        if status == "in_progress":
            for step in steps:
                if step is not selected and step.get("status") == "in_progress":
                    step["status"] = "pending"
        selected["status"] = status
        if note:
            selected["note"] = str(note)[:1000]
        if status == "completed":
            next_pending = next((step for step in steps if step.get("status") == "pending"), None)
            if next_pending and not any(step.get("status") == "in_progress" for step in steps):
                next_pending["status"] = "in_progress"
        unfinished = [step for step in steps
                      if step.get("status") in ("pending", "in_progress")]
        data["status"] = "active" if unfinished else "complete"
        data["updated"] = time.time()
        self._write(data)
        return data

    def add_note(self, note: str) -> None:
        if not note:
            return
        data = self.load()
        notes = list(data.get("notes") or [])
        notes.append({"text": str(note)[:1000], "time": time.time()})
        data["notes"] = notes[-20:]
        data["updated"] = time.time()
        self._write(data)

    def record_validation(self, label: str, status: str, summary: str = "") -> None:
        data = self.load()
        validations = list(data.get("validations") or [])
        validations.append({
            "label": str(label)[:500],
            "status": str(status)[:40],
            "summary": str(summary)[:1500],
            "time": time.time(),
        })
        data["validations"] = validations[-20:]
        data["updated"] = time.time()
        self._write(data)

    def track_validation_process(self, process_id: str, label: str) -> None:
        data = self.load()
        pending = dict(data.get("validation_processes") or {})
        pending[str(process_id)] = str(label)[:800]
        data["validation_processes"] = pending
        data["updated"] = time.time()
        self._write(data)

    def set_active_paths(self, paths: list[str]) -> None:
        data = self.load()
        data["active_paths"] = [str(path) for path in paths[-20:]]
        data["updated"] = time.time()
        self._write(data)

    def observe_tool(self, name: str, args: dict[str, Any] | None, result: str,
                     processes=None) -> None:
        args = args or {}
        if name == "git_diff" and "[exit code: 0]" in result:
            data = self.load()
            data["diff_reviewed"] = True
            data["updated"] = time.time()
            self._write(data)
            return
        if name == "run_command":
            command = str(args.get("command") or "")
            if VALIDATION_COMMAND_RE.search(command):
                match = re.search(r"\[exit code:\s*(-?\d+)\]", result)
                code = int(match.group(1)) if match else None
                status = "passed" if code == 0 else "failed"
                self.record_validation(command, status, result[-1200:])
            return
        if name == "poll_command":
            try:
                payload = json.loads(result)
            except ValueError:
                return
            if payload.get("status") != "finished":
                return
            process_id = str(payload.get("process_id") or args.get("process_id") or "")
            item = processes.get(process_id) if processes and process_id else None
            command = str(getattr(item, "command", "") or "")
            data = self.load()
            pending = dict(data.get("validation_processes") or {})
            label = pending.pop(process_id, "")
            if label or (command and VALIDATION_COMMAND_RE.search(command)):
                code = payload.get("exit_code")
                self.record_validation(
                    label or command, "passed" if code == 0 else "failed",
                    str(payload.get("output") or "")[-1200:],
                )
                refreshed = self.load()
                refreshed["validation_processes"] = pending
                refreshed["updated"] = time.time()
                self._write(refreshed)

    def readiness(self, has_changes: bool) -> list[str]:
        if not has_changes:
            return []
        data = self.load()
        issues: list[str] = []
        steps = data.get("steps") or []
        unfinished = [step for step in steps
                      if step.get("status") in ("pending", "in_progress")]
        if unfinished:
            issues.append(f"{len(unfinished)} task plan step(s) are still unfinished")
        if not any(item.get("status") == "passed" for item in data.get("validations") or []):
            issues.append("no successful validation is recorded")
        if not data.get("diff_reviewed"):
            issues.append("the final Git diff has not been reviewed")
        return issues

    def mark_review_nudged(self) -> None:
        data = self.load()
        data["review_nudged"] = True
        data["updated"] = time.time()
        self._write(data)

    def review_nudged(self) -> bool:
        return bool(self.load().get("review_nudged"))

    def context_block(self) -> str:
        data = self.load()
        if not data:
            return "No operational task plan has been created yet."
        lines = [f"Goal: {data.get('goal') or '(not set)'}",
                 f"Plan status: {data.get('status', 'active')}"]
        for step in data.get("steps") or []:
            note = f" - {step.get('note')}" if step.get("note") else ""
            lines.append(
                f"{step.get('id')}. [{step.get('status', 'pending')}] "
                f"{step.get('text', '')}{note}")
        validations = data.get("validations") or []
        if validations:
            last = validations[-1]
            lines.append(
                f"Latest validation: [{last.get('status')}] {last.get('label')}")
        lines.append(f"Git diff reviewed: {'yes' if data.get('diff_reviewed') else 'no'}")
        return "\n".join(lines)

    def _write(self, data: dict[str, Any]) -> None:
        if self.session.transient:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)
