"""Single-model application service, independent of browser connections and UI frameworks."""
from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from pathlib import Path

from harness.agent import Agent, Status, build_registry
from harness.app_storage import EventStore
from harness.browser import BrowserSession
from harness.changes import atomic_write_text
from harness.config import Config
from harness.llm import LLMClient
from harness.model_switch import ModelSwitchController
from harness.processes import ProcessManager
from harness.projects import Projects
from harness.prompts import build_system_prompt
from harness.safety import SafetyPolicy
from harness.session import IMG_MIMES, Session
from harness.work_modes import WORK_MODES, normalize_work_mode


def read_json(path: Path, fallback=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return fallback if fallback is not None else {}


class ApplicationService:
    def __init__(self, cfg: Config, *, llm_factory=LLMClient, manage_model=True):
        self.cfg = cfg
        self.llm_factory = llm_factory
        self.manage_model = manage_model
        self.lock = threading.RLock()
        self.wake = threading.Condition(self.lock)
        self.closed = False
        self.queue_paused = False
        self.sessions: dict[str, Session] = {}
        self.agents: dict[str, Agent] = {}
        self.live: dict[str, dict] = {}
        self.active: dict | None = None
        self.abort = threading.Event()
        self.models = ModelSwitchController(cfg)
        self.store = EventStore(cfg.path("paths.runtime_dir") / "application.sqlite3")
        self.preferences_path = cfg.path("paths.runtime_dir") / "workspace-settings.json"
        legacy = read_json(cfg.path("paths.runtime_dir") / "webui-state.json")
        self.preferences = {
            "model": legacy.get("model", cfg.model_key()),
            "thinking": "off" if legacy.get("thinking", cfg.data.get("thinking", True)) is False
                        else legacy.get("reasoning_effort", cfg.data.get("reasoning_effort", "xhigh")),
            "language": legacy.get("language", "en"), "theme": "dark", "density": "comfortable",
            "autonomy": legacy.get("autonomy", cfg.agent.get("autonomy", "supervised")),
            "work_mode": legacy.get("work_mode", cfg.data.get("work_mode", "discussion")),
            "session_id": legacy.get("session_id"), "kv_cache_modes": legacy.get("kv_cache_modes", {}),
            "send_mode": "steer", **read_json(self.preferences_path),
        }
        if self.preferences["model"] not in cfg.data["models"]:
            self.preferences["model"] = cfg.model_key()
        self.preferences.setdefault("vram_gb", legacy.get("vram_gb", cfg.data.get("hardware", {}).get("vram_gb", "auto")))
        if manage_model:
            self.fit_hardware()
        from harness.i18n import detect_language
        if not legacy.get("language") and not self.preferences_path.exists():
            self.preferences["language"] = detect_language(cfg.root)
        for job in self.store.jobs(("running", "steering")):
            payload = job["payload"]
            if job["status"] == "running":
                old_live = cfg.path("paths.sessions_dir") / job["session_id"] / "run-live.json"
                if old_live.is_file():
                    atomic_write_text(old_live.with_name("interrupted-live.json"), old_live.read_text(encoding="utf-8"))
            self.store.save_job(payload, "interrupted" if job["status"] == "running" else "queued")
        self.worker = threading.Thread(target=self._work, name="marvin-run-controller", daemon=True)
        self.worker.start()

    def save_preferences(self):
        atomic_write_text(self.preferences_path, json.dumps(self.preferences, ensure_ascii=False, indent=2))

    def fit_hardware(self):
        from harness.gpu import best_fit, effective_vram_gb, fits
        candidate = Config(copy.deepcopy(self.cfg.data), self.cfg.root)
        candidate.data.setdefault("hardware", {})["vram_gb"] = self.preferences.get("vram_gb", "auto")
        key = self.preferences["model"]
        candidate.data["default_model"] = key
        profile = self.preferences.get("kv_cache_modes", {}).get(key, candidate.kv_cache_mode(key))
        if profile in candidate.kv_cache_profiles(key):
            candidate.set_kv_cache_mode(key, profile)
        vram = effective_vram_gb(candidate)
        if vram and not fits(candidate, key, profile, vram):
            choice = best_fit(candidate, vram)
            if choice:
                self.preferences["model"] = choice[0]
                self.preferences.setdefault("kv_cache_modes", {})[choice[0]] = choice[1]

    def session(self, session_id: str) -> Session:
        with self.lock:
            if session_id not in self.sessions:
                self.sessions[session_id] = Session.load(self.cfg, session_id)
            return self.sessions[session_id]

    def new_session(self, workspace=None, work_mode=None):
        with self.lock:
            if workspace and not Path(workspace).is_dir():
                raise FileNotFoundError(f"Project folder is unavailable: {workspace}")
            mode = normalize_work_mode(work_mode or self.preferences["work_mode"])
            session = Session(self.cfg, workspace=workspace or None, work_mode=mode, transient=True)
            self.sessions[session.id] = session
            self.select_session(session.id)
            self.store.emit(session.id, "session_changed", {"id": session.id})
            return session

    def select_session(self, session_id):
        session = self.session(session_id)
        self.preferences["session_id"] = session.id
        self.save_preferences()
        return session

    def select_project(self, project_id=None):
        project = next((p for p in Projects(self.cfg).list_all() if p["id"] == project_id), None)
        if project and project.get("missing"):
            raise FileNotFoundError(f"Project folder is unavailable: {project['path']}")
        workspace = project["path"] if project else None
        matches = [s for s in Session.list_sessions(self.cfg, limit=100000) if s.get("workspace") == workspace]
        return self.select_session(matches[0]["id"]) if matches else self.new_session(
            workspace, project.get("work_mode") if project else "discussion")

    def config_for(self, session, settings=None):
        if session.meta.get("workspace") and not Path(session.meta["workspace"]).is_dir():
            raise FileNotFoundError(f"Project folder is unavailable: {session.meta['workspace']}")
        preferences = {**self.preferences, **(settings or {})}
        data = copy.deepcopy(self.cfg.data)
        data["default_model"] = preferences["model"]
        data["thinking"] = preferences["thinking"] != "off"
        data["reasoning_effort"] = preferences["thinking"] if data["thinking"] else "xhigh"
        data["work_mode"] = session.meta.get("work_mode") or preferences["work_mode"]
        data["agent"]["workspace"] = session.meta.get("workspace")
        data["agent"]["autonomy"] = preferences["autonomy"]
        data["agent"]["mode"] = WORK_MODES[data["work_mode"]].agent_mode
        data.setdefault("hardware", {})["vram_gb"] = preferences.get("vram_gb", "auto")
        cfg = Config(data, self.cfg.root)
        for model, profile in preferences.get("kv_cache_modes", {}).items():
            if model in data["models"] and profile in cfg.kv_cache_profiles(model):
                cfg.set_kv_cache_mode(model, profile)
        return cfg

    def submit(self, session_id, text, attachments=None, *, request_id=None, delivery="steer", kind="message"):
        with self.wake:
            self.queue_paused = False
            request_id = request_id or uuid.uuid4().hex
            existing = self.store.job(request_id)
            if existing:
                return existing
            session = self.session(session_id)
            cfg = self.config_for(session)
            files = [self.store.file(key) for key in (attachments or [])]
            if any(not f or f["session_id"] != session_id for f in files):
                raise ValueError("Attachment does not belong to this conversation")
            if any(Path(f["path"]).suffix.lower() in IMG_MIMES for f in files) and not cfg.mmproj_file():
                raise ValueError("Selected model has no vision. Choose a vision-capable model; attachments remain in the draft.")
            if kind == "message" and not text.strip() and not files:
                raise ValueError("Message is empty")
            job = {"id": request_id, "session_id": session_id, "text": text,
                   "attachments": [f["id"] for f in files], "delivery": delivery, "kind": kind,
                   "settings": copy.deepcopy(self.preferences), "config": copy.deepcopy(cfg.data),
                   "created": time.time()}
            status = "queued"
            if (self.active and self.active["session_id"] == session_id
                    and delivery == "steer" and kind == "message" and not text.startswith("/")):
                status = "steering"
                self.abort.set()
            self.store.save_job(job, status)
            self.store.emit(session_id, "submission", {"id": request_id, "status": status, **job})
            self.wake.notify_all()
            return self.store.job(request_id)

    def edit_queued(self, request_id, text=None, cancel=False):
        with self.lock:
            job = self.store.job(request_id)
            if not job or job["status"] not in ("queued", "steering"):
                raise ValueError("This message has already started")
            payload = job["payload"]
            if text is not None:
                payload["text"] = text
            self.store.save_job(payload, "cancelled" if cancel else job["status"])
            self.store.emit(job["session_id"], "queue_changed", {})

    def stop(self, session_id=None):
        with self.lock:
            if self.active and (not session_id or self.active["session_id"] == session_id):
                self.queue_paused = True
                self.abort.set()
                self.store.emit(self.active["session_id"], "run_status", {"status": "stopping", "run_id": self.active["id"]})
                return True
            return False

    def resume(self, session_id, approve=None):
        with self.wake:
            self.queue_paused = False
            candidates = [j for j in self.store.jobs(("interrupted", "stopped", "failed", "waiting_confirmation"))
                          if j["session_id"] == session_id]
            if not candidates:
                raise ValueError("No interrupted task in this chat")
            job = candidates[-1]["payload"]
            job["resume"] = True
            job["approve"] = approve
            self.store.save_job(job, "queued")
            self.wake.notify_all()
            return job

    def _work(self):
        while True:
            with self.wake:
                jobs = self.store.jobs(("queued",))
                if self.closed:
                    return
                if not jobs or self.queue_paused:
                    self.wake.wait(timeout=0.5)
                    continue
                job = jobs[0]["payload"]
                self.active = job
                self.abort = threading.Event()
                self.store.save_job(job, "running")
            try:
                self._drive(job)
            except Exception as exc:
                status = "stopped" if self.abort.is_set() else "failed"
                job["error"] = str(exc) if status == "failed" else ""
                self.store.save_job(job, status)
                self.store.emit(job["session_id"], "run_status", {"status": status, "error": job["error"], "run_id": job["id"]})
            finally:
                with self.wake:
                    self.active = None
                    self.wake.notify_all()

    def _image_paths(self, job):
        files = [self.store.file(key) for key in job.get("attachments", [])]
        return [Path(f["path"]) for f in files if f and Path(f["path"]).suffix.lower() in IMG_MIMES]

    def _job_text(self, job):
        text = job["text"]
        files = [self.store.file(key) for key in job.get("attachments", [])]
        docs = [f for f in files if f and Path(f["path"]).suffix.lower() not in IMG_MIMES]
        if docs:
            text += "\n\nAttached documents available through read_document:\n" + "\n".join(f["path"] for f in docs)
        return text or "Please analyze the attached image(s)."

    def _drive(self, job):
        sid, rid = job["session_id"], job["id"]
        session = self.session(sid)
        cfg = Config(copy.deepcopy(job["config"]), self.cfg.root)
        cfg.agent["workspace"] = session.meta.get("workspace")
        mode = cfg.data["work_mode"]
        spec = WORK_MODES[mode]
        llm = self.llm_factory(cfg)
        live_path = session.dir / "run-live.json"
        first_step = 1 + max((m.get("step_id", -1) for m in session.messages if m.get("run_id") == rid), default=-1)
        live = {"run_id": rid, "session_id": sid, "step": first_step, "text": "", "reasoning": "",
                "phase": "preparing", "tool": "", "tool_chars": 0, "started": time.time(),
                "config": {"model": cfg.model_key(), "thinking": job["settings"]["thinking"], "work_mode": mode}}
        self.live[sid] = live
        last_flush = [0.0]

        def flush(force=False):
            now = time.monotonic()
            if force or now - last_flush[0] >= 0.3:
                last_flush[0] = now
                atomic_write_text(live_path, json.dumps(live, ensure_ascii=False))
                self.store.emit(sid, "live", dict(live))

        def event(kind, payload):
            if kind == "text":
                live["text"] += payload
                live["phase"] = "answering"
            elif kind == "reasoning":
                live["reasoning"] += payload
                live["phase"] = "thinking"
            elif kind == "tool_delta":
                name, arguments = payload
                live["tool"] = name or live["tool"]
                live["tool_chars"] += len(arguments or "")
                live["phase"] = "preparing_tool"
            elif kind == "tool_start":
                live["phase"] = "executing"
                live["tool"] = payload[0]
                live["arguments"] = {k: str(v)[:500] for k, v in (payload[1] or {}).items() if k not in ("content", "data")}
            elif kind == "usage":
                self.store.emit(sid, "usage", payload)
            elif kind == "tool_result":
                self.store.emit(sid, "tool_completed", {"name": payload[0]})
            elif kind == "info":
                live["info"] = str(payload)
                self.store.emit(sid, "notice", {"text": str(payload), "run_id": rid, "created": time.time()})
            flush(kind in ("tool_start", "tool_result", "info"))

        def message_saved(message):
            self.store.emit(sid, "message", self.message_payload(session, message))

        def capture_context():
            import hashlib
            from harness.memory import MemoryStore
            memory = MemoryStore(cfg, session.meta.get("workspace"), mode)
            records = []
            for scope in ("global", "mode", "project"):
                path = memory._path_for(scope)
                if path and path.is_file():
                    content = path.read_text(encoding="utf-8")
                    records.append({"scope": scope, "path": str(path), "content": content,
                                    "sha256": hashlib.sha256(content.encode()).hexdigest()})
            target = session.dir / "runs" / rid / f"context-{live['step']}.json"
            snapshot = {"run_id": rid, "created": time.time(), "work_mode": mode,
                        "model": cfg.model_key(), "memory": records,
                        "pinned_files": list(session.meta.get("pinned_files", [])),
                        "system_prompt": session.messages[0].get("content", "") if session.messages else ""}
            atomic_write_text(target, json.dumps(snapshot, ensure_ascii=False, indent=2))
            session.meta["context_snapshot"] = str(target)
            session._save_meta()

        session.on_message = message_saved
        session.run_id = rid
        session.request_id = rid
        if not session.messages or session.messages[0].get("role") != "system":
            session.messages.insert(0, {"role": "system", "content": "", "id": f"{sid}:system"})
        previous_agent = self.agents.get(sid)
        agent = Agent(cfg, llm, session, build_registry(spec.agent_mode, mode),
                      SafetyPolicy(autonomy=cfg.agent["autonomy"]), mode=spec.agent_mode,
                      work_mode=mode, abort_flag=self.abort, on_event=event,
                      process_manager=previous_agent.ctx.processes if previous_agent else None,
                      browser_manager=previous_agent.ctx.browser if previous_agent else None)
        if not session.meta.get("workspace"):
            agent.ctx.workspace = session.dir
            agent.ctx.changes.set_workspace(session.dir)
        self.agents[sid] = agent
        agent._overflow_retried = False
        flush(True)
        try:
            if job.get("kind") == "command" or job["text"].startswith("/"):
                from harness.app_operations import execute_command
                transformed = execute_command(self, agent, job)
                if transformed is None:
                    self.store.save_job(job, "complete")
                    return
                job = {**job, "text": transformed}
            if self.manage_model:
                from harness import servermgmt
                key = cfg.model_key()
                profile_changed = cfg.kv_cache_mode(key) != self.models.cfg.kv_cache_mode(key)
                if profile_changed or not servermgmt.health(cfg) or servermgmt.running_model(cfg) != key:
                    live["phase"] = "loading_model"
                    flush(True)
                    self.models.request(key, restart=profile_changed, kv_profile=cfg.kv_cache_mode(key))
                    while self.models.snapshot().busy and not self.abort.wait(0.1):
                        pass
                    if self.abort.is_set():
                        self.store.save_job(job, "stopped")
                        return
                    if not servermgmt.health(cfg):
                        raise RuntimeError(self.models.snapshot().error or "Model server is not ready")
            if job.get("resume"):
                saved_live = read_json(session.dir / "interrupted-live.json")
                if saved_live.get("text") and not any(m.get("content") == saved_live["text"] for m in session.messages[-8:]):
                    session.add("assistant", saved_live["text"], reasoning=saved_live.get("reasoning"))
                # A missing tool result after a crash must be inspected, never blindly replayed.
                answered = {m.get("tool_call_id") for m in session.messages if m.get("role") == "tool"}
                pending = [call for m in session.messages for call in m.get("tool_calls", []) if call["id"] not in answered]
                for call in pending:
                    session.add("tool", "Execution was interrupted; outcome unknown. Inspect actual state before retrying.",
                                tool_call_id=call["id"], name=call["function"]["name"])
                agent.refresh_system_prompt()
            else:
                for previous in self.store.jobs(("interrupted", "stopped", "failed", "waiting_confirmation")):
                    if previous["session_id"] == sid and previous["id"] != rid:
                        self.store.save_job(previous["payload"], "superseded")
                agent.new_task(self._job_text(job), images=self._image_paths(job))
                user = next((m for m in reversed(session.messages) if Session._is_user_boundary(m)), None)
                if user and job.get("attachments"):
                    user["attachments"] = job["attachments"]
                    session._rewrite_jsonl()
                    message_saved(user)
                if session.meta.get("workspace") and mode in ("development", "computer", "writing"):
                    agent.ctx.changes.capture_workspace()
            approve = job.get("approve")
            capture_context()
            while True:
                live.update(step=live["step"] + 1, text="", reasoning="", tool="", tool_chars=0, phase="preparing")
                session.step_id = live["step"]
                flush(True)
                result = agent.step(approve=approve)
                approve = None
                flush(True)
                if agent.ctx.project_workspace:
                    agent.ctx.changes.reconcile_workspace()
                steering = [item for item in self.store.jobs(("steering",)) if item["session_id"] == sid]
                if steering:
                    for item in steering:
                        addition = item["payload"]
                        session.request_id = addition["id"]
                        agent.steer(self._job_text(addition), images=self._image_paths(addition))
                        user = next(m for m in reversed(session.messages) if Session._is_user_boundary(m))
                        user["attachments"] = addition.get("attachments", [])
                        session._rewrite_jsonl()
                        message_saved(user)
                        self.store.save_job(addition, "complete")
                    capture_context()
                    self.abort.clear()
                    continue
                if result.status is Status.CONTINUE:
                    continue
                status = {Status.FINAL: "complete", Status.ABORTED: "stopped",
                          Status.ERROR: "failed", Status.NEEDS_CONFIRMATION: "waiting_confirmation"}[result.status]
                self.store.save_job(job, status)
                if status in ("stopped", "failed") and (live["text"] or live["reasoning"]):
                    if not any(m.get("role") == "assistant" and m.get("step_id") == live["step"]
                               and m.get("run_id") == rid for m in session.messages[-8:]):
                        session.add("assistant", live["text"], reasoning=live["reasoning"])
                self.store.emit(sid, "run_status", {"run_id": rid, "status": status,
                    "text": result.text, "pending": result.pending_summary if status == "waiting_confirmation" else [],
                    "usage": session.meta.get("last_usage", {})})
                if status == "failed" and live.get("text"):
                    atomic_write_text(session.dir / "interrupted-live.json", json.dumps(live, ensure_ascii=False))
                return
        finally:
            session.on_message = None
            live["phase"] = "idle"
            flush(True)
            self.discover_results(session, agent)
            self.store.emit(sid, "session_changed", {"id": sid})

    def message_payload(self, session, message):
        value = copy.deepcopy(message)
        value["files"] = [self.store.register_file(path, session.id, "image")
                          for path in message.get("images", []) if Path(path).is_file()]
        for key in message.get("attachments", []):
            item = self.store.file(key)
            if item and not any(f["path"] == item["path"] for f in value["files"]):
                value["files"].append(self.store.register_file(item["path"], session.id, "attachment"))
        return value

    def discover_results(self, session, agent=None):
        directories = [session.dir / "exports"]
        if session.meta.get("workspace"):
            directories.append(Path(session.meta["workspace"]) / "exports")
        for directory in directories:
            if directory.is_dir():
                for path in directory.iterdir():
                    if path.is_file():
                        self.store.register_file(path, session.id)
        if agent:
            for item in agent.ctx.changes.summary().get("files", []):
                path = agent.ctx.workspace / item["path"]
                if item["changed"] and path.is_file():
                    self.store.register_file(path, session.id, "changed")

    def close(self):
        with self.wake:
            self.closed = True
            self.abort.set()
            self.wake.notify_all()
        self.worker.join(timeout=3)
