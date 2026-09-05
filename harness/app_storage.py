"""Durable UI events, idempotent submissions and portable project archives."""
from __future__ import annotations

import json
from contextlib import contextmanager
import sqlite3
import time
import uuid
import zipfile
from pathlib import Path

from harness.changes import atomic_write_text


class EventStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT, kind TEXT, payload TEXT, created REAL);
                CREATE INDEX IF NOT EXISTS events_session ON events(session_id, seq);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, session_id TEXT,
                    status TEXT, payload TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY, path TEXT, session_id TEXT,
                    kind TEXT, name TEXT, created REAL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def emit(self, session_id, kind, payload):
        with self.connect() as db:
            cursor = db.execute("INSERT INTO events(session_id,kind,payload,created) VALUES(?,?,?,?)",
                                (session_id, kind, json.dumps(payload, ensure_ascii=False), time.time()))
            return cursor.lastrowid

    def after(self, sequence=0, limit=200):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT ?",
                              (sequence, limit)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def sequence(self):
        with self.connect() as db:
            return db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]

    def notices(self, session_id):
        with self.connect() as db:
            rows = db.execute("SELECT seq,payload FROM events WHERE session_id=? AND kind='notice' ORDER BY seq",
                              (session_id,)).fetchall()
        return [{"seq": row["seq"], **json.loads(row["payload"])} for row in rows]

    def job(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return {**dict(row), "payload": json.loads(row["payload"])} if row else None

    def save_job(self, job, status="queued"):
        with self.connect() as db:
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                       "status=excluded.status,payload=excluded.payload",
                       (job["id"], job["session_id"], status, json.dumps(job, ensure_ascii=False),
                        job.get("created", time.time())))

    def jobs(self, statuses=("queued", "running", "steering", "interrupted", "waiting_confirmation")):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM jobs WHERE status IN (" + ",".join("?" for _ in statuses)
                              + ") ORDER BY created", tuple(statuses)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def register_file(self, path, session_id, kind="result", name=None):
        import hashlib
        path = Path(path).resolve()
        key = hashlib.sha256(str(path).encode()).hexdigest()[:24]
        with self.connect() as db:
            db.execute("INSERT INTO files VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                       "path=excluded.path,kind=excluded.kind",
                       (key, str(path), session_id, kind, name or path.name, time.time()))
            saved_name = db.execute("SELECT name FROM files WHERE id=?", (key,)).fetchone()[0]
        return {"id": key, "name": saved_name, "path": str(path), "kind": kind,
                "url": f"/api/files/{key}", "exists": path.is_file(),
                "mtime": path.stat().st_mtime if path.is_file() else 0}

    def file(self, key):
        with self.connect() as db:
            row = db.execute("SELECT * FROM files WHERE id=?", (key,)).fetchone()
        return dict(row) if row else None

    def files(self, session_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM files WHERE session_id=? ORDER BY created DESC",
                                                   (session_id,))]


def export_project(cfg, project: dict, destination: Path):
    from harness.file_index import project_files
    from harness.session import Session
    root = Path(project["path"]).resolve()
    sessions = [item for item in Session.list_sessions(cfg, limit=100000)
                if item.get("workspace") and Path(item["workspace"]).resolve() == root]
    destination.parent.mkdir(parents=True, exist_ok=True)
    metadata = {"format": 1, "project": project, "sessions": [item["id"] for item in sessions],
                "source_sessions": str(cfg.path("paths.sessions_dir").resolve())}
    store = EventStore(cfg.path("paths.runtime_dir") / "application.sqlite3")
    metadata["files"] = [item for session in sessions for item in store.files(session["id"])]
    temporary = destination.with_suffix(".partial")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            archive.writestr("marvin-project.json", json.dumps(metadata, ensure_ascii=False))
            for path in project_files(root, refresh=True):
                if path.resolve() in (destination.resolve(), temporary.resolve()):
                    continue
                archive.write(path, "project/" + path.relative_to(root).as_posix())
            for item in sessions:
                session_dir = cfg.path("paths.sessions_dir") / item["id"]
                for path in session_dir.rglob("*"):
                    if path.is_file():
                        archive.write(path, "sessions/" + path.relative_to(cfg.path("paths.sessions_dir")).as_posix())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def import_project(cfg, source: Path):
    from harness.projects import Projects
    from harness.session import Session
    with zipfile.ZipFile(source) as archive:
        metadata = json.loads(archive.read("marvin-project.json"))
        if metadata.get("format") != 1:
            raise ValueError("Unsupported project archive")
        project = Projects(cfg).create_new(metadata["project"]["name"])
        project_root = Path(project["path"])
        remap = {metadata["project"]["path"]: str(project_root)}
        session_map = {old: time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
                       for old in metadata.get("sessions", [])}
        for old, new in session_map.items():
            remap[str(Path(metadata["source_sessions"]) / old)] = str(cfg.path("paths.sessions_dir") / new)
        file_ids = {}
        store = EventStore(cfg.path("paths.runtime_dir") / "application.sqlite3")
        for item in metadata.get("files", []):
            target_path = item["path"]
            for old, new in sorted(remap.items(), key=lambda pair: -len(pair[0])):
                if target_path == old or target_path.startswith(old + "\\") or target_path.startswith(old + "/"):
                    target_path = new + target_path[len(old):]
                    break
            registered = store.register_file(target_path, session_map.get(item["session_id"], item["session_id"]),
                                             item["kind"], name=item["name"])
            file_ids[item["id"]] = registered["id"]

        def remap_data(value):
            if isinstance(value, dict):
                return {key: (session_map.get(item, item) if key in ("source_session", "session_id") and isinstance(item, str)
                              else [file_ids.get(i, i) for i in item] if key == "attachments" and isinstance(item, list)
                              else remap_data(item)) for key, item in value.items()}
            if isinstance(value, list):
                return [remap_data(item) for item in value]
            if isinstance(value, str):
                for item in metadata.get("files", []):
                    new_id = file_ids.get(item["id"])
                    if new_id and item["path"] in value:
                        record = store.file(new_id)
                        value = value.replace(item["path"], record["path"])
                for old, new in sorted(remap.items(), key=lambda pair: -len(pair[0])):
                    if value == old or value.startswith(old + "\\") or value.startswith(old + "/"):
                        return new + value[len(old):]
            return value

        for member in archive.infolist():
            relative = Path(member.filename)
            if member.is_dir() or ".." in relative.parts or relative.is_absolute():
                continue
            if relative.parts[0] == "project":
                target = project_root.joinpath(*relative.parts[1:])
            elif relative.parts[0] == "sessions" and relative.parts[1] in session_map:
                target = (cfg.path("paths.sessions_dir") / session_map[relative.parts[1]]).joinpath(*relative.parts[2:])
            else:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            data = archive.read(member)
            if target.suffix in (".json", ".jsonl"):
                try:
                    if target.suffix == ".json":
                        data = json.dumps(remap_data(json.loads(data)), ensure_ascii=False).encode()
                    else:
                        data = ("\n".join(json.dumps(remap_data(json.loads(line)), ensure_ascii=False)
                                          for line in data.decode().splitlines() if line.strip()) + "\n").encode()
                except (ValueError, UnicodeError):
                    pass
            target.write_bytes(data)
        for new in session_map.values():
            session = Session.load(cfg, new)
            session.meta["workspace"] = str(project_root)
            session._save_meta()
        Projects(cfg).set_work_mode(str(project_root), metadata["project"].get("work_mode", "discussion"))
        return project
