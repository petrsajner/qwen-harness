"""Správce projektů - interní registr projektů aplikace.

projects.json v kořenu: [{"id", "name", "path", "created"}]
- Nový projekt: vytvoří složku v {projects.root_dir}/{jméno} a zaregistruje ji
- Připojení složky: zaregistruje existující složku (projekt se jmenuje dle složky)
- Workspace session = path vybraného projektu
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from harness.config import Config


def _safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "-", name).strip(". ")
    return name or "projekt"


class Projects:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.file = cfg.root / "projects.json"
        p = cfg.data.get("projects", {})
        self.root_dir = cfg.root / p.get("root_dir", "projects")

    # ------------------------------------------------------------------
    def _load(self) -> list[dict]:
        try:
            return json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _save(self, items: list[dict]) -> None:
        self.file.write_text(json.dumps(items, ensure_ascii=False, indent=1),
                             encoding="utf-8")

    # ------------------------------------------------------------------
    def list_all(self) -> list[dict]:
        items = self._load()
        for it in items:  # doplň chybějící složku (např. po smazání na disku)
            if not Path(it["path"]).is_dir():
                it["missing"] = True
        return items

    def by_path(self, path: str) -> dict | None:
        return next((p for p in self._load() if p["path"] == path), None)

    def create_new(self, name: str) -> dict:
        """Nový projekt: vytvoří složku v projects rootu a zaregistruje ji."""
        name = _safe_name(name)
        folder = self.root_dir / name
        i = 2
        while folder.exists():  # unikátní jméno
            folder = self.root_dir / f"{name}-{i}"
            i += 1
        folder.mkdir(parents=True, exist_ok=True)
        proj = {"id": uuid.uuid4().hex[:8], "name": folder.name,
                "path": str(folder), "created": time.time()}
        items = self._load()
        items.append(proj)
        self._save(items)
        return proj

    def attach_folder(self, path: str) -> dict:
        """Připoj existující složku jako projekt (jméno dle složky)."""
        p = Path(path).resolve()
        if not p.is_dir():
            raise ValueError(f"Adresář neexistuje: {p}")
        existing = self.by_path(str(p))
        if existing:
            return existing
        proj = {"id": uuid.uuid4().hex[:8], "name": p.name,
                "path": str(p), "created": time.time()}
        items = self._load()
        items.append(proj)
        self._save(items)
        return proj

    def ensure_registered(self, path: str) -> dict | None:
        """Workspace bez registru → zaregistruj (migrace ze starších verzí)."""
        if not path:
            return None
        try:
            return self.attach_folder(path)
        except ValueError:
            return None
