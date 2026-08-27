"""Per-task journal změn souborů s persistentním rollbackem."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class ChangeJournal:
    def __init__(self, session, workspace: Path):
        self.session = session
        self.workspace = Path(workspace).resolve()
        self.base = session.dir / "changes"
        self.task_id: str | None = None
        self._records: dict[str, dict] = {}
        self._lock = threading.RLock()

    def set_workspace(self, workspace: Path) -> None:
        self.workspace = Path(workspace).resolve()

    def begin_task(self, label: str = "") -> str:
        with self._lock:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self.task_id = f"{stamp}-{uuid.uuid4().hex[:6]}"
            self._records = {}
            self._write_manifest(label=label[:200])
            return self.task_id

    @property
    def task_dir(self) -> Path:
        if self.task_id is None:
            self.begin_task()
        return self.base / str(self.task_id)

    def record_before(self, path: Path) -> None:
        path = path.resolve()
        key = str(path)
        with self._lock:
            if key in self._records:
                return
            existed = path.is_file()
            backup_name = hashlib.sha256(key.encode("utf-8")).hexdigest() + ".bak"
            record = {
                "path": key,
                "display_path": self._display_path(path),
                "existed": existed,
                "backup": backup_name if existed else None,
                "before_sha256": file_sha256(path),
                "after_sha256": None,
            }
            if existed:
                self.task_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, self.task_dir / backup_name)
            self._records[key] = record
            self._write_manifest()

    def record_after(self, path: Path) -> None:
        path = path.resolve()
        with self._lock:
            record = self._records.get(str(path))
            if record is None:
                return
            record["after_sha256"] = file_sha256(path)
            self._write_manifest()

    def record_directory_before(self, path: Path) -> None:
        path = path.resolve()
        key = str(path)
        with self._lock:
            if key in self._records:
                return
            existed = path.is_dir()
            self._records[key] = {
                "path": key,
                "display_path": self._display_path(path),
                "kind": "directory",
                "existed": existed,
                "backup": None,
                "before_sha256": "directory" if existed else None,
                "after_sha256": None,
            }
            self._write_manifest()

    def record_directory_after(self, path: Path) -> None:
        path = path.resolve()
        with self._lock:
            record = self._records.get(str(path))
            if record is None:
                return
            record["after_sha256"] = "directory" if path.is_dir() else None
            self._write_manifest()

    def summary(self, task_id: str | None = None) -> dict:
        manifest = self._load_manifest(task_id)
        records = manifest.get("files", []) if manifest else []
        undone = bool(manifest and manifest.get("undone_at"))
        return {
            "task_id": manifest.get("task_id") if manifest else None,
            "label": manifest.get("label", "") if manifest else "",
            "file_count": len(records),
            "files": [
                {
                    "path": record["display_path"],
                    "change": ("directory" if record.get("kind") == "directory"
                               else "deleted" if record["existed"] and record.get("after_sha256") is None
                               else "modified" if record["existed"] else "created"),
                    "changed": not undone and record.get("before_sha256") != record.get("after_sha256"),
                }
                for record in records
            ],
        }

    def undo(self, task_id: str | None = None) -> dict:
        with self._lock:
            manifest = self._load_manifest(task_id)
            if not manifest:
                return {"restored": [], "errors": ["No task checkpoint available"]}
            task_dir = self.base / manifest["task_id"]
            restored: list[str] = []
            errors: list[str] = []
            for record in reversed(manifest.get("files", [])):
                path = Path(record["path"])
                try:
                    if record.get("kind") == "directory":
                        if not record["existed"] and path.is_dir():
                            path.rmdir()
                    elif record["existed"]:
                        backup = task_dir / record["backup"]
                        path.parent.mkdir(parents=True, exist_ok=True)
                        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.restore")
                        shutil.copy2(backup, temporary)
                        os.replace(temporary, path)
                    else:
                        path.unlink(missing_ok=True)
                    restored.append(record["display_path"])
                except OSError as exc:
                    errors.append(f"{record['display_path']}: {exc}")
            manifest["undone_at"] = time.time()
            self._atomic_json(task_dir / "manifest.json", manifest)
            return {"task_id": manifest["task_id"], "restored": restored, "errors": errors}

    def _display_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.workspace))
        except ValueError:
            return str(path)

    def _write_manifest(self, label: str | None = None) -> None:
        if self.task_id is None:
            return
        path = self.task_dir / "manifest.json"
        previous = self._read_json(path) or {}
        manifest = {
            "task_id": self.task_id,
            "created": previous.get("created", time.time()),
            "label": previous.get("label", "") if label is None else label,
            "workspace": str(self.workspace),
            "files": list(self._records.values()),
        }
        self._atomic_json(path, manifest)

    def _load_manifest(self, task_id: str | None) -> dict | None:
        selected = task_id or self.task_id
        if selected:
            return self._read_json(self.base / selected / "manifest.json")
        if not self.base.exists():
            return None
        manifests = sorted(self.base.glob("*/manifest.json"), reverse=True)
        return self._read_json(manifests[0]) if manifests else None

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _atomic_json(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
