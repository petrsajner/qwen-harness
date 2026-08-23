"""Jediny zdroj viditelne verze aplikace pro source i instalovany build."""
from __future__ import annotations

import sys
from pathlib import Path


def _version_candidates() -> list[Path]:
    root = Path(__file__).resolve().parent.parent
    candidates = [root / "version.txt", root / "installer" / "version.txt"]
    if getattr(sys, "frozen", False):
        candidates.insert(0, Path(sys.executable).resolve().parent / "version.txt")
    return candidates


def read_app_version() -> str:
    for path in _version_candidates():
        try:
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        except OSError:
            continue
    return "development"


APP_VERSION = read_app_version()
