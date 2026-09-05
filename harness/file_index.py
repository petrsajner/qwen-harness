"""Shared file discovery and persistent incremental project text search."""
from __future__ import annotations

import os
from contextlib import closing
import sqlite3
import threading
import time
from pathlib import Path

IGNORED = {".git", ".venv", "venv", "node_modules", "runtime", "sessions", "dist",
           "build", "target", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
           ".idea", ".vscode", "ui_dist"}
_cache: dict[str, tuple[float, list[Path]]] = {}
_lock = threading.RLock()


def invalidate_project_files(root: Path):
    with _lock:
        _cache.pop(str(root.resolve()), None)


def project_files(root: Path, *, refresh: bool = False) -> list[Path]:
    root = root.resolve()
    key = str(root)
    with _lock:
        saved = _cache.get(key)
        if saved and not refresh and time.monotonic() - saved[0] < 2:
            return list(saved[1])
    paths = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in IGNORED
                   and not name.startswith(("QwenHarness-Offline-Backup", "Marvin-Offline-Backup"))
                   and not (Path(directory) / name).is_symlink()]
        paths.extend(Path(directory) / name for name in files)
    with _lock:
        _cache[key] = (time.monotonic(), paths)
    return paths


def search_index(root: Path, database: Path, query: str, extensions: set[str], limit: int):
    database.parent.mkdir(parents=True, exist_ok=True)
    with _lock, closing(sqlite3.connect(database, timeout=30)) as db, db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, mtime INTEGER, size INTEGER)")
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS contents USING fts5(path UNINDEXED, content, tokenize='unicode61')")
        known = {row[0]: row[1:] for row in db.execute("SELECT path,mtime,size FROM files")}
        seen = set()
        for path in project_files(root):
            if path.suffix.lower() not in extensions:
                continue
            relative = str(path.relative_to(root))
            try:
                stat = path.stat()
                seen.add(relative)
                if known.get(relative) == (stat.st_mtime_ns, stat.st_size):
                    continue
                content = path.read_text(encoding="utf-8", errors="replace")
                if "\0" in content:
                    continue
                db.execute("DELETE FROM contents WHERE path=?", (relative,))
                db.execute("INSERT INTO contents VALUES(?,?)", (relative, content))
                db.execute("INSERT OR REPLACE INTO files VALUES(?,?,?)", (relative, stat.st_mtime_ns, stat.st_size))
            except OSError:
                continue
        for path in known.keys() - seen:
            db.execute("DELETE FROM files WHERE path=?", (path,))
            db.execute("DELETE FROM contents WHERE path=?", (path,))
        return db.execute("SELECT path,snippet(contents,1,'**','**','...',24),bm25(contents) "
                          "FROM contents WHERE contents MATCH ? ORDER BY bm25(contents) LIMIT ?",
                          (query, max(1, limit))).fetchall(), len(seen)
