"""User operations shared by web actions and slash commands."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from pathlib import Path

from harness.changes import ChangeJournal, atomic_write_text
from harness.context import summarize_messages
from harness.documents import export_document, read_document_content
from harness.decisions import DecisionStore
from harness.memory import MemoryStore
from harness.projects import Projects
from harness.research import ResearchLedger
from harness.session import Session
from harness.skills import SkillLibrary
from harness.work_modes import WORK_MODES

COMMANDS = {
    "/help": "Commands", "/skills": "Skill library", "/skill": "Load a skill",
    "/compress": "Compress earlier context", "/handoff": "Continue in a new chat",
    "/revert": "Revert task files", "/checkpoint": "Create a restore point",
    "/search": "Search the project", "/pin": "Pin a file", "/unpin": "Unpin a file",
    "/pins": "Pinned files", "/test": "Run project checks", "/plan": "Plan a task",
    "/review": "Review changes", "/clear": "New conversation", "/stop": "Stop the task",
}


def execute_command(app, agent, job):
    raw = job["text"].strip()
    command, _, argument = raw.partition(" ")
    session = agent.session
    workspace = agent.ctx.project_workspace
    if command in ("/test", "/plan", "/review"):
        return {"/test": "Run the project's appropriate checks and report the actual results. ",
                "/plan": "Discuss a plan for this task before implementing it: ",
                "/review": "Review the current project changes for functional problems. "}[command] + argument
    if command == "/skill":
        if argument.startswith(("new", "create", "designer")):
            return "Design an optional SKILL.md workflow for this request and store it in the user or project skill directory: " + argument
        skill = SkillLibrary(app.cfg, workspace).read(argument.strip())
        session.meta.setdefault("active_skills", [])
        if argument.strip() not in session.meta["active_skills"]:
            session.meta["active_skills"].append(argument.strip())
        session._save_meta()
        return f"Use this optional skill as guidance, respecting the user's request:\n{skill}\n\n{argument}"
    if command not in COMMANDS:
        return raw
    session.add("user", raw)
    if command == "/help":
        result = "\n".join(f"- `{name}`: {description}" for name, description in COMMANDS.items())
    elif command == "/skills":
        result = SkillLibrary(app.cfg, workspace).catalog()
    elif command == "/pins":
        result = "\n".join(session.meta.get("pinned_files", [])) or "No pinned files."
    elif command in ("/pin", "/unpin"):
        target = agent.ctx.resolve(argument.strip().strip('"'))
        if command == "/pin":
            if not target.is_file():
                raise FileNotFoundError(target)
            session.pin_context_file(target)
        else:
            session.unpin_context_file(target)
        result = f"{command}: {target}"
    elif command == "/search":
        result = agent.registry.execute("search_project", {"query": argument}, agent.ctx)
    elif command == "/checkpoint":
        if not workspace:
            raise ValueError("Choose a project first")
        result = "Restore point: " + agent.ctx.changes.create_checkpoint(argument or "Manual checkpoint")
    elif command == "/revert":
        result = json.dumps(agent.ctx.changes.revert_last_task(), ensure_ascii=False)
    elif command == "/clear":
        new = app.new_session(str(workspace) if workspace else None, agent.work_mode)
        app.store.emit(session.id, "navigate", {"session_id": new.id})
        result = "New conversation created."
    elif command in ("/compress", "/handoff"):
        if app.manage_model:
            from harness import servermgmt
            if not servermgmt.ensure(agent.cfg, agent.cfg.model_key()):
                raise RuntimeError("Model server could not start")
        summary = summarize_messages(agent.llm, session._view_messages(), should_stop=app.abort.is_set)
        if command == "/compress":
            session.compress_to_summary(summary)
            result = "Earlier context compressed. Full original conversation remains available."
        else:
            new = app.new_session(str(workspace) if workspace else None, agent.work_mode)
            new.add("user", f"Continuation from chat {session.id}:\n\n{summary}")
            app.store.emit(session.id, "navigate", {"session_id": new.id})
            result = "Continuation chat: " + new.id
    else:
        result = "Task stopped."
    session.add("assistant", result)
    return None


def session_detail(app, session):
    from harness.application import read_json
    cfg = app.config_for(session)
    workspace = Path(session.meta["workspace"]) if session.meta.get("workspace") else None
    memory = MemoryStore(cfg, workspace, session.meta.get("work_mode"))
    skills = SkillLibrary(cfg, workspace)
    agent = app.agents.get(session.id)
    journal = agent.ctx.changes if agent else ChangeJournal(session, workspace or session.dir)
    from harness.task_plan import TaskPlanStore
    research = agent.ctx.research if agent else ResearchLedger(session)
    from harness.processes import ProcessManager
    manager = agent.ctx.processes if agent else ProcessManager()
    if not agent:
        manager.bind_session(session)
    app.discover_results(session, agent)
    files = [app.store.register_file(row["path"], session.id, row["kind"])
             for row in app.store.files(session.id) if row["kind"] in ("result", "changed")]
    checkpoints = []
    if journal.base.exists():
        for path in sorted(journal.base.glob("*/manifest.json"), reverse=True):
            from harness.application import read_json
            manifest = read_json(path)
            checkpoints.append({"id": manifest.get("task_id"), "label": manifest.get("label"),
                                "files": len(manifest.get("files", [])), "created": manifest.get("created"),
                                "restored": bool(manifest.get("undone_at")), "snapshot": manifest.get("snapshot", False)})
    paths = {scope: memory._path_for(scope) for scope in ("global", "mode", "project")}
    return {
        "decisions": DecisionStore(workspace).list(),
        "notices": app.store.notices(session.id),
        "context": {**session.context_breakdown(), "limit": cfg.context_size(),
                    "snapshot": read_json(Path(session.meta["context_snapshot"])) if session.meta.get("context_snapshot") else None,
                    "usage": session.meta.get("last_usage", {}), "active_skills": session.meta.get("active_skills", []),
                    "breakdown": agent.context_usage_breakdown() if agent else {}},
        "memory": {scope: {"path": str(path) if path else None, "content": memory.read(scope) if path else ""}
                   for scope, path in paths.items()},
        "skills": [{"name": s.name, "description": s.description, "source": s.source, "path": str(s.path)} for s in skills.list()],
        "plan": TaskPlanStore(session).load(), "changes": journal.summary(), "checkpoints": checkpoints,
        "research": research.current(), "processes": manager.list(),
        "browser": agent.ctx.browser.status() if agent else {"running": False},
        "results": files,
    }


def perform_action(app, sid, action, payload):
    session = app.session(sid)
    active_here = app.active and app.active["session_id"] == sid
    if action == "decision":
        from harness.decisions import DecisionStore
        return DecisionStore(session.meta.get("workspace")).save(
            payload["text"], sid, payload.get("status", "accepted"), payload.get("id"))
    if action == "mode":
        if payload["mode"] not in WORK_MODES:
            raise ValueError("Unknown work mode")
        session.meta["work_mode"] = payload["mode"]
        session._save_meta()
        if not active_here:
            previous = app.agents.pop(sid, None)
            if previous:
                previous.ctx.browser.close()
        return {"ok": True}
    if action in ("rename", "draft"):
        if action == "rename":
            session.meta["title"] = str(payload.get("title", "")).strip() or None
            if session.meta["title"]:
                session.persist()
            session._save_meta()
        else:
            if session.transient:
                if not payload.get("text") and not payload.get("attachments"):
                    return {"ok": True}
                session.persist()
            atomic_write_text(session.dir / "draft.json", json.dumps(payload, ensure_ascii=False))
        return {"ok": True}
    if action == "stop":
        return {"stopped": app.stop(sid)}
    if action == "resume":
        return app.resume(sid, payload.get("approve"))
    if action in ("compress", "handoff", "checkpoint", "revert", "skill", "test", "plan", "review"):
        return app.submit(sid, "/" + action + " " + str(payload.get("argument", "")),
                          delivery="queue", kind="command")
    if action == "export":
        fmt = payload.get("format", "md")
        if payload.get("research"):
            ledger = ResearchLedger(session).current()
            content = (ledger or {}).get("synthesis") or ""
            target = export_document(content, session.dir / "exports", "research-synthesis", fmt)
        elif fmt == "jsonl":
            target = session.export_jsonl()
        elif fmt == "md":
            target = session.export_markdown()
        else:
            content = session.export_markdown().read_text(encoding="utf-8")
            target = export_document(content, session.dir / "exports", "conversation", fmt)
        return app.store.register_file(target, sid)
    if action == "export_sources":
        target = session.dir / "exports" / "research-sources.json"
        atomic_write_text(target, json.dumps(ResearchLedger(session).data, ensure_ascii=False, indent=2))
        return app.store.register_file(target, sid)
    if action == "memory":
        cfg = app.config_for(session)
        store = MemoryStore(cfg, session.meta.get("workspace"), session.meta.get("work_mode"))
        target = store._path_for(payload["scope"])
        if not target:
            raise ValueError("No project memory in this chat")
        atomic_write_text(target, payload["content"])
        return {"ok": True}
    if action in ("pin", "unpin"):
        path = Path(payload["path"]).resolve()
        if action == "pin":
            if not path.is_file():
                raise FileNotFoundError(path)
            session.pin_context_file(path)
        else:
            session.unpin_context_file(path)
        return {"ok": True}
    if action == "clear_pins":
        session.meta["pinned_files"] = []
        session._save_meta()
        return {"ok": True}
    if action == "stop_process":
        agent = app.agents.get(sid)
        from harness.processes import ProcessManager
        manager = agent.ctx.processes if agent else ProcessManager()
        if not agent:
            manager.bind_session(session)
        return manager.terminate(payload["id"])
    if action == "close_browser":
        if sid in app.agents:
            app.agents[sid].ctx.browser.close()
        return {"ok": True}
    if action == "read_skill":
        return {"content": SkillLibrary(app.cfg, session.meta.get("workspace")).read(payload["name"])}
    if action == "open_skill_folder":
        if payload.get("scope") == "project":
            if not session.meta.get("workspace"):
                raise ValueError("Select a project first")
            path = Path(session.meta["workspace"]) / app.cfg.data.get("skills", {}).get("project_directory", ".qwen-skills")
        else:
            path = app.cfg.root / app.cfg.data.get("skills", {}).get("user_directory", "user-skills")
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))
        return {"path": str(path)}
    if active_here:
        raise ValueError("Stop this task before moving, deleting or rewinding its conversation")
    if action == "move":
        project = next((p for p in Projects(app.cfg).list_all() if p["id"] == payload.get("project_id")), None)
        session.meta["workspace"] = project["path"] if project else None
        session.meta["work_mode"] = project["work_mode"] if project else session.meta.get("work_mode", "discussion")
        session._save_meta()
        old = app.agents.pop(sid, None)
        if old:
            old.ctx.browser.close()
        app.store.emit(sid, "session_changed", {})
        return {"ok": True}
    if action in ("retry", "undo"):
        question = session.rewind_last_turn(keep_user=action == "retry")
        if action == "retry" and question:
            # Queue a continuation over the existing user message, avoiding a duplicate turn.
            response = app.submit(sid, question, delivery="queue")
            job = response["payload"]
            job["resume"] = True
            app.store.save_job(job)
            return job
        return {"ok": True}
    if action == "fork":
        fork = session.fork_at_last_user("")
        if not fork:
            raise ValueError("Nothing to branch")
        file_ids = {}
        for message in fork.messages:
            for key in message.get("attachments", []):
                if key in file_ids:
                    continue
                original = app.store.file(key)
                if original and Path(original["path"]).is_file():
                    target = fork.dir / "attachments" / (key + Path(original["path"]).suffix)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original["path"], target)
                    file_ids[key] = app.store.register_file(target, fork.id, original["kind"], original["name"])["id"]
                    for copied in fork.messages:
                        if isinstance(copied.get("content"), str):
                            copied["content"] = copied["content"].replace(original["path"], str(target))
            if "attachments" in message:
                message["attachments"] = [file_ids.get(key, key) for key in message["attachments"]]
        fork._rewrite_jsonl()
        app.sessions[fork.id] = fork
        app.select_session(fork.id)
        return {"session_id": fork.id}
    if action == "delete":
        for job in app.store.jobs():
            if job["session_id"] == sid:
                app.store.save_job(job["payload"], "cancelled")
        Session.delete(app.cfg, sid)
        app.sessions.pop(sid, None)
        return {"ok": True}
    if action == "restore":
        journal = ChangeJournal(session, Path(session.meta.get("workspace") or session.dir))
        return journal.undo(payload["id"], force=bool(payload.get("force")))
    raise ValueError(f"Unknown action: {action}")
