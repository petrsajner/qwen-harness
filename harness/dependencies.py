"""Detekce a synchronizace Python zavislosti podle obsahu requirements.txt."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


MARKER_NAME = ".requirements.sha256"
LEGACY_MARKER_NAME = ".deps.ok"


def requirements_digest(requirements: Path) -> str:
    return hashlib.sha256(requirements.read_bytes()).hexdigest()


def dependency_marker(venv_dir: Path) -> Path:
    return venv_dir / MARKER_NAME


def dependencies_current(requirements: Path, venv_dir: Path) -> bool:
    python = venv_dir / "Scripts" / "python.exe"
    marker = dependency_marker(venv_dir)
    if not python.is_file() or not requirements.is_file() or not marker.is_file():
        return False
    try:
        return marker.read_text(encoding="ascii").strip() == requirements_digest(requirements)
    except OSError:
        return False


def mark_dependencies_current(requirements: Path, venv_dir: Path) -> None:
    marker = dependency_marker(venv_dir)
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_name(marker.name + ".tmp")
    temporary.write_text(requirements_digest(requirements) + "\n", encoding="ascii")
    temporary.replace(marker)
    (venv_dir / LEGACY_MARKER_NAME).unlink(missing_ok=True)


def sync_dependencies(requirements: Path, venv_dir: Path, *, force: bool = False) -> int:
    if dependencies_current(requirements, venv_dir) and not force:
        print("Python zavislosti odpovidaji requirements.txt - preskakuji.")
        return 0

    python = venv_dir / "Scripts" / "python.exe"
    if not python.is_file():
        print(f"[CHYBA] Python prostredi nenalezeno: {python}")
        return 1
    if not requirements.is_file():
        print(f"[CHYBA] Soubor zavislosti nenalezen: {requirements}")
        return 1

    rc = subprocess.call([str(python), "-m", "pip", "install", "-r", str(requirements)])
    if rc == 0:
        mark_dependencies_current(requirements, venv_dir)
    return rc
