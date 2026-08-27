"""Project-specific validation commands with conservative auto-detection."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProjectCheck:
    id: str
    label: str
    command: str
    shell: str = "powershell"
    timeout: int = 900
    kind: str = "test"
    primary: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProjectProfile:
    def __init__(self, workspace: Path, python: Path | str):
        self.workspace = Path(workspace).resolve()
        self.python = str(python)

    def checks(self) -> list[ProjectCheck]:
        configured = self._configured_checks()
        return configured if configured else self._detected_checks()

    def select(self, check_id: str = "primary") -> ProjectCheck | None:
        checks = self.checks()
        if not checks:
            return None
        if check_id and check_id != "primary":
            selected = next((item for item in checks
                             if item.id == check_id or item.kind == check_id), None)
            if selected:
                return selected
        return next((item for item in checks if item.primary), checks[0])

    def describe(self) -> str:
        checks = self.checks()
        if not checks:
            return "No validation commands detected. Add .qwen/project.yaml to define them."
        lines = ["Available project validation commands:"]
        for item in checks:
            primary = " (primary)" if item.primary else ""
            lines.append(
                f"- {item.id}: {item.label}{primary}\n"
                f"  [{item.kind}, {item.shell}, timeout {item.timeout}s] {item.command}")
        lines.append(
            "A project can override detection in .qwen/project.yaml under a checks list.")
        return "\n".join(lines)

    def _configured_checks(self) -> list[ProjectCheck]:
        path = self.workspace / ".qwen" / "project.yaml"
        if not path.is_file():
            return []
        try:
            import yaml
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            raw_checks = data.get("checks") or []
        except (OSError, ValueError, TypeError, ImportError):
            return []
        checks: list[ProjectCheck] = []
        for index, raw in enumerate(raw_checks, 1):
            if not isinstance(raw, dict) or not str(raw.get("command") or "").strip():
                continue
            checks.append(ProjectCheck(
                id=str(raw.get("id") or f"check-{index}"),
                label=str(raw.get("label") or raw.get("id") or f"Check {index}"),
                command=str(raw["command"]),
                shell=str(raw.get("shell") or "powershell"),
                timeout=max(1, int(raw.get("timeout") or 900)),
                kind=str(raw.get("kind") or "test"),
                primary=bool(raw.get("primary", index == 1)),
            ))
        return checks

    def _detected_checks(self) -> list[ProjectCheck]:
        checks: list[ProjectCheck] = []
        if (self.workspace / "tests" / "test_core.py").is_file():
            checks.append(ProjectCheck(
                "tests", "Core tests", f"& '{self.python}' 'tests/test_core.py'",
                primary=True))
        elif ((self.workspace / "pyproject.toml").is_file()
              or (self.workspace / "pytest.ini").is_file()
              or (self.workspace / "tests").is_dir()):
            checks.append(ProjectCheck(
                "tests", "Python tests", f"& '{self.python}' -m pytest",
                primary=True))

        pyproject = self.workspace / "pyproject.toml"
        if pyproject.is_file():
            text = pyproject.read_text(encoding="utf-8", errors="replace").lower()
            if "ruff" in text:
                checks.append(ProjectCheck(
                    "lint", "Ruff lint", f"& '{self.python}' -m ruff check .",
                    kind="lint"))
            if "mypy" in text:
                checks.append(ProjectCheck(
                    "typecheck", "Mypy", f"& '{self.python}' -m mypy .",
                    kind="typecheck"))

        package_path = self.workspace / "package.json"
        if package_path.is_file():
            try:
                scripts = (json.loads(package_path.read_text(encoding="utf-8"))
                           .get("scripts") or {})
            except (OSError, ValueError):
                scripts = {}
            for script, kind in (("test", "test"), ("check", "test"),
                                 ("lint", "lint"), ("typecheck", "typecheck"),
                                 ("build", "build")):
                if script not in scripts:
                    continue
                checks.append(ProjectCheck(
                    f"npm-{script}", f"npm {script}", f"npm run {script}",
                    kind=kind, primary=not any(item.primary for item in checks)))

        if (self.workspace / "Cargo.toml").is_file():
            checks.append(ProjectCheck(
                "cargo-test", "Cargo tests", "cargo test", kind="test",
                primary=not any(item.primary for item in checks)))
        if (self.workspace / "go.mod").is_file():
            checks.append(ProjectCheck(
                "go-test", "Go tests", "go test ./...", kind="test",
                primary=not any(item.primary for item in checks)))
        if list(self.workspace.glob("*.sln")) or list(self.workspace.glob("*.csproj")):
            checks.append(ProjectCheck(
                "dotnet-test", ".NET tests", "dotnet test", kind="test",
                primary=not any(item.primary for item in checks)))
        return checks
