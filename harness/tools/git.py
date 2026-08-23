"""Strukturované Git nástroje pro coding agenta."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from harness.safety import Risk
from harness.tools.base import AgentContext, Tool, truncate


def _git(ctx: AgentContext, args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(ctx.workspace), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=0x08000000,
    )


def _result(proc: subprocess.CompletedProcess, label: str, limit: int = 40_000) -> str:
    output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    output = output.strip() or "(no output)"
    return f"{label}\n[exit code: {proc.returncode}]\n{truncate(output, limit, 'git output')}"


class GitStatusTool(Tool):
    name = "git_status"
    parallel_safe = True
    description = "Return the current branch and concise staged/unstaged/untracked file status."
    parameters = {}

    def run(self, ctx: AgentContext) -> str:
        try:
            proc = _git(ctx, ["status", "--short", "--branch"])
            return _result(proc, "$ git status --short --branch")
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"ERROR: git status failed: {exc}"


class GitDiffTool(Tool):
    name = "git_diff"
    parallel_safe = True
    description = "Return a Git diff for the workspace or one path, optionally for staged changes."
    parameters = {
        "path": {"type": "string", "description": "Optional workspace-relative file path"},
        "staged": {"type": "boolean", "description": "Show staged diff (default false)"},
        "stat_only": {"type": "boolean", "description": "Show summary only (default false)"},
    }

    def run(self, ctx: AgentContext, path: str | None = None,
            staged: bool = False, stat_only: bool = False) -> str:
        args = ["diff"]
        if staged:
            args.append("--cached")
        if stat_only:
            args.append("--stat")
        if path:
            args.extend(["--", path])
        try:
            return _result(_git(ctx, args), "$ git " + " ".join(args))
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"ERROR: git diff failed: {exc}"


class GitCommitTool(Tool):
    name = "git_commit"
    description = ("Stage selected paths and create a local commit. If paths are omitted, only files "
                   "recorded in the current task checkpoint are committed. Never pushes.")
    parameters = {
        "message": {"type": "string", "description": "Commit message"},
        "paths": {
            "type": "array", "items": {"type": "string"},
            "description": "Optional explicit workspace-relative paths",
        },
    }
    required = ["message"]
    risk = Risk.WRITE

    def run(self, ctx: AgentContext, message: str, paths: list[str] | None = None) -> str:
        message = (message or "").strip()
        if not message:
            return "ERROR: commit message must not be empty"
        selected = list(paths or self._task_paths(ctx))
        if not selected:
            return "ERROR: no current-task files to commit; pass explicit paths if intended"
        try:
            add = _git(ctx, ["add", "--", *selected])
            if add.returncode:
                return _result(add, "$ git add -- " + " ".join(selected))
            commit = _git(ctx, ["commit", "-m", message], timeout=60)
            return _result(commit, f"$ git commit -m {json.dumps(message, ensure_ascii=False)}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"ERROR: git commit failed: {exc}"

    @staticmethod
    def _task_paths(ctx: AgentContext) -> list[str]:
        if not ctx.changes:
            return []
        selected: list[str] = []
        for item in ctx.changes.summary().get("files", []):
            if not item.get("changed"):
                continue
            path = Path(item["path"])
            if path.is_absolute():
                try:
                    path = path.relative_to(ctx.workspace)
                except ValueError:
                    continue
            selected.append(str(path))
        return selected


def register_git_tools(registry) -> None:
    registry.register(GitStatusTool())
    registry.register(GitDiffTool())
    registry.register(GitCommitTool())
