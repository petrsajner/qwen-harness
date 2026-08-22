"""Trvalá paměť modelu - globální (instalace) a projektová (workspace).

Soubory:
  global:  {app}/memory/MEMORY.md            - preference, obecná pravidla
  project: {workspace}/QWEN_MEMORY.md        - fakta platná pro daný projekt

Paměť se vkládá do system promptu (start úlohy + po kompresi); model ji
doplňuje přes nástroj save_memory, uživatel ji může libovolně upravovat.
"""
from __future__ import annotations

import time
from pathlib import Path

from harness.config import Config

GLOBAL_TEMPLATE = """# 🧠 Globální paměť (platí pro všechny projekty)

<!-- Model sem ukládá obecná pravidla a preference uživatele (nástrojem save_memory,
     scope="global"). Tento soubor můžeš libovolně upravovat - model změnu uvidí
     při dalším startu úlohy. Piš stručně, po jednom faktu na řádek. -->
"""


class MemoryStore:
    def __init__(self, cfg: Config, workspace: Path | None = None):
        self.cfg = cfg
        self.workspace = workspace
        m = cfg.data.get("memory", {})
        self.global_path = cfg.root / m.get("global_dir", "memory") / "MEMORY.md"
        self.project_filename: str = m.get("project_filename", "QWEN_MEMORY.md")
        self.max_chars = int(m.get("max_chars", 6000))
        self._ensure_global()

    # ------------------------------------------------------------------
    def _ensure_global(self) -> None:
        self.global_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.global_path.exists():
            self.global_path.write_text(GLOBAL_TEMPLATE, encoding="utf-8")

    def project_path(self) -> Path | None:
        if not self.workspace:
            return None
        return Path(self.workspace) / self.project_filename

    # ------------------------------------------------------------------
    def read(self, scope: str) -> str:
        path = self.global_path if scope == "global" else self.project_path()
        if path is None:
            return "ERROR: Není nastavený workspace (projektová paměť bez projektu neexistuje)."
        if not path.exists():
            return ("(prázdné - paměť pro tento projekt dosud neexistuje; "
                    "první uložení ji vytvoří)" if scope == "project" else "(prázdné)")
        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            return f"ERROR: nelze číst {path}: {e}"

    def append(self, fact: str, scope: str) -> str:
        fact = (fact or "").strip()
        if not fact:
            return "ERROR: prázdný fakt - není co uložit."
        if scope == "global":
            path = self.global_path
        else:
            path = self.project_path()
            if path is None:
                return "ERROR: Není nastavený workspace - projektovou paměť nelze použít."
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            existed = path.exists()
            line = f"- {fact}\n"
            if not existed and scope == "project":
                path.write_text(
                    f"# 🧠 Paměť projektu ({self.project_filename})\n\n"
                    f"<!-- Fakta platná pro tento projekt. Můžeš ručně upravovat. -->\n\n{line}",
                    encoding="utf-8")
            else:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line)
            return f"OK: uloženo do {'globální' if scope == 'global' else 'projektové'} paměti: {fact[:80]}"
        except OSError as e:
            return f"ERROR: nelze zapsat {path}: {e}"

    # ------------------------------------------------------------------
    def _trunc(self, text: str) -> str:
        text = text.strip()
        if len(text) <= self.max_chars:
            return text
        return text[:self.max_chars] + f"\n… (zkráceno na {self.max_chars} znaků; plný obsah přes read_memory)"

    def context_block(self) -> str:
        """Blok pro system prompt - obě paměti, zkrácené."""
        parts = ["## PERSISTENT MEMORY",
                 "Facts below were saved by you earlier or by the user (durable knowledge - "
                 "apply without re-asking). To store a new fact use the save_memory tool "
                 "(scope 'project' for workspace-specific facts, 'global' for general "
                 "preferences/rules) when the user asks or when a fact is clearly worth "
                 "persisting. Prefer concise one-line entries."]
        g = self._trunc(self.read("global"))
        parts.append(f"### GLOBAL MEMORY (all projects)\n{g}")
        p = self.project_path()
        if p is not None:
            pv = self._trunc(self.read("project"))
            ws_name = Path(self.workspace).name
            parts.append(f"### PROJECT MEMORY (workspace: {ws_name})\n{pv}")
        else:
            parts.append("### PROJECT MEMORY: no workspace set (project memory inactive)")
        return "\n\n".join(parts)
