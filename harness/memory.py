"""Trvala pamet modelu: globalni, pro pracovni rezim a projektova.

Soubory:
  global:      {app}/memory/GLOBAL.md
  development: {app}/memory/MEMORY.md (puvodni globalni coding pamet)
  other modes: {app}/memory/modes/<work_mode>.md
  project:     {workspace}/QWEN_MEMORY.md

Vsechny tri aktivni vrstvy se vkladaji cele do system promptu pri startu ulohy
a po kompresi. Model je muze cist a doplnovat pres memory nastroje.
"""
from __future__ import annotations

from pathlib import Path

from harness.config import Config
from harness.work_modes import WORK_MODES, normalize_work_mode


GLOBAL_TEMPLATE = """# Global memory

<!-- Facts and preferences that apply across all work modes and projects.
     To write here use save_memory with scope="global". Keep it brief, one fact per line. -->
"""


def _mode_template(work_mode: str) -> str:
    label = WORK_MODES[work_mode].label
    return f"""# Work mode memory: {label}

<!-- Facts, rules and preferences that apply to the {label} mode across projects.
     To write here use save_memory with scope="mode". Keep it brief, one fact per line. -->
"""


class MemoryStore:
    def __init__(self, cfg: Config, workspace: Path | None = None,
                 work_mode: str | None = None):
        self.cfg = cfg
        self.workspace = Path(workspace) if workspace else None
        self.work_mode = normalize_work_mode(
            work_mode or cfg.data.get("work_mode"), cfg.agent.get("mode", "agent"))
        settings = cfg.data.get("memory", {})
        directory = settings.get("directory", settings.get("global_dir", "memory"))
        self.base_dir = cfg.root / directory
        self.global_path = self.base_dir / settings.get("global_filename", "GLOBAL.md")
        self.modes_dir = self.base_dir / settings.get("modes_directory", "modes")
        self.development_filename = settings.get("development_filename", "MEMORY.md")
        self.project_filename = settings.get("project_filename", "QWEN_MEMORY.md")
        self._ensure_global()
        self._ensure_mode()

    def _ensure_global(self) -> None:
        self.global_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.global_path.exists():
            self.global_path.write_text(GLOBAL_TEMPLATE, encoding="utf-8")

    def mode_path(self) -> Path:
        if self.work_mode == "development":
            return self.base_dir / self.development_filename
        return self.modes_dir / f"{self.work_mode}.md"

    def _ensure_mode(self) -> None:
        path = self.mode_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(_mode_template(self.work_mode), encoding="utf-8")
            return
        if self.work_mode != "development":
            return
        try:
            text = path.read_text(encoding="utf-8")
            legacy = "# 🧠 Globální paměť (platí pro všechny projekty)"
            if text.startswith(legacy):
                text = text.replace(legacy, "# Work mode memory: Development", 1)
                text = text.replace('scope="global"', 'scope="mode"', 1)
                path.write_text(text, encoding="utf-8")
        except OSError:
            pass

    def project_path(self) -> Path | None:
        if self.workspace is None:
            return None
        return self.workspace / self.project_filename

    def ensure_project(self) -> None:
        path = self.project_path()
        if path is None:
            return
        try:
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    f"# Project memory ({self.project_filename})\n\n"
                    "<!-- Facts that apply to this project only. "
                    "To write here use save_memory with scope=\"project\". -->\n",
                    encoding="utf-8")
        except OSError:
            pass

    def _path_for(self, scope: str) -> Path | None:
        if scope == "global":
            return self.global_path
        if scope == "mode":
            return self.mode_path()
        if scope == "project":
            return self.project_path()
        return None

    def read(self, scope: str) -> str:
        if scope not in ("global", "mode", "project"):
            return f"ERROR: Neznámá vrstva paměti: {scope}"
        path = self._path_for(scope)
        if path is None:
            return "ERROR: Není nastavený projekt; projektová paměť není aktivní."
        if not path.exists():
            return "(prázdné)"
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            return f"ERROR: nelze číst {path}: {exc}"

    def append(self, fact: str, scope: str) -> str:
        fact = (fact or "").strip()
        if not fact:
            return "ERROR: prázdný fakt - není co uložit."
        if scope not in ("global", "mode", "project"):
            return f"ERROR: Neznámá vrstva paměti: {scope}"
        path = self._path_for(scope)
        if path is None:
            return "ERROR: Není nastavený projekt; projektovou paměť nelze použít."
        if scope == "project":
            self.ensure_project()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(f"- {fact}\n")
            labels = {"global": "globální", "mode": "režimové", "project": "projektové"}
            return f"OK: uloženo do {labels[scope]} paměti: {fact[:80]}"
        except OSError as exc:
            return f"ERROR: nelze zapsat {path}: {exc}"

    def context_block(self) -> str:
        """Vsechny tri aktivni vrstvy pameti, bez umeleho zkracovani."""
        label = WORK_MODES[self.work_mode].label
        parts = [
            "## PERSISTENT MEMORY",
            "The following durable memory layers all apply to this chat. Use all of them. "
            "Store universal user facts in scope 'global', facts shared by this work mode "
            "in scope 'mode', and project-specific facts in scope 'project'.",
            f"### GLOBAL MEMORY (all work modes and projects)\n{self.read('global').strip()}",
            f"### WORK MODE MEMORY ({label})\n{self.read('mode').strip()}",
        ]
        if self.project_path() is not None:
            parts.append(
                f"### PROJECT MEMORY (workspace: {self.workspace.name})\n"
                f"{self.read('project').strip()}")
        else:
            parts.append("### PROJECT MEMORY: no project selected (project layer inactive)")
        return "\n\n".join(parts)
