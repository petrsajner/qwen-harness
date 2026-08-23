"""Progressively disclosed SKILL.md library for optional agent guidance."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from harness.config import Config


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    path: Path
    source: str


class SkillLibrary:
    def __init__(self, cfg: Config, workspace: Path | None = None):
        self.cfg = cfg
        self.workspace = Path(workspace).resolve() if workspace else None

    def roots(self) -> list[tuple[Path, str]]:
        settings = self.cfg.data.get("skills", {})
        roots = [
            (self.cfg.root / settings.get("directory", "skills"), "system"),
            (self.cfg.root / settings.get("user_directory", "user-skills"), "user"),
        ]
        if self.workspace:
            roots.append((self.workspace / settings.get("project_directory", ".qwen-skills"),
                          "project"))
        return roots

    @staticmethod
    def _parse(path: Path, source: str) -> SkillInfo | None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if not text.startswith("---"):
            return None
        parts = text.split("---", 2)
        if len(parts) != 3:
            return None
        try:
            metadata: Any = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            return None
        name = str(metadata.get("name") or "").strip()
        description = str(metadata.get("description") or "").strip()
        if not name or not description:
            return None
        return SkillInfo(name, description, path.resolve(), source)

    def list(self) -> list[SkillInfo]:
        found: dict[str, SkillInfo] = {}
        for root, source in self.roots():
            if not root.is_dir():
                continue
            for path in sorted(root.glob("*/SKILL.md")):
                info = self._parse(path, source)
                if info:
                    # Project skills intentionally override system skills with the same name.
                    found[info.name] = info
        return sorted(found.values(), key=lambda item: item.name.lower())

    def catalog(self) -> str:
        skills = self.list()
        if not skills:
            return "No optional skills are currently installed."
        return "\n".join(
            f"- `{item.name}` ({item.source}): {item.description}" for item in skills)

    def read(self, name: str, max_chars: int = 60_000) -> str:
        info = next((item for item in self.list() if item.name == name), None)
        if info is None:
            available = ", ".join(item.name for item in self.list()) or "none"
            raise ValueError(f"Unknown skill '{name}'. Available: {available}")
        text = info.path.read_text(encoding="utf-8", errors="replace")
        return text[:max(1000, int(max_chars))]
