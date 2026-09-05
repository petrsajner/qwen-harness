"""Local web API and packaged static frontend for Marvin."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import mimetypes
import os
from pathlib import Path
import time
import uuid

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from harness.application import ApplicationService, read_json
from harness.app_operations import COMMANDS, perform_action, session_detail
from harness.changes import atomic_write_text
from harness.config import load_config
from harness.history_index import HistoryIndex
from harness.projects import Projects
from harness.session import Session
from harness.version import APP_VERSION
from harness.work_modes import WORK_MODES


def create_app(cfg=None, *, service=None):
    cfg = cfg or load_config()
    service = service or ApplicationService(cfg)

    @asynccontextmanager
    async def lifespan(app):
        yield
        service.close()

    app = FastAPI(title="Marvin", lifespan=lifespan)
    app.state.service = service

    @app.exception_handler(ValueError)
    @app.exception_handler(FileNotFoundError)
    def invalid_request(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/config")
    def compatibility_config():
        return {"title": f"Marvin v{APP_VERSION}", "mode": "workspace", "version": APP_VERSION, "components": []}

    @app.get("/favicon.ico")
    def favicon():
        return FileResponse(Path(__file__).resolve().parent.parent / "app_icon.ico")

    @app.get("/api/state")
    def state(session_id: str | None = None):
        with service.lock:
            sessions = Session.list_sessions(cfg, limit=100000)
            selected = session_id or service.preferences.get("session_id")
            if not selected or (selected not in service.sessions and not any(item["id"] == selected for item in sessions)):
                selected = sessions[0]["id"] if sessions else service.new_session(work_mode="discussion").id
                sessions = Session.list_sessions(cfg, limit=100000)
            session = service.session(selected)
            if not any(item["id"] == selected for item in sessions):
                sessions.insert(0, {**session.meta, "id": selected, "messages": len(session.messages)})
            return {"version": APP_VERSION, "preferences": service.preferences,
                "session_id": selected, "sessions": sessions, "projects": Projects(cfg).list_all(),
                "modes": [{"id": key, "label": value.label} for key, value in WORK_MODES.items()],
                "models": [{"id": key, "name": model.get("status_label") or model["alias"],
                            "vision": bool(model.get("mmproj")), "installed": cfg.model_file(key).is_file(),
                            "profiles": [{"id": p, **spec} for p, spec in cfg.kv_cache_profiles(key).items()],
                            "profile": service.preferences.get("kv_cache_modes", {}).get(key, cfg.kv_cache_mode(key))}
                           for key, model in cfg.data["models"].items()],
                "active": {k: service.active[k] for k in ("id", "session_id", "text")} if service.active else None,
                "queue": service.store.jobs(), "queue_paused": service.queue_paused,
                "commands": COMMANDS, "sequence": service.store.sequence()}

    @app.get("/api/sessions/{sid}")
    def get_session(sid: str, before: int | None = None, count: int = 100):
        with service.lock:
            session = service.session(sid)
            messages = session.messages
            end = min(before, len(messages)) if before is not None else len(messages)
            start = max(0, end - max(1, count))
            live = service.live.get(sid) or read_json(session.dir / "run-live.json")
            jobs = [j for j in service.store.jobs(("queued", "steering", "running", "interrupted", "stopped", "failed", "waiting_confirmation"))
                    if j["session_id"] == sid]
            return {"id": sid, "meta": session.meta, "messages": [service.message_payload(session, m) for m in messages[start:end]],
                    "before": start if start else None, "total": len(messages), "live": live,
                    "draft": read_json(session.dir / "draft.json"), "jobs": jobs}

    @app.get("/api/sessions/{sid}/detail")
    def detail(sid: str):
        return session_detail(service, service.session(sid))

    @app.post("/api/sessions")
    def new_session(payload: dict):
        project = next((p for p in Projects(cfg).list_all() if p["id"] == payload.get("project_id")), None)
        session = service.new_session(project["path"] if project else None,
                    payload.get("mode") or (project.get("work_mode") if project else "discussion"))
        return {"session_id": session.id}

    @app.post("/api/sessions/{sid}/select")
    def select_session(sid: str):
        return {"session_id": service.select_session(sid).id}

    @app.post("/api/sessions/{sid}/submit")
    def submit(sid: str, payload: dict):
        if payload.get("text", "").strip() == "/stop":
            return {"stopped": service.stop(sid)}
        return service.submit(sid, payload.get("text", ""), payload.get("attachments", []),
                              request_id=payload.get("request_id"), delivery=payload.get("delivery", "steer"))

    @app.post("/api/sessions/{sid}/actions/{action}")
    def action(sid: str, action: str, payload: dict):
        with service.lock:
            result = perform_action(service, sid, action, payload)
            if action != "draft":
                service.store.emit(sid, "session_changed", {})
            return result

    @app.patch("/api/queue/{job_id}")
    def edit_queue(job_id: str, payload: dict):
        service.edit_queued(job_id, payload.get("text"), payload.get("cancel", False))
        return {"ok": True}

    @app.post("/api/queue/resume")
    def resume_queue():
        with service.wake:
            service.queue_paused = False
            service.wake.notify_all()
        return {"ok": True}

    @app.get("/api/events")
    async def events(request: Request, after: int = 0):
        async def generate():
            sequence = max(after, int(request.headers.get("last-event-id", "0")))
            heartbeat = time.monotonic()
            while not await request.is_disconnected():
                rows = await asyncio.to_thread(service.store.after, sequence)
                for row in rows:
                    sequence = row["seq"]
                    yield f"id: {sequence}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
                if time.monotonic() - heartbeat > 10:
                    heartbeat = time.monotonic()
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.2)
        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/sessions/{sid}/attachments")
    async def upload(sid: str, file: UploadFile):
        session = service.session(sid)
        session.persist()
        directory = session.dir / "attachments"
        directory.mkdir(parents=True, exist_ok=True)
        name = Path(file.filename or "attachment").name
        target = directory / (uuid.uuid4().hex[:10] + "-" + name)
        temporary = target.with_suffix(target.suffix + ".partial")
        try:
            with temporary.open("wb") as handle:
                while chunk := await file.read(1024 * 1024):
                    handle.write(chunk)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        result = service.store.register_file(target, sid, "attachment", name=name)
        result["name"] = name
        return result

    @app.get("/api/files/{file_id}")
    def get_file(file_id: str, download: bool = False):
        record = service.store.file(file_id)
        if not record or not Path(record["path"]).is_file():
            raise HTTPException(404, "File not found")
        mime = mimetypes.guess_type(record["path"])[0] or "application/octet-stream"
        return FileResponse(record["path"], media_type=mime,
                            filename=record["name"] if download else None)

    @app.get("/api/files/{file_id}/preview")
    def preview_file(file_id: str, start: int = 1, count: int = 50, sheet: str | None = None, formulas: bool = False):
        record = service.store.file(file_id)
        if not record:
            raise HTTPException(404, "File not found")
        from harness.documents import read_document_content
        return {"content": read_document_content(Path(record["path"]), start=start, count=count,
                                                  sheet=sheet, formulas=formulas)}

    @app.get("/api/preview/{file_id}/{relative:path}")
    def html_preview(file_id: str, relative: str):
        record = service.store.file(file_id)
        if not record:
            raise HTTPException(404, "File not found")
        original = Path(record["path"]).resolve()
        target = (original.parent / (relative or original.name)).resolve()
        if not target.is_relative_to(original.parent) or not target.is_file():
            raise HTTPException(404, "Preview asset not found")
        return FileResponse(target, headers={"Access-Control-Allow-Origin": "*"})

    @app.post("/api/files/{file_id}/run")
    def run_result(file_id: str):
        from harness.processes import ProcessManager
        record = service.store.file(file_id)
        if not record:
            raise HTTPException(404, "File not found")
        path = Path(record["path"])
        session = service.session(record["session_id"])
        manager = service.agents[session.id].ctx.processes if session.id in service.agents else ProcessManager()
        manager.bind_session(session)
        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"
        if path.suffix == ".py":
            python = Path(session.meta.get("workspace") or path.parent) / ".venv" / "Scripts" / "python.exe"
            if not python.is_file():
                python = cfg.root / ".venv" / "Scripts" / "python.exe"
            command = f"& {quote(python)} {quote(path)}"
        elif path.suffix.lower() in (".exe", ".bat", ".cmd", ".ps1"):
            command = f"& {quote(path)}"
        else:
            raise ValueError("This file is opened as a document, not run as a program")
        item = manager.start(command, "powershell", path.parent, 0)
        return {"process_id": item.id}

    @app.post("/api/files/{file_id}/open")
    def open_file(file_id: str, payload: dict):
        record = service.store.file(file_id)
        if not record:
            raise HTTPException(404, "File not found")
        target = Path(record["path"])
        if payload.get("folder"):
            target = target.parent
        os.startfile(str(target))
        return {"ok": True}

    @app.post("/api/pick")
    def pick(payload: dict):
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            path = filedialog.askdirectory(parent=root) if payload.get("folder", True) else filedialog.askopenfilename(parent=root)
            return {"path": path or None}
        finally:
            root.destroy()

    @app.post("/api/projects")
    def create_project(payload: dict):
        projects = Projects(cfg)
        project = projects.attach_folder(payload["path"]) if payload.get("path") else projects.create_new(payload["name"])
        if payload.get("mode"):
            projects.set_work_mode(project["path"], payload["mode"])
        return {"project": project, "session_id": service.select_project(project["id"]).id}

    @app.post("/api/projects/select")
    def select_project(payload: dict):
        return {"session_id": service.select_project(payload.get("id")).id}

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: str):
        project = next((p for p in Projects(cfg).list_all() if p["id"] == project_id), None)
        if not project:
            raise ValueError("Unknown project")
        if service.active and service.session(service.active["session_id"]).meta.get("workspace") == project["path"]:
            raise ValueError("Stop the project's task before deleting it")
        Projects(cfg).delete_by_path(project["path"])
        for session in Session.list_sessions(cfg, limit=100000):
            if session.get("workspace") == project["path"]:
                perform_action(service, session["id"], "delete", {})
        return {"session_id": service.select_project(None).id}

    @app.get("/api/sessions/{sid}/library")
    def library(sid: str):
        from harness.file_index import project_files
        from harness.repo_index import DOCUMENT_EXTENSIONS
        session = service.session(sid)
        workspace = session.meta.get("workspace")
        if not workspace:
            return []
        return [service.store.register_file(path, sid, "project")
                for path in project_files(Path(workspace))
                if path.suffix.lower() in (DOCUMENT_EXTENSIONS | {".xlsx", ".xlsm", ".png", ".jpg", ".webp"})]

    @app.post("/api/sessions/{sid}/import-chat")
    def import_chat(sid: str, payload: dict):
        session = service.session(sid)
        record = service.store.file(payload["file_id"])
        imported = Session.import_jsonl(cfg, Path(record["path"]), "", workspace=session.meta.get("workspace"),
                                        work_mode=session.meta.get("work_mode", "discussion"))
        service.sessions[imported.id] = imported
        return {"session_id": imported.id}

    @app.post("/api/projects/{project_id}/export")
    def project_export(project_id: str, payload: dict):
        from harness.app_storage import export_project
        project = next(p for p in Projects(cfg).list_all() if p["id"] == project_id)
        target = cfg.path("paths.runtime_dir") / "project-exports" / f"{project['name']}-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        return service.store.register_file(export_project(cfg, project, target), payload["session_id"])

    @app.post("/api/projects/import")
    def project_import(payload: dict):
        from harness.app_storage import import_project
        record = service.store.file(payload["file_id"])
        project = import_project(cfg, Path(record["path"]))
        return {"session_id": service.select_project(project["id"]).id}

    @app.get("/api/search")
    def search(query: str):
        return HistoryIndex(cfg.path("paths.sessions_dir")).search(query)

    @app.patch("/api/settings")
    def update_settings(payload: dict):
        with service.lock:
            if "model" in payload and payload["model"] not in cfg.data["models"]:
                raise ValueError("Unknown model")
            if "thinking" in payload and payload["thinking"] not in ("off", "low", "medium", "xhigh"):
                raise ValueError("Unknown thinking profile")
            for key, profile in payload.get("kv_cache_modes", {}).items():
                if key not in cfg.data["models"] or profile not in cfg.kv_cache_profiles(key):
                    raise ValueError("Unknown KV profile")
            allowed = {"model", "thinking", "language", "theme", "density", "autonomy", "send_mode", "kv_cache_modes", "vram_gb"}
            service.preferences.update({key: value for key, value in payload.items() if key in allowed})
            if service.manage_model and "vram_gb" in payload:
                service.fit_hardware()
            service.save_preferences()
            if service.manage_model and not service.active and ("model" in payload or "kv_cache_modes" in payload):
                key = service.preferences["model"]
                service.models.request(key, restart=True, kv_profile=service.preferences["kv_cache_modes"].get(key))
            service.store.emit(None, "settings_changed", service.preferences)
            return service.preferences

    runtime_cache = {"at": 0.0, "value": {}}

    @app.get("/api/runtime")
    def runtime():
        if time.monotonic() - runtime_cache["at"] < 2:
            return runtime_cache["value"]
        from harness import servermgmt
        snapshot = service.models.snapshot()
        value = {"status": servermgmt.server_state(cfg), "switch": snapshot.__dict__,
                 "model": servermgmt.running_model(cfg), "vram": servermgmt.vram_value(),
                 "python": __import__("platform").python_version(), "version": APP_VERSION}
        runtime_cache.update(at=time.monotonic(), value=value)
        return value

    @app.post("/api/runtime/{command}")
    def runtime_command(command: str):
        if command == "stop":
            service.stop()
            service.models.cancel()
        elif command in ("start", "restart"):
            if service.active:
                service.stop()
            key = service.preferences["model"]
            service.models.request(key, restart=command == "restart", kv_profile=service.preferences["kv_cache_modes"].get(key))
        else:
            raise ValueError("Unknown runtime action")
        runtime_cache["at"] = 0
        return {"ok": True}

    @app.get("/api/backup")
    def backup_info():
        from scripts.offline_backup import backup_info
        path = cfg.path("paths.runtime_dir") / "offline-backup-path.txt"
        if not path.is_file():
            return {"selected": False}
        try:
            return {"selected": True, **backup_info(Path(path.read_text(encoding="utf-8-sig").strip()))}
        except Exception as exc:
            return {"selected": False, "error": str(exc)}

    @app.post("/api/backup/{operation}")
    def backup_action(operation: str, payload: dict):
        from harness.processes import ProcessManager
        marker = cfg.path("paths.runtime_dir") / "offline-backup-path.txt"
        if operation == "clear":
            marker.unlink(missing_ok=True)
            return {"ok": True}
        path = Path(payload["path"]).resolve()
        if operation == "select":
            from scripts.offline_backup import load_manifest
            load_manifest(path)
            atomic_write_text(marker, str(path))
            return {"ok": True}
        manager = getattr(service, "maintenance", None)
        if manager is None:
            manager = service.maintenance = ProcessManager()
            manager.bind_session(service.session(payload["session_id"]))
        script = cfg.root / "scripts" / "offline_backup.py"
        python = cfg.root / ".venv" / "Scripts" / "python.exe"
        argument = "--backup"
        if operation == "create":
            path /= "Marvin-Offline-Backup-" + time.strftime("%Y%m%d-%H%M%S")
            argument = "--output"
        if operation not in ("create", "verify"):
            raise ValueError("Unknown backup action")
        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"
        item = manager.start(f"& {quote(python)} -u {quote(script)} {operation} {argument} {quote(path)}", "powershell", cfg.root, 0)
        if operation == "create":
            if not hasattr(service, "backup_targets"):
                service.backup_targets = {}
            service.backup_targets[item.id] = str(path)
        return {"process_id": item.id, "path": str(path)}

    @app.get("/api/maintenance")
    def maintenance():
        manager = getattr(service, "maintenance", None)
        items = manager.list() if manager else []
        for item in items:
            target = getattr(service, "backup_targets", {}).get(item["process_id"])
            if target and item.get("exit_code") == 0 and (Path(target) / "manifest.json").is_file():
                atomic_write_text(cfg.path("paths.runtime_dir") / "offline-backup-path.txt", target)
                service.backup_targets.pop(item["process_id"], None)
        return items

    @app.get("/api/maintenance/{process_id}")
    def maintenance_output(process_id: str):
        manager = getattr(service, "maintenance", None)
        if not manager or not manager.get(process_id):
            raise HTTPException(404, "Operation not found")
        return manager.poll(process_id, max_chars=50000)

    @app.get("/api/sessions/{sid}/processes/{process_id}")
    def process_output(sid: str, process_id: str, cursor: int = 0):
        from harness.processes import ProcessManager
        agent = service.agents.get(sid)
        manager = agent.ctx.processes if agent else ProcessManager()
        if not agent:
            manager.bind_session(service.session(sid))
        return manager.poll(process_id, cursor=cursor, max_chars=50000)

    @app.get("/api/manual/{language}")
    def manual(language: str):
        name = "Marvin-Manual-" + ("CS" if language == "cs" else "EN") + ".pdf"
        for path in (cfg.root / "docs" / name, cfg.root / "output" / "pdf" / name,
                     Path(__file__).resolve().parent.parent / "output" / "pdf" / name):
            if path.is_file():
                return FileResponse(path, media_type="application/pdf")
        raise HTTPException(404, "Manual not found")

    ui_dir = cfg.root / "ui_dist"
    if not ui_dir.is_dir():
        ui_dir = Path(__file__).resolve().parent.parent / "ui_dist"
    if ui_dir.is_dir():
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="workspace")
    return app


def main():
    import uvicorn
    cfg = load_config()
    app = create_app(cfg)
    if os.environ.get("QWEN_AUTOSTART_SERVER"):
        service = app.state.service
        key = service.preferences["model"]
        service.models.request(key, kv_profile=service.preferences["kv_cache_modes"].get(key))
    uvicorn.run(app, host=cfg.web.get("host", "127.0.0.1"),
                port=int(os.environ.get("QWEN_WEB_PORT", cfg.web.get("port", 7860))), log_level="warning")
