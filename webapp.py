"""Web UI for the Qwen3.8-27B harness (Gradio, localhost only).

Run:  .venv/Scripts/python webapp.py  →  http://127.0.0.1:7860
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# pythonw (no console) has no stdout/stderr → redirect to a log to avoid silent death
if sys.stdout is None or sys.stderr is None:
    _logdir = ROOT / "runtime"
    _logdir.mkdir(parents=True, exist_ok=True)
    _lf = open(_logdir / "webapp.log", "a", buffering=1, encoding="utf-8")
    _lf.write(f"\n===== WEBAPP {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    sys.stdout = _lf
    sys.stderr = _lf

import gradio as gr

from harness.agent import Agent, Status, build_registry
from harness.config import load_config
from harness.i18n import detect_language, get_language, language_choices, set_language, t
from harness.llm import LLMClient
from harness.model_switch import ModelSwitchController
from harness.processes import ProcessManager
from harness.prompts import system_prompt
from harness.safety import SafetyPolicy
from harness.session import Session, IMG_MIMES
from harness.streaming import SteeringQueue, StreamHub, step_threaded
from harness.work_modes import WORK_MODES, normalize_work_mode
from harness import servermgmt

cfg = load_config()
llm = LLMClient(cfg)

# trvalá paměť: prázdný soubor globální paměti zakládáme hned na startu
from harness.memory import MemoryStore
from harness.version import APP_VERSION
MemoryStore(cfg)

# ------------------------------------------------------------- stav aplikace
STATE_FILE = cfg.path("paths.runtime_dir") / "webui-state.json"


def _load_ui_state() -> dict:
    import json
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_ui_state(data: dict) -> None:
    import json
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


class AppState:
    def __init__(self) -> None:
        self.hub = StreamHub()
        self.run_active = threading.Event()
        self.steering = SteeringQueue()
        self._run_claim_lock = threading.Lock()
        self.model_switch = ModelSwitchController(cfg)
        self.processes = ProcessManager()
        # po smazani chatu: nahradni (transient) chat NABIDNOUT v seznamech az
        # s prvni zpravou - nesmi tam hned svitit jako "(bez titulku)"
        self.suppress_active_entry = False
        # UI language: user choice (webui-state.json) > installer file > English
        saved = _load_ui_state()
        self.language = set_language(saved.get("language") or detect_language(ROOT))
        self.ui_reload = threading.Event()
        self.model_key = saved.get("model") or cfg.model_key()
        if self.model_key not in cfg.data["models"]:
            self.model_key = cfg.model_key()
        saved_kv = saved.get("kv_cache_modes") or {}
        for key in cfg.data["models"]:
            mode = saved_kv.get(key)
            if mode in cfg.kv_cache_profiles(key):
                cfg.set_kv_cache_mode(key, mode)
        self.kv_cache_modes = {
            key: cfg.kv_cache_mode(key) for key in cfg.data["models"]
        }
        cfg.data["default_model"] = self.model_key  # agent podle toho zná ctx limit
        legacy_mode = saved.get("mode") or cfg.agent.get("mode", "agent")
        self.work_mode = normalize_work_mode(
            saved.get("work_mode") or cfg.data.get("work_mode"), legacy_mode)
        self.mode = WORK_MODES[self.work_mode].agent_mode
        self.autonomy = saved.get("autonomy") or cfg.agent.get("autonomy", "supervised")
        self.thinking = saved.get("thinking", cfg.data.get("thinking", True))
        self.reasoning_effort = saved.get("reasoning_effort") or cfg.data.get("reasoning_effort", "xhigh")
        if self.reasoning_effort not in ("xhigh", "medium", "low"):
            self.reasoning_effort = "xhigh"
        cfg.data["thinking"] = bool(self.thinking)
        cfg.data["reasoning_effort"] = self.reasoning_effort
        cfg.data["work_mode"] = self.work_mode
        cfg.agent["mode"] = self.mode
        self.workspace = saved.get("workspace") or cfg.agent.get("workspace")
        self.recent_ws: list[str] = saved.get("recent", [])
        if self.workspace:
            cfg.agent["workspace"] = self.workspace  # převezme každý nový Agent
            from harness.projects import Projects
            Projects(cfg).ensure_registered(self.workspace)  # migrace → projekt
        self._restore_session()

    def claim_submission(self, message: str, files: list[str]) -> str:
        """Claim a new run, or steer the one that is already active."""
        with self._run_claim_lock:
            if not self.run_active.is_set():
                self.run_active.set()
                return "run"
            self.steering.push(message, files)
            self.abort.set()
            return "steer"

    def _restore_session(self) -> None:
        """Obnov session uloženou jako aktivní (fallback: poslední na disku)."""
        saved = _load_ui_state().get("session_id")
        if saved:
            try:
                self.session = Session.load(cfg, saved, self._system_prompt())
                self._adopt_session_work_mode()
                self.rebuild_agent()
                self._refresh_system_prompt()
                return
            except FileNotFoundError:
                pass
        try:
            latest = Session.list_sessions(cfg)
            if latest and latest[0]["messages"] > 1:
                self.session = Session.load(cfg, latest[0]["id"], self._system_prompt())
                self._adopt_session_work_mode()
                self.rebuild_agent()
                self._refresh_system_prompt()
                return
        except Exception:
            pass
        self.new_session()

    def _adopt_session_work_mode(self) -> None:
        """Převezmi z chatu režim i projekt, včetně explicitního 'bez projektu'."""
        session_mode = self.session.meta.get("work_mode")
        if session_mode in WORK_MODES:
            self.work_mode = session_mode
            self.mode = WORK_MODES[session_mode].agent_mode
            cfg.data["work_mode"] = session_mode
            cfg.agent["mode"] = self.mode
        session_workspace = self.session.meta.get("workspace")
        self.workspace = str(session_workspace) if session_workspace else None
        cfg.agent["workspace"] = self.workspace

    def save_ui_state(self) -> None:
        _save_ui_state({
            "workspace": self.workspace,
            "recent": self.recent_ws,
            "model": self.model_key,
            "kv_cache_modes": self.kv_cache_modes,
            "mode": self.mode,
            "work_mode": self.work_mode,
            "autonomy": self.autonomy,
            "thinking": bool(self.thinking),
            "reasoning_effort": self.reasoning_effort,
            "language": self.language,
            "session_id": getattr(self, "session", None).id if getattr(self, "session", None) else None,
        })

    def new_session(self) -> None:
        self.session = Session(cfg, system_prompt=self._system_prompt(),
                               workspace=self.workspace, transient=True,
                               work_mode=self.work_mode)
        self.rebuild_agent()
        try:
            self.save_ui_state()
        except Exception:
            pass

    def rebuild_agent(self) -> None:
        safety = SafetyPolicy(
            autonomy=self.autonomy,
        )
        self.abort = threading.Event()
        self.agent = Agent(cfg, llm, self.session,
                           build_registry(self.mode, self.work_mode),
                           safety, mode=self.mode, abort_flag=self.abort,
                           on_event=self.hub.on_event, process_manager=self.processes,
                           work_mode=self.work_mode)
        if self.workspace:
            try:
                self.agent.set_workspace(self.workspace)
            except ValueError:
                pass

    def _system_prompt(self) -> str:
        from harness.prompts import build_system_prompt
        return build_system_prompt(self.mode, cfg, self.workspace, self.work_mode)

    def _refresh_system_prompt(self) -> None:
        """Aktualizuj system prompt existující session (změna workspace/režimu)."""
        if self.session.messages and self.session.messages[0]["role"] == "system":
            self.session.messages[0]["content"] = self._system_prompt()

    def set_mode(self, mode: str) -> None:
        self.work_mode = normalize_work_mode(None, mode)
        self.mode = WORK_MODES[self.work_mode].agent_mode
        self.rebuild_agent()
        self._refresh_system_prompt()

    def set_work_mode(self, work_mode: str) -> None:
        self.work_mode = normalize_work_mode(work_mode, self.mode)
        self.mode = WORK_MODES[self.work_mode].agent_mode
        cfg.data["work_mode"] = self.work_mode
        cfg.agent["mode"] = self.mode
        if getattr(self, "session", None):
            self.session.meta["work_mode"] = self.work_mode
            self.session._save_meta()
        if self.workspace:
            from harness.projects import Projects
            Projects(cfg).set_work_mode(self.workspace, self.work_mode)
        self.rebuild_agent()
        self._refresh_system_prompt()

    def set_workspace(self, path: str, adopt_project_mode: bool = True) -> Path:
        """Nastav workspace + persist do state souboru."""
        p = self.agent.set_workspace(path)  # ValueError pokud neexistuje
        self.workspace = str(p)
        cfg.agent["workspace"] = str(p)
        self.recent_ws = [str(p)] + [w for w in self.recent_ws if w != str(p)]
        self.recent_ws = self.recent_ws[:8]
        from harness.projects import Projects
        project = Projects(cfg).ensure_registered(str(p))
        if adopt_project_mode and project and project.get("work_mode") in WORK_MODES:
            self.work_mode = project["work_mode"]
            self.mode = WORK_MODES[self.work_mode].agent_mode
            cfg.data["work_mode"] = self.work_mode
            cfg.agent["mode"] = self.mode
            self.session.meta["work_mode"] = self.work_mode
            self.rebuild_agent()
        self.save_ui_state()
        self._refresh_system_prompt()
        try:
            MemoryStore(cfg, p, self.work_mode).ensure_project()
        except Exception:
            pass
        return p

    def clear_workspace(self) -> None:
        """Zruš výběr projektu (nové chatty bez příslušnosti, agent bez workspace)."""
        self.workspace = None
        cfg.agent["workspace"] = None
        self.rebuild_agent()
        self._refresh_system_prompt()
        self.save_ui_state()


def _live_message(hub: StreamHub, elapsed_s: int = 0) -> dict:
    """Živá zpráva: streamovaný text, nebo 'uvažuji' s poctivým indikátorem aktivity.

    elapsed_s = sekundy od posledního toku - roste, i když model mlčí (poctivé:
    uživatel vidí, že se nic neděje; blikající kurzor se obnovuje jen s daty).
    """
    text, reasoning, _, _ = hub.snapshot()
    progress = hub.progress()
    cursor = ' <span class="blink-cursor">▍</span>'
    tool_line = ""
    if progress["tools_running"]:
        descriptions = [_tool_progress_text(name, args, preparing=False)
                        for name, args in progress["tools_running"]]
        tool_line = t("🔧 <i>{tools} ({sec}s)</i>", tools=" · ".join(descriptions), sec=elapsed_s)
    elif progress["tool_call_chars"]:
        name = progress["tool_call_name"] or t("tool")
        chars = int(progress["tool_call_chars"])
        amount = f"{chars}" if chars < 1000 else f"{chars / 1000:.1f}k"
        detail = _tool_progress_text(
            name, progress.get("tool_call_preview") or "", preparing=True)
        tool_line = t("🧰 <i>{detail} · generated ~{amount} chars</i>",
                      detail=detail, amount=amount)
    if text:
        if tool_line:
            suffix = "\n\n" + tool_line
        else:
            suffix = f"\n\n<i>{t('⏳ {sec}s without new tokens', sec=elapsed_s)}</i>" if elapsed_s >= 5 else ""
        return {"role": "assistant", "content": text + cursor + suffix}
    if tool_line:
        return {"role": "assistant", "content": tool_line + cursor}
    tail = reasoning[-200:].replace("\n", " ") if reasoning else ""
    head = t("💭 <i>thinking… ({sec}s)</i>", sec=elapsed_s)
    return {"role": "assistant",
            "content": (head + f" <small>{tail}</small>" if tail else head) + cursor}


def _tool_progress_text(name: str, arguments, *, preparing: bool) -> str:
    import re
    args = arguments if isinstance(arguments, dict) else {}
    raw = arguments if isinstance(arguments, str) else ""
    path = str(args.get("path") or "")
    if not path and raw:
        match = re.search(r'"path"\s*:\s*"([^"]+)', raw)
        path = match.group(1) if match else ""
    filename = Path(path).name if path else ""
    target = f" `{filename}`" if filename else ""
    if name == "write_file":
        verb = t("Preparing content") if preparing else t("Saving file")
        return f"{verb}{target}…"
    if name == "apply_patch":
        return t("Preparing file edits…") if preparing else t("Applying file edits…")
    if name in ("run_command", "start_command"):
        return t("Preparing a command or test…") if preparing else t("Running a command or test…")
    if name == "poll_command":
        return t("Checking a long-running operation…")
    if name == "read_file":
        return t("Reading file{target}…", target=target)
    if name in ("search_files", "list_dir", "repo_overview"):
        return t("Scanning the project…")
    if name in ("web_search", "web_fetch"):
        return t("Browsing web sources…")
    verb = t("Preparing") if preparing else t("Running")
    return f"{verb} `{name}`…"


def _live_token_estimate() -> int:
    return int(state.hub.progress()["generated_chars"]) * 10 // 36


state = AppState()


# ------------------------------------------------------------- render helpers
_HIDDEN_NOTE_PREFIXES = ("[TASK PROTOCOL", "[PROGRESS UPDATE", "[FINAL SUMMARY",
                         "[Interrupted by user]", "[RESEARCH PLAN", "[DYNAMIC TASK CONTEXT")


def chat_view() -> list[dict]:
    """Převeď session messages do formátu gr.Chatbot (celá historie včetně komprimované části)."""
    from harness.agent import _PROTOCOL_MARKS
    hidden = tuple(_PROTOCOL_MARKS) + _HIDDEN_NOTE_PREFIXES[3:]
    out = []
    cut = state.session.compression["cut"] if state.session.compression else None
    for idx, m in enumerate(state.session.messages):
        if cut is not None and idx == cut:
            out.append({"role": "assistant",
                        "content": t("📦 **Context compressed** — everything above this marker is no "
                                     "longer visible to the model (it works from a summary). Your "
                                     "history stays complete.")})
        role = m["role"]
        if role == "system" or (role == "assistant" and not m.get("content")):
            continue
        if role == "user" and str(m.get("content", "")).startswith(hidden):
            continue  # interní protokolové poznámky se v chatu nezobrazují
        content = m.get("content") or ""
        imgs = m.get("images", [])
        if role == "tool":
            name = m.get("name", "tool")
            short = content if len(content) <= 400 else content[:400] + " …"
            out.append({"role": "assistant",
                        "content": f"🔧 **{name}** → {short}"})
            continue
        if role == "user" and imgs and content.startswith("[The following image"):
            # zpráva s přiloženými obrázky od nástrojů
            out.append({"role": "assistant",
                        "content": t("🖼️ attached image: {name}", name=Path(imgs[-1]).name)})
            continue
        msg: dict = {"role": role, "content": content or "…"}
        if role == "user" and imgs:
            msg["content"] = (content + "\n" if content else "") + t("🖼️ +{count} image(s)", count=len(imgs))
        out.append(msg)
    return out


def _content_str(msg: dict) -> str:
    """Obsah zprávy jako string - zvládá plain string i Gradio list-of-parts formát."""
    c = msg.get("content", "")
    if isinstance(c, str):
        return c
    if isinstance(c, list):  # [{"type": "text", "text": "..."}, ...]
        return " ".join(str(p.get("text", "")) for p in c if isinstance(p, dict))
    return str(c)


def _is_pending_question(msg: dict) -> bool:
    # marker in the active language + legacy Czech chats
    content = _content_str(msg)
    return msg.get("role") == "assistant" and (
        "Waiting for action confirmation" in content or "Čekám na potvrzení" in content)


def _error_message(e: BaseException) -> str:
    """Jemná chybová zpráva do chatu (místo červeného overlay Gradia)."""
    import traceback
    lines = traceback.format_exc(limit=4).strip().splitlines()
    tail = lines[-1][:200] if len(lines) > 1 else ""
    msg = (f"{t('❌ **An error occurred** — `{error}`', error=f'{type(e).__name__}: {e}')}\n\n"
           f"<small>`{tail}`</small>\n\n"
           f"{t('You can try continuing with another message. If the problem persists, try **🆕 New chat** or **▶ Start server**.')}")
    return msg


def _agent_error_message(r) -> str:
    """Chybový stav agenta (Status.ERROR) jako srozumitelná zpráva."""
    hint = ""
    if "Connection" in r.text or "Connect" in r.text or "timeout" in r.text.lower():
        hint = "\n\n" + t("💡 *Looks like an inference server problem — try **▶ Start server**.*")
    elif "tool" in r.text.lower():
        hint = "\n\n" + t("💡 *A tool failed — try phrasing the task differently.*")
    return f"⚠️ **{r.text}**{hint}"


def _run_steps(history: list[dict], approve: bool | None = None):
    """Generátor: krokuj agentem; živý text obnovuje klidným tempem (~2×/s).

    Krok agenta běží ve vlákně, události (text/reasoning) tečou přes StreamHub,
    tady se pollingují a promítají do dočasné "live" zprávy v chatu.
    Výjimky zachytává a vrací jako zprávu v chatu (nikdy nenechá spadnout UI).
    """
    import time as _time

    state.run_active.set()
    try:
        first = True
        seen_rev = state.session.compression_rev
        while True:
            state.hub.reset()
            t, box = step_threaded(state.agent, approve if first else None)
            first = False
            live_idx: int | None = None
            last_rev = -1
            last_change = _time.time()
            shown_sec = -1
            prev_yield_rev = -1
            last_yield_at = 0.0
            idle_strikes = 0  # počítadla pro dead-man detekci zombie streamu
            while t.is_alive():
                _, _, rev, last_activity = state.hub.snapshot()
                now = _time.time()
                if rev != last_rev:
                    last_rev = rev
                    last_change = now
                elapsed = int(now - last_change)
                # yield při nových datech, nebo každou sekundu (poctivý indikátor)
                has_new_content = rev != prev_yield_rev and now - last_yield_at >= 0.6
                idle_tick = elapsed != shown_sec and elapsed > 0
                if has_new_content or idle_tick:
                    prev_yield_rev = rev
                    shown_sec = elapsed
                    last_yield_at = now
                    live = _live_message(state.hub, elapsed)
                    if live_idx is None:
                        history.append(live)
                        live_idx = len(history) - 1
                    else:
                        history[live_idx] = live
                    yield history, gr.update(visible=False), gr.update()
                # DEAD-MAN: >90s bez jakékoli aktivity → zkontroluj, jestli server
                # něco dělá; když ne (2× za sebou), spojení je zombie → uživ nemůže
                # čekat na timeout (až 300s) s blokovanou frontou
                if now - last_activity > 90:
                    from harness import servermgmt
                    busy = servermgmt.slots_processing(cfg)
                    idle_strikes = idle_strikes + 1 if busy is False else 0
                if idle_strikes >= 2:
                    state.abort.set()
                    if live_idx is not None:
                        history.pop(live_idx)
                    history.append({"role": "assistant",
                                    "content": t("🔌 **Connection to the server stalled** (the server "
                                                 "is no longer generating but the response never "
                                                 "arrived). The response was not completed — try "
                                                 "sending the message again.")})
                    yield history, gr.update(visible=False), refresh_status()
                    return
                else:
                    idle_strikes = 0
                _time.sleep(0.15)
            t.join()
            # Dočasný kurzor odstraň; stabilní text se níže obnoví z autoritativní session.
            if live_idx is not None:
                history.pop(live_idx)
            if "e" in box:
                raise box["e"]
            r = box.get("r")
            # live marker, pokud během kroku došlo ke kompresi kontextu
            if state.session.compression_rev != seen_rev:
                seen_rev = state.session.compression_rev
            if r is None:
                raise RuntimeError("agent step ended without a result")
            steering = state.steering.pop_all()
            if steering:
                for text, files in steering:
                    images = [Path(path) for path in files
                              if Path(path).suffix.lower() in IMG_MIMES]
                    state.agent.steer(text, images=images)
                history = chat_view()
                yield history, gr.update(visible=False), refresh_status()
                continue
            if r.status is Status.CONTINUE:
                history = chat_view()
                yield history, gr.update(visible=False), refresh_status()
            elif r.status is Status.FINAL:
                history = chat_view()
                yield history, gr.update(visible=False), refresh_status()
                return
            elif r.status is Status.NEEDS_CONFIRMATION:
                history = chat_view()
                if r.text:
                    history.append({"role": "assistant", "content": r.text})
                lines = "\n".join(f"⚠️ `{a}`" for a in r.pending_summary)
                history.append({"role": "assistant",
                                "content": f"{t('**Waiting for action confirmation:**')}\n{lines}"})
                yield history, gr.update(visible=True), refresh_status()
                return
            else:  # ABORTED / ERROR
                history = chat_view()
                text = _agent_error_message(r) if r.status is Status.ERROR else f"⛔ {r.text}"
                history.append({"role": "assistant", "content": text})
                yield history, gr.update(visible=False), refresh_status()
                return
    except Exception as e:  # pojistka - žádné spadnutí UI
        history = chat_view()
        history.append({"role": "assistant", "content": _error_message(e)})
        yield history, gr.update(visible=False), refresh_status()
    finally:
        state.run_active.clear()


# ------------------------------------------------------------- handlery
def prepare_submission(message: str, files):
    """Immediately clear the composer and route the message to run or steering."""
    text = (message or "").strip()
    paths = [str(path) for path in (files or [])]
    if not text and not paths:
        return {"kind": "ignore"}, gr.update(), gr.update(value=""), gr.update(value=None)
    kind = state.claim_submission(text, paths)
    if kind == "steer":
        gr.Info(t("Clarification received — finishing the current sentence and redirecting the running task."))
        return {"kind": kind}, gr.update(), gr.update(value=""), gr.update(value=None)
    try:
        cfg.data["thinking"] = state.thinking
        state.suppress_active_entry = False
        images = [Path(path) for path in paths if Path(path).suffix.lower() in IMG_MIMES]
        state.agent.new_task(text or "Please analyze the attached image(s).", images=images)
        return {"kind": kind}, chat_view(), gr.update(value=""), gr.update(value=None)
    except Exception:
        state.run_active.clear()
        raise


def run_prepared_submission(submission: dict, _browser_history: list[dict]):
    kind = (submission or {}).get("kind")
    if kind != "run":
        yield chat_view(), gr.update(visible=False), refresh_status()
        return
    try:
        history = chat_view()
        yield history, gr.update(visible=False), refresh_status()
        yield from _run_steps(history)
    finally:
        state.run_active.clear()


def send_message(message: str, files, _browser_history: list[dict]):
    """Compatibility wrapper for non-Gradio callers."""
    try:
        history = chat_view()
        if not (message or "").strip() and not files:
            yield history, gr.update(visible=False), refresh_status()
            return
        cfg.data["thinking"] = state.thinking
        state.suppress_active_entry = False  # zpráva = chat začíná být skutečný
        imgs = [Path(f) for f in (files or []) if Path(f).suffix.lower() in IMG_MIMES]
        state.agent.new_task(message.strip() or "Please analyze the attached image(s).", images=imgs)
        history = chat_view()
        yield history, gr.update(visible=False), refresh_status()
        yield from _run_steps(history)
    except Exception as e:
        history.append({"role": "assistant", "content": _error_message(e)})
        yield history, gr.update(visible=False), refresh_status()


def confirm(approve: bool, history: list[dict]):
    """Reakce na tlačítka Povolit/Zamítnout."""
    try:
        history = chat_view()
        if not state.agent._pending:
            # není co potvrzovat (např. po dvojkliku) - jen zavři panel
            if history and _is_pending_question(history[-1]):
                history.pop()
            yield history, gr.update(visible=False), refresh_status()
            return
        # odeber zprávu s dotazem a zaloguj rozhodnutí uživatele
        if history and _is_pending_question(history[-1]):
            history.pop()
        history.append({"role": "user", "content": t("✅ Allow") if approve else t("❌ Deny")})
        yield history, gr.update(visible=False), refresh_status()
        yield from _run_steps(history, approve=approve)
    except Exception as e:
        history.append({"role": "assistant", "content": _error_message(e)})
        yield history, gr.update(visible=False), refresh_status()


def confirm_yes(history: list[dict]):
    """btn_yes handler - MUSÍ být generátor (Gradio iteruje yieldy)."""
    yield from confirm(True, history)


def confirm_no(history: list[dict]):
    """btn_no handler - MUSÍ být generátor."""
    yield from confirm(False, history)


def stop_run(_history: list[dict]):
    state.abort.set()
    gr.Info(t("Stop received — finishing the current sentence."))
    return gr.update(), gr.update(visible=False), refresh_status()


def retry_last_answer():
    prompt = state.session.rewind_last_turn(keep_user=True)
    if prompt is None:
        gr.Warning(t("No prompt in this chat to retry."))
        yield chat_view(), gr.update(visible=False), refresh_status()
        return
    state.rebuild_agent()
    state.agent.resume_task(f"Retry: {prompt}")
    state.save_ui_state()
    history = chat_view()
    yield history, gr.update(visible=False), refresh_status()
    yield from _run_steps(history)


def resumable_task_text() -> str:
    task = state.session.load_task_state()
    if task.get("status") not in ("running", "waiting_confirmation"):
        return t("<small>No unfinished task.</small>")
    status = t("waiting for confirmation") if task["status"] == "waiting_confirmation" else t("ready to continue")
    label = str(task.get("label") or t("Unfinished task"))[:160]
    return f"**{status}**\n\n{label}\n\n{t('Step: {count}', count=task.get('steps', 0))}"


def refresh_resumable_task():
    active = state.agent.has_resumable_task
    return (resumable_task_text(), gr.update(interactive=active),
            gr.update(visible=active))


def continue_saved_task():
    if not state.agent.has_resumable_task:
        gr.Info(t("No unfinished task is available."))
        yield chat_view(), gr.update(visible=False), refresh_status()
        return
    history = chat_view()
    yield history, gr.update(visible=False), refresh_status()
    yield from _run_steps(history)


def edit_last_question():
    prompt = state.session.rewind_last_turn(keep_user=False)
    if prompt is None:
        gr.Warning(t("No prompt in this chat to edit."))
        return chat_view(), gr.update(), gr.update(visible=False), refresh_status()
    state.rebuild_agent()
    state.save_ui_state()
    return chat_view(), gr.update(value=prompt), gr.update(visible=False), refresh_status()


def undo_last_round():
    prompt = state.session.rewind_last_turn(keep_user=False)
    if prompt is None:
        gr.Warning(t("No round in this chat to undo."))
    else:
        gr.Info(t("The last question and answer were removed from the chat."))
    state.rebuild_agent()
    state.save_ui_state()
    return chat_view(), gr.update(visible=False), refresh_status()


def fork_last_round():
    fork = state.session.fork_at_last_user(state._system_prompt())
    if fork is None:
        gr.Warning(t("No prompt in this chat to fork."))
        yield chat_view(), gr.update(visible=False), refresh_status()
        return
    state.session = fork
    state.rebuild_agent()
    prompt_index = state.session.last_user_index()
    prompt = str(state.session.messages[prompt_index]["content"]) if prompt_index is not None else ""
    state.agent.resume_task(f"Fork retry: {prompt}")
    state.save_ui_state()
    history = chat_view()
    yield history, gr.update(visible=False), refresh_status()
    yield from _run_steps(history)


def new_chat():
    state.suppress_active_entry = False
    state.new_session()
    return [], gr.update(visible=False), refresh_status()


def compress_now(history: list[dict]):
    """📦 Ruční komprese: souhrn starší konverzace (i pod prahem 85 %)."""
    try:
        est = state.session.estimate_context_tokens()
        rev_before = state.session.compression_rev
        gr.Info(t("📦 Summarizing the older conversation (this takes a while)…"))
        state.agent._maybe_compress(force=True)
        if state.session.compression_rev == rev_before:
            gr.Warning(t("Nothing to compress (conversation too short)."))
            yield history, gr.update(visible=False), refresh_status()
            return
        est2 = state.session.estimate_context_tokens()
        history.append({"role": "assistant",
                        "content": t("📦 **Manual compression done** — ~{before}k → ~{after}k tokens. "
                                     "The model works from the summary; the history stays complete.",
                                     before=f"{est / 1000:.1f}", after=f"{est2 / 1000:.1f}")})
        gr.Info(t("✅ Compressed: ~{before}k → ~{after}k tokens",
                  before=f"{est / 1000:.1f}", after=f"{est2 / 1000:.1f}"))
        yield history, gr.update(visible=False), refresh_status()
    except Exception as e:
        history.append({"role": "assistant", "content": _error_message(e)})
        yield history, gr.update(visible=False), refresh_status()


def handoff_to_new_session():
    """📦 Předat práci do nové session: souhrn stávající konverzace + čistý kontext."""
    try:
        if len(state.session.messages) <= 2:
            gr.Warning(t("This chat is empty — nothing to hand off."))
            yield chat_view(), gr.update(visible=False), refresh_status()
            return
        gr.Info(t("📦 Summarizing the older conversation (this takes a while)…"))
        from harness.context import summarize_messages
        summary = summarize_messages(llm, state.session.messages[1:])
        state.suppress_active_entry = False
        state.new_session()
        state.session.add(
            "user",
            "[HANDOFF from previous session]\n" + summary +
            "\n\nThis is a summary of the previous session. Continue the work from this state.")
        gr.Info(t("✅ New chat with the summary is ready"))
        yield chat_view(), gr.update(visible=False), refresh_status()
    except Exception as e:
        history_view = chat_view()
        history_view.append({"role": "assistant", "content": _error_message(e)})
        yield history_view, gr.update(visible=False), refresh_status()


def _rel_time(ts: float) -> str:
    if not ts:
        return ""
    import time as _t
    d = _t.time() - ts
    if d < 90:
        return t("just now")
    if d < 3600:
        return t("{count} min ago", count=int(d // 60))
    if d < 86400:
        return t("{count} h ago", count=int(d // 3600))
    return t("{count} d ago", count=int(d // 86400))


# cache id podle řádku (Dataframe předává hodnoty, ne indexy meta)
_sessions_rows: list[dict] = []


def session_rows() -> list[list]:
    """Řádky pro 🕘 Historie: seskupeno podle projektu (aktuální první)."""
    global _sessions_rows
    try:
        sessions = Session.list_sessions(cfg)
    except Exception:
        _sessions_rows = []
        return []
    cur = state.workspace
    sessions = [s for s in sessions if s["messages"] > 1 or s.get("workspace") == cur]
    sessions.sort(key=lambda s: (s.get("workspace") != cur, s.get("workspace") or "",
                                  -s["updated"]))
    _sessions_rows = sessions
    rows = []
    last_ws = object()
    for s in sessions:
        ws = s.get("workspace")
        proj = Path(ws).name if ws else t("— no project —")
        marker = "▶ " if ws == cur else ""
        rows.append([f"{marker}{proj}", s["title"][:70], _rel_time(s["updated"]),
                     s["messages"], s["id"]])
    return rows


def sessions_refresh():
    return gr.update(value=session_rows())


def load_from_row(sel_evt, df_value):
    """(zastaralé - nahrazeno select_row_handler; ponecháno pro kompatibilitu)"""
    yield from []


def rename_session(name: str):
    """Přejmenuj aktuální chat po potvrzení Enterem."""
    try:
        name = (name or "").strip()
        if not name:
            gr.Warning(t("Enter a new chat name."))
            return gr.update(), gr.update(), gr.update(), gr.update()
        state.session.meta["title"] = name[:100]
        state.session._save_meta()
        gr.Info(t("✅ Chat renamed: {name}", name=name[:60]))
        r1, r2, b = update_chats_radio()
        return gr.update(value=""), r1, r2, b
    except Exception as e:
        gr.Warning(f"❌ {e}")
        return gr.update(), gr.update(), gr.update(), gr.update()


def load_session_handler(selection: str):
    """Načti session; pokud patří jinému projektu, přepni i workspace."""
    try:
        if not selection or selection == state.session.id:
            yield (chat_view(), gr.update(visible=False), refresh_status(), gr.update(),
                   gr.update(value=state.work_mode))
            return
        previous_workspace = state.workspace
        state.session = Session.load(cfg, selection, state._system_prompt())
        state._adopt_session_work_mode()
        state.rebuild_agent()
        state._refresh_system_prompt()
        state.suppress_active_entry = False
        # Session je autorita: projekt se přepne i tehdy, když cílem je "bez projektu".
        s_ws = state.session.meta.get("workspace")
        if s_ws != previous_workspace:
            target = Path(s_ws).name if s_ws else t("no project")
            gr.Info(t("✅ Chat loaded + context switched to {target}", target=target))
        else:
            gr.Info(t("✅ Chat loaded: {title}", title=str(state.session.meta.get("title", selection)[:50])))
        state.save_ui_state()
        yield chat_view(), gr.update(visible=False), refresh_status(), \
            gr.update(choices=project_choices(), value=current_project_name()), \
            gr.update(value=state.work_mode)
    except Exception as e:
        gr.Warning(f"❌ {e}")
        yield (chat_view(), gr.update(visible=False), refresh_status(), gr.update(),
               gr.update(value=state.work_mode))


def search_chat_history(query: str):
    results = Session.search_sessions(cfg, query)
    choices = []
    for item in results:
        label = item.get("title") or t("(untitled)")
        if item.get("snippet"):
            label += f" · {item['snippet']}"
        choices.append((label[:160], item["id"]))
    if not choices:
        gr.Info(t("No matches found in history."))
    return gr.update(choices=choices, value=None)


def export_current_chat(fmt: str):
    path = state.session.export_jsonl() if fmt == "jsonl" else state.session.export_markdown()
    return gr.update(value=str(path), visible=True)


def import_chat_file(path: str | None):
    if not path:
        gr.Warning(t("Select a JSONL chat export first."))
        return chat_view(), gr.update(), refresh_status()
    try:
        history = chat_view()
        imported = Session.import_jsonl(
            cfg, Path(path), state._system_prompt(), workspace=state.workspace,
            work_mode=state.work_mode)
        state.session = imported
        state.rebuild_agent()
        state.suppress_active_entry = False
        state.save_ui_state()
        gr.Info(t("Chat imported as a new chat."))
        return chat_view(), gr.update(value=None), refresh_status()
    except (OSError, ValueError) as exc:
        gr.Warning(t("Chat import failed: {error}", error=exc))
        return chat_view(), gr.update(), refresh_status()


def _model_switch_succeeded(key: str) -> None:
    state.model_key = key
    cfg.data["default_model"] = key  # agent/ctx-limit sledují aktuální model
    state.save_ui_state()


def change_model(key: str):
    if not state.model_switch.request(key, on_success=_model_switch_succeeded):
        gr.Info(t("A model is already loading; wait for the current operation to finish."))
    return refresh_runtime_controls()


def kv_cache_choices(key: str) -> list[tuple[str, str]]:
    from harness.i18n import get_language
    suffix = "cs" if get_language() == "cs" else "en"
    return [
        (str(profile.get(f"label_{suffix}") or profile.get("label") or mode), mode)
        for mode, profile in cfg.kv_cache_profiles(key).items()
    ]


def kv_cache_control_update(key: str | None = None, *, busy: bool = False):
    key = key or state.model_key
    choices = kv_cache_choices(key)
    return gr.update(
        choices=choices,
        value=cfg.kv_cache_mode(key),
        interactive=len(choices) > 1 and not busy,
    )


def change_kv_cache(mode: str):
    key = state.model_key
    try:
        cfg.set_kv_cache_mode(key, mode)
    except ValueError as exc:
        gr.Warning(str(exc))
        return refresh_runtime_controls()
    state.kv_cache_modes[key] = mode
    state.save_ui_state()
    if not state.model_switch.request(
            key, restart=True, on_success=_model_switch_succeeded):
        gr.Info(t("A model is already loading; KV cache cannot be switched right now."))
    return refresh_runtime_controls()


def change_mode(mode: str):
    state.set_mode(mode)
    state.save_ui_state()
    return f"{t('Mode: **{mode}**', mode=mode)} · {refresh_status()}"


def change_work_mode(work_mode: str):
    state.set_work_mode(work_mode)
    state.save_ui_state()
    changes, processes, research = work_mode_panel_updates()
    memory = _mem_infos()
    return (t("Work mode: **{mode}**", mode=t(WORK_MODES[state.work_mode].label)),
            changes, processes, research,
            *(gr.update(value=value) for value in memory))


def work_mode_panel_updates():
    return (
        gr.update(visible=state.work_mode in ("writing", "development", "computer")),
        gr.update(visible=state.work_mode in ("development", "computer")),
        gr.update(visible=state.work_mode == "research"),
    )


def change_autonomy(a: str):
    state.autonomy = a
    state.rebuild_agent()
    state.save_ui_state()
    return t("Autonomy: **{level}**", level=a)


def change_thinking(value: str):
    """Přemýšlení: xhigh / medium / low / off."""
    value = (value or "xhigh").strip().lower()
    if value == "off":
        state.thinking = False
    else:
        state.thinking = True
        state.reasoning_effort = value if value in ("xhigh", "medium", "low") else "xhigh"
    cfg.data["thinking"] = state.thinking
    cfg.data["reasoning_effort"] = state.reasoning_effort
    state.save_ui_state()
    mode_txt = "off" if not state.thinking else state.reasoning_effort
    return t("Thinking: **{level}**", level=mode_txt)


def _ctx_pct() -> int:
    try:
        est = state.agent.estimate_context_tokens()
        limit = cfg.context_size()
        return min(200, est * 100 // max(limit, 1))
    except Exception:
        return 0


def _check_ctx_warning(pct: int | None = None) -> None:
    """Toast varování při překročení prahů kontextu (jen při přechodu, ne opakovaně)."""
    pct = _ctx_pct() if pct is None else pct
    prev = getattr(state, "last_ctx_pct", 0)
    state.last_ctx_pct = pct
    if prev < 70 <= pct < 85:
        gr.Warning(t("📊 Context at {pct}% — auto-compression runs at 85%", pct=pct))
    elif prev < 85 <= pct:
        gr.Warning(t("📊 Context at {pct}% — near the limit! Consider 📦 Hand off (summary into a new chat)", pct=pct))


def _memory_paths():
    from harness.memory import MemoryStore
    store = MemoryStore(
        cfg, Path(state.workspace) if state.workspace else None, state.work_mode)
    return store


# ------------------------------------------------------------- projekty
from harness.projects import Projects


def _projects() -> Projects:
    return Projects(cfg)


# stabilní sentinel "bez projektu" - porovnává se ve value částech dropdownů,
# zobrazovaný label se překládá (choices jsou (label, value) dvojice)
NOPROJ_NAME = "__noproject__"
_project_del_arm: dict = {"ts": 0.0, "path": None}


def project_choices() -> list[tuple[str, str]]:
    return [(t("No project"), NOPROJ_NAME)] + [
        (p["name"], p["name"]) for p in _projects().list_all()
    ]


def current_project_name() -> str:
    if not state.workspace:
        return NOPROJ_NAME
    p = _projects().by_path(state.workspace)
    return p["name"] if p else Path(state.workspace).name


def set_project_handler(name: str):
    """Výběr projektu v dropdownu → nastav workspace (volba žádný projekt = bez projektu)."""
    try:
        if name == NOPROJ_NAME:
            state.clear_workspace()
            gr.Info(t("∅ No project — new chats will have no project"))
            return gr.update(choices=project_choices(), value=NOPROJ_NAME)
        proj = next((p for p in _projects().list_all() if p["name"] == name), None)
        if not proj:
            return gr.update()
        if proj.get("missing"):
            gr.Warning(t("Project folder does not exist: {path}", path=proj["path"]))
            return gr.update()
        state.set_workspace(proj["path"])
        gr.Info(t("📁 Project: {name}", name=proj["name"]))
        return gr.update(choices=project_choices(), value=proj["name"])
    except Exception as e:
        gr.Warning(f"❌ {e}")
        return gr.update()


def attach_project_handler():
    """📂 Připoj existující složku jako projekt (název dle složky)."""
    path = pick_directory_dialog()
    if not path:
        return gr.update(), gr.update(visible=False)
    try:
        proj = _projects().attach_folder(path)
        state.set_workspace(proj["path"])
        gr.Info(t("📁 Project attached: {name}", name=proj["name"]))
        return gr.update(choices=project_choices(), value=proj["name"]), gr.update(visible=False)
    except Exception as e:
        gr.Warning(f"❌ {e}")
        return gr.update(), gr.update(visible=False)


def create_project_handler(name: str):
    """➕ Nový projekt: vytvoří složku v projects/ a zaregistruje."""
    try:
        name = (name or "").strip()
        if not name:
            gr.Warning(t("Enter a project name."))
            return gr.update(), gr.update(visible=False), ""
        proj = _projects().create_new(name)
        state.set_workspace(proj["path"])
        gr.Info(t("📁 Project created {name} → {path}", name=proj["name"], path=proj["path"]))
        return gr.update(choices=project_choices(), value=proj["name"]), gr.update(visible=False), ""
    except Exception as e:
        gr.Warning(f"❌ {e}")
        return gr.update(), gr.update(visible=False), ""


def delete_project_handler(name: str):
    """Two-click deletion of the registry entry, project chats and workspace tree."""
    import time as _t
    empty = (gr.update(),) * 5
    if not name or name == NOPROJ_NAME:
        gr.Warning(t("Select the project you want to delete first."))
        return (*empty, gr.update(value=""), gr.update())
    project = next((item for item in _projects().list_all() if item["name"] == name), None)
    if project is None:
        gr.Warning(t("Project is no longer in the registry."))
        choices = project_choices()
        return (gr.update(choices=choices, value=NOPROJ_NAME), *empty[1:],
                gr.update(value=""), gr.update(choices=move_project_choices(), value=""))
    now = _t.time()
    if (_project_del_arm.get("path") != project["path"]
            or now - float(_project_del_arm.get("ts") or 0) >= 8.0):
        _project_del_arm.update(ts=now, path=project["path"])
        gr.Warning(t("Confirm full project deletion with a second click within 8 seconds."))
        warning = t("⚠️ **Click again: this permanently deletes the whole project, its chats and folder**  "
                    "`{path}`", path=project["path"])
        return (*empty, gr.update(value=warning), gr.update())

    _project_del_arm.update(ts=0.0, path=None)
    project_path = project["path"]
    sessions = [item for item in Session.list_sessions(cfg, limit=10000)
                if item.get("workspace") == project_path]
    for item in sessions:
        Session.delete(cfg, item["id"])
    _projects().delete_by_path(project_path)
    state.clear_workspace()
    state.new_session()
    state.suppress_active_entry = True
    choices = project_choices()
    chats_update, noproj_update, _ = update_chats_radio()
    gr.Info(t("Project {name} including its folder and {count} chats was deleted.",
              name=project["name"], count=len(sessions)))
    return (
        gr.update(choices=choices, value=NOPROJ_NAME),
        chat_view(), chats_update, noproj_update, refresh_status(),
        gr.update(value=""),
        gr.update(choices=move_project_choices(), value=""),
    )


def _active_entry() -> tuple[str, str] | None:
    """Aktivní session jako položka seznamu (i transient - hned viditelná)."""
    s = getattr(state, "session", None)
    if s is None or getattr(state, "suppress_active_entry", False):
        return None
    title = (s.meta.get("title") or t("(untitled)"))[:38]
    when = t("just now") if s.transient else _rel_time(s.meta.get("updated") or time.time())
    return (f"💬 {title}  ·  {when}", s.id)


def chat_choices() -> list[tuple[str, str]]:
    """(popisek, id) chatů AKTUÁLNÍHO projektu - pro sidebar radio."""
    try:
        cur = state.workspace
        if cur is None:
            return []  # bez vybraného projektu nezobrazuj chaty jako "projektové"
        sessions = [s for s in Session.list_sessions(cfg, limit=200)
                    if s.get("workspace") == cur]
        sessions.sort(key=lambda s: s["updated"], reverse=True)
        out = []
        for s in sessions[:200]:
            label = f"💬 {s['title'][:38]}  ·  {_rel_time(s['updated'])}"
            out.append((label, s["id"]))
        act = _active_entry()
        if act and act[1] not in {sid for _, sid in out}                 and state.session.meta.get("workspace") == state.workspace:
            out.insert(0, act)  # aktivní chat hned nahoře (jen patří-li do tohoto projektu)
        return out
    except Exception:
        return []


def noproj_chat_choices() -> list[tuple[str, str]]:
    """(popisek, id) chatů BEZ projektu - sekce pod seznamem projektu."""
    try:
        in_main = {sid for _, sid in chat_choices()}
        sessions = [s for s in Session.list_sessions(cfg, limit=200)
                    if not s.get("workspace") and s["id"] not in in_main]
        sessions.sort(key=lambda s: s["updated"], reverse=True)
        out = []
        for s in sessions[:200]:
            label = f"💬 {s['title'][:38]}  ·  {_rel_time(s['updated'])}"
            out.append((label, s["id"]))
        act = _active_entry()
        if act and act[1] not in {sid for _, sid in out} and not state.session.meta.get("workspace"):
            out.insert(0, act)  # aktivní (transient) chat hned nahoře
        return out
    except Exception:
        return []


def _del_state(armed: bool = False):
    """Viditelný stav mazání pod tlačítkem (tlačítko samo sebe v Gradio 6 updatovat nemůže)."""
    return gr.update(value=t("⚠️ **Confirm deletion — click again within 6 s**") if armed else "")


def update_chats_radio():
    """Aktualizuj oba seznamy chatů + stav mazání (3 výstupy)."""
    in_proj = bool(state.session.meta.get("workspace"))
    return (gr.update(choices=chat_choices(), value=state.session.id if in_proj else None),
            gr.update(choices=noproj_chat_choices(), value=None if in_proj else state.session.id),
            _del_state(False))


_del_arm: dict = {"ts": 0.0}


def delete_current_chat():
    """Smaž AKTUÁLNÍ chat - dvojklik ochrana (1. klik zobrazí varování, 2. do 6 s smaže)."""
    import time as _t
    now = _t.time()
    if now - _del_arm["ts"] >= 6.0:          # 1. klik → nabít
        _del_arm["ts"] = now
        gr.Warning(t("Confirm deletion: click the button again within 6 s."))
        # no-op pro ostatní komponenty = rychlé apply (jinak Gradio spolkne rychlé 2 kliknutí)
        return gr.update(), gr.update(), gr.update(), gr.update(), _del_state(True)
    _del_arm["ts"] = 0.0                     # 2. klik → smazat
    sid = state.session.id
    if state.session.transient:
        gr.Info(t("The active chat is not saved (empty) — nothing to delete."))
        return gr.update(), gr.update(), gr.update(), gr.update(), _del_state(False)
    state.new_session()                      # náhrada je transient - nic se neukládá
    state.suppress_active_entry = True       # a hned se v seznamech nenabízí
    ok = Session.delete(cfg, sid)
    gr.Info(t("🗑 Chat deleted") if ok else t("Chat no longer exists"))
    r1, r2, _ = update_chats_radio()
    return chat_view(), r1, r2, refresh_status(), _del_state(False)


def _current_chat_project() -> str:
    """Projekt aktivního chatu (pro dropdown přesunu)."""
    ws = state.session.meta.get("workspace") if getattr(state, "session", None) else None
    if not ws:
        return NOPROJ_NAME
    p = _projects().by_path(ws)
    return p["name"] if p else Path(ws).name


def move_project_choices() -> list[tuple[str, str]]:
    current = _current_chat_project()
    return [(t("Move chat to…"), "")] + [
        (value, value) for _label, value in project_choices() if value != current
    ]


def move_chat_to(project_name: str):
    """Přesuň AKTIVNÍ chat do vybraného projektu (nebo mimo projekty)."""
    try:
        if not project_name:
            return (gr.update(), gr.update(), gr.update(), gr.update(),
                    gr.update(value="", choices=move_project_choices()))
        s = state.session
        if s.transient:
            gr.Warning(t("Chat is not saved — send a message first."))
            return (gr.update(), gr.update(), gr.update(), gr.update(),
                    gr.update(value="", choices=move_project_choices()))
        if not project_name or project_name == NOPROJ_NAME:
            s.meta["workspace"] = None
            target = t("no project")
            target_workspace = None
        else:
            proj = next((p for p in _projects().list_all() if p["name"] == project_name), None)
            if not proj:
                gr.Warning(t("Project '{name}' not found.", name=project_name))
                return (gr.update(), gr.update(), gr.update(), gr.update(),
                        gr.update(value="", choices=move_project_choices()))
            s.meta["workspace"] = proj["path"]
            target = proj["name"]
            target_workspace = proj["path"]
        s._save_meta()
        if target_workspace:
            state.set_workspace(target_workspace, adopt_project_mode=False)
        else:
            state.clear_workspace()
        state._refresh_system_prompt()
        state.save_ui_state()
        gr.Info(t("📁 Chat moved → {target}", target=target))
        r1, r2, ds = update_chats_radio()
        return (r1, r2, ds,
                gr.update(choices=project_choices(), value=current_project_name()),
                gr.update(choices=move_project_choices(), value=""))
    except Exception as e:
        gr.Warning(f"❌ {e}")
        return (gr.update(), gr.update(), gr.update(), gr.update(),
                gr.update(value="", choices=move_project_choices()))


def open_in_editor(path: Path | str):
    """Otevři soubor ve výchozím editoru uživatele."""
    import os as _os
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch(exist_ok=True)
        _os.startfile(str(p))  # noqa: S606 - Windows default app
        return t("Opening: {path}", path=p)
    except Exception as e:
        return f"❌ {e}"


def skills_info_text() -> str:
    from harness.skills import SkillLibrary
    return SkillLibrary(cfg, Path(state.workspace) if state.workspace else None).catalog()


def open_skills_folder() -> None:
    import os as _os
    folder = cfg.root / cfg.data.get("skills", {}).get("user_directory", "user-skills")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        _os.startfile(str(folder))  # noqa: S606 - local Windows folder
        gr.Info(t("Opening skills folder: {folder}", folder=folder))
    except OSError as exc:
        gr.Warning(t("Skills folder cannot be opened: {error}", error=exc))


def open_user_manual(language: str) -> None:
    """Open the packaged user manual in the system PDF viewer."""
    import os as _os
    filename = ("QwenHarness-Manual-CS.pdf" if language == "cs"
                else "QwenHarness-Manual-EN.pdf")
    candidates = [
        cfg.root / "docs" / filename,
        cfg.root / "output" / "pdf" / filename,
    ]
    manual = next((path for path in candidates if path.is_file()), None)
    if manual is None:
        gr.Warning(t("Manual not found. Reinstall or repair the application."))
        return
    try:
        _os.startfile(str(manual))  # noqa: S606 - local packaged PDF
        gr.Info(t("Opening manual: {path}", path=manual))
    except OSError as exc:
        gr.Warning(t("Manual cannot be opened: {error}", error=exc))


# ------------------------------------------------------------- mazání chatů
_selected_sid: dict = {"id": None}


def _selected_info_text() -> str:
    sid = _selected_sid.get("id")
    if not sid:
        return t("<small>click a row in the table → selects the chat (nothing is loaded)</small>")
    row = next((s for s in _sessions_rows if s["id"] == sid), None)
    if not row:
        return t("<small>selected chat no longer exists</small>")
    proj = Path(row["workspace"]).name if row.get("workspace") else t("no project")
    return t("<small>📄 selected: <b>{title}</b> · {project} · {count} messages</small>",
             title=row["title"][:60], project=proj, count=row["messages"])


def select_row_handler(sel_evt, df_value):
    """Klik na řádek = POUZE VÝBĚR (načtení/mazání až tlačítky - nic se nenačte!)."""
    try:
        idx = sel_evt.index[0] if sel_evt and sel_evt.index is not None else None
        if idx is not None and idx < len(_sessions_rows):
            _selected_sid["id"] = _sessions_rows[idx]["id"]
    except Exception:
        pass
    return _selected_info_text()


def load_selected_session():
    """📂 Načti právě vybraný chat."""
    sid = _selected_sid.get("id")
    if not sid:
        gr.Warning(t("Click a chat row in the table first (selection)."))
        return
    yield from load_session_handler(sid)


def delete_selected_session():
    """🗑 Smaž vybraný chat (jde i aktuální - nahradí se novým prázdným)."""
    try:
        sid = _selected_sid.get("id")
        if not sid:
            gr.Warning(t("Click a chat row in the table first (selection)."))
            yield chat_view(), sessions_refresh(), _selected_info_text(), gr.update(visible=False), refresh_status()
            return
        if sid == state.session.id:
            state.new_session()  # otevřený chat nahraď novým, pak maž
        ok = Session.delete(cfg, sid)
        gr.Info(t("🗑 Chat deleted") if ok else t("Chat not found (already deleted?)"))
        _selected_sid["id"] = None
        yield chat_view(), sessions_refresh(), _selected_info_text(), gr.update(visible=False), refresh_status()
    except Exception as e:
        gr.Warning(f"❌ {e}")
        yield chat_view(), sessions_refresh(), _selected_info_text(), gr.update(visible=False), refresh_status()


def _mem_infos():
    """Info texty o souborech paměti (cesty k otevření)."""
    try:
        _project_del_arm.update(ts=0.0, path=None)
        store = _memory_paths()
        g = t("**Global for everything:** `{path}`", path=store.global_path)
        mode = t(WORK_MODES[state.work_mode].label)
        m = t("**For {mode} mode:** `{path}`", mode=mode, path=store.mode_path())
        p = store.project_path()
        p_txt = (t("**Project:** `{path}`", path=p) if p
                 else t("**Project:** — select a project first"))
    except Exception:
        g, m, p_txt = "—", "—", "—"
    return (f"<small>{g}</small>", f"<small>{m}</small>",
            f"<small>{p_txt}</small>")


def _mem_g_text() -> str:
    return _mem_infos()[0]


def _mem_p_text() -> str:
    return _mem_infos()[2]


def _mem_mode_text() -> str:
    return _mem_infos()[1]


def memory_info_updates():
    return tuple(gr.update(value=value) for value in _mem_infos())


def load_memory_global() -> str:
    try:
        return _memory_paths().read("global")
    except Exception:
        return ""


_PROJECT_MEM_HINTS = ("(set a workspace", "(nastav workspace")  # EN + starší české chatty


def load_memory_project() -> str:
    try:
        store = _memory_paths()
        if store.project_path() is None:
            return t("(set a workspace — project memory will bind to it)")
        return store.read("project")
    except Exception:
        return ""


def load_memory_mode() -> str:
    try:
        return _memory_paths().read("mode")
    except Exception:
        return ""


def save_memory_handler(global_text: str, mode_text: str, project_text: str):
    """Ulož tři vrstvy paměti a občerstvi system prompt."""
    try:
        store = _memory_paths()
        store.global_path.parent.mkdir(parents=True, exist_ok=True)
        store.global_path.write_text(global_text, encoding="utf-8")
        store.mode_path().write_text(mode_text, encoding="utf-8")
        pp = store.project_path()
        if pp is not None and not project_text.startswith(_PROJECT_MEM_HINTS):
            pp.write_text(project_text, encoding="utf-8")
        state._refresh_system_prompt()
        gr.Info(t("✅ Memory saved — the model will see it from the next message"))
        return gr.update(), gr.update(), gr.update()
    except Exception as e:
        gr.Warning(f"❌ {type(e).__name__}: {e}")
        return gr.update(), gr.update(), gr.update()


def refresh_status():
    """Status ve 3 řádcích: model / VRAM / tokeny."""
    switch = state.model_switch.snapshot()
    st = servermgmt.server_state(cfg)
    key = switch.target if switch.busy or switch.status == "failed" \
        else (servermgmt.running_model(cfg) or state.model_key)
    model = cfg.data["models"].get(key, {})
    model_name = str(model.get("status_label") or model.get("alias") or key)
    kv_name = "F16" if cfg.kv_cache_mode(key) == "f16" else "Q8"
    if switch.busy or st == "starting":
        line1 = t("⏳ Loading {model}…", model=model_name)
        line2 = t("🖥️ GPU VRAM: — · KV cache: {kv}", kv=kv_name)
    elif switch.status == "failed":
        line1 = t("❌ {model} — switch failed", model=model_name)
        line2 = f"<small>{switch.error}</small>"
    elif st == "running":
        line1 = t("🟢 {model}", model=model_name)
        line2 = t("🖥️ GPU VRAM: {vram} · KV cache: {kv}", vram=servermgmt.vram_value(), kv=kv_name)
    else:
        line1 = t("🔴 {model} — server is down", model=model_name)
        line2 = t("🖥️ GPU VRAM: — · KV cache: {kv}", kv=kv_name)
    pct = 0
    try:
        est = state.agent.estimate_context_tokens()
        limit = cfg.context_size(key)
        pct = min(100, est * 100 // max(limit, 1))
        warn = " 🔴" if pct >= 85 else (" 🟠" if pct >= 70 else "")
        live = _live_token_estimate() if state.run_active.is_set() else 0
        live_count = f"{live}" if live < 1000 else f"{live / 1000:.1f}k"
        live_text = t(" · generating live: ~{count} tokens", count=live_count) if live else ""
        line3 = t("📊 Chat context: ~{used}k / {limit}k tokens{live}{warn}",
                  used=f"{est / 1000:.1f}", limit=limit // 1000, live=live_text, warn=warn)
    except Exception:
        line3 = t("📊 Chat context: —")
    _check_ctx_warning(pct)
    return f"{line1}<br>{line2}<br>{line3}"


def refresh_runtime_controls():
    switch = state.model_switch.snapshot()
    update_args = {"interactive": not switch.busy}
    if switch.status == "failed":
        update_args["value"] = state.model_key
    key = switch.target if switch.busy and switch.target else state.model_key
    return (refresh_status(), gr.update(**update_args),
            kv_cache_control_update(key, busy=switch.busy))


def _autostart_server_thread() -> None:
    """Launcher nastaví QWEN_AUTOSTART_SERVER=1 → model se nahodí na pozadí,
    UI zobrazuje ⏳ stav (UI first, model second)."""
    if servermgmt.server_state(cfg) != "down":
        return
    print("[AUTOSTART] starting llama-server in the background ...", flush=True)
    state.model_switch.request(state.model_key, on_success=_model_switch_succeeded)


# ------------------------------------------------------------- workspace
WS_JUNK = {".git", "node_modules", ".venv", "venv", "__pycache__", ".idea", ".vscode", "dist", "build"}

# PowerShell FolderBrowserDialog (fallback, když by chyběl tkinter).
# -STA je nutné pro Windows Forms dialogy; topmost owner drží dialog nad prohlížečem.
_PS_FOLDER_DIALOG = r"""
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true; $owner.ShowInTaskbar = $false
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = '{desc}'
if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
    Write-Output $d.SelectedPath
}
"""

_PS_FILE_DIALOG = r"""
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true; $owner.ShowInTaskbar = $false
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = '{title}'
$d.Filter = 'Text and source files|*.md;*.txt;*.rst;*.py;*.js;*.ts;*.json;*.yaml;*.yml;*.toml;*.html;*.css;*.rs;*.go;*.java;*.cs;*.cpp;*.h|All files|*.*'
if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
    Write-Output $d.FileName
}
"""


def pick_directory_dialog() -> str | None:
    """Nativní Windows dialog pro výběr složky (na stroji, kde běží webapp = localhost).

    1) tkinter askdirectory (nativní, rychlé)
    2) fallback: PowerShell FolderBrowserDialog
    Vrací vybranou cestu nebo None (zrušeno / nedostupné).
    """
    # 1) tkinter
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)  # dialog nad oknem prohlížeče
        try:
            path = filedialog.askdirectory(title=t("Select a project folder (workspace)"))
        finally:
            root.destroy()
        if path:
            return path
    except Exception:
        pass
    # 2) PowerShell fallback
    try:
        import subprocess
        script = _PS_FOLDER_DIALOG.format(desc=t("Select a project folder (workspace)"))
        out = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True, text=True, timeout=600,
            creationflags=0x08000000,  # bez blikání černého okna
        )
        p = (out.stdout or "").strip().strip('"')
        if p and Path(p).is_dir():
            return p
    except Exception:
        pass
    return None


def pick_context_file_dialog() -> str | None:
    initial = state.workspace or str(Path.home())
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askopenfilename(
            parent=root, initialdir=initial,
            title=t("Select a text file to pin into the context"),
            filetypes=[
                (t("Text and source files"),
                 "*.md *.txt *.rst *.py *.js *.ts *.json *.yaml *.yml *.toml *.html *.css"),
                (t("All files"), "*.*"),
            ],
        )
        root.destroy()
        return selected or None
    except Exception:
        pass
    try:
        script = _PS_FILE_DIALOG.format(title=t("Select a text file to pin into the context"))
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        selected = proc.stdout.strip()
        return selected or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def pin_context_file_dialog():
    selected = pick_context_file_dialog()
    if not selected:
        return context_inspector_text(), gr.update(
            interactive=bool(state.session.meta.get("pinned_files")))
    path = Path(selected)
    try:
        if not path.is_file():
            raise FileNotFoundError(path)
        added = state.session.pin_context_file(path)
        if added:
            gr.Info(t("Pinned to this chat: {name}", name=path.name))
        else:
            gr.Info(t("File is already pinned: {name}", name=path.name))
    except OSError as exc:
        gr.Warning(t("Failed to pin the file: {error}", error=exc))
    return context_inspector_text(), gr.update(
        interactive=bool(state.session.meta.get("pinned_files")))


def set_workspace_handler(path: str):
    """Nastaví workspace (z dropdownu/ručního zadání). Feedback jako toast."""
    try:
        p = state.set_workspace(path)
        gr.Info(t("✅ Workspace: {path}", path=p))
        return gr.update(choices=state.recent_ws, value=str(p))
    except ValueError as e:
        gr.Warning(f"❌ {e}")
        return gr.update()
    except Exception as e:
        gr.Warning(f"❌ {type(e).__name__}: {e}")
        return gr.update()


def browse_workspace():
    """Nativní dialog → nastav workspace. Feedback jako toast."""
    path = pick_directory_dialog()
    if not path:
        return gr.update()
    return set_workspace_handler(path)


def workspace_header() -> str:
    """Text s aktuálním workspace (používáno ve zpětné vazbě / debugu)."""
    if not state.workspace:
        return t("📁 Workspace: not set")
    return t("📁 Workspace: {path}", path=state.workspace)


def server_cmd(cmd: str):
    if cmd == "start":
        return change_model(state.model_key)
    if cmd == "stop":
        servermgmt.stop(cfg, quiet=True)
        state.model_switch.reset()
    if cmd == "restart":
        if not state.model_switch.request(
                state.model_key, restart=True, on_success=_model_switch_succeeded):
            gr.Info(t("A model is already loading; restart cannot start right now."))
    return refresh_runtime_controls()


def task_changes_text() -> str:
    journal = getattr(state.agent.ctx, "changes", None)
    summary = journal.summary() if journal else {"file_count": 0, "files": []}
    changed = [item for item in summary.get("files", []) if item.get("changed")]
    if not changed:
        return t("<small>No files changed in the current task yet.</small>")
    lines = [t("**Changes in this task: {count}**", count=len(changed))]
    for item in changed:
        action = t("Created") if item["change"] == "created" else t("Modified")
        lines.append(f"- {action}: `{item['path']}`")
    return "\n".join(lines)


def refresh_task_changes():
    journal = getattr(state.agent.ctx, "changes", None)
    summary = journal.summary() if journal else {"files": []}
    has_changes = any(item.get("changed") for item in summary.get("files", []))
    return task_changes_text(), gr.update(interactive=has_changes)


def undo_current_task():
    journal = getattr(state.agent.ctx, "changes", None)
    if journal is None:
        gr.Warning(t("Restore point is not available."))
        return refresh_task_changes()
    result = journal.undo()
    if result.get("errors"):
        gr.Warning(t("Some files could not be restored: {errors}",
                     errors="; ".join(result["errors"])))
    elif result.get("restored"):
        gr.Info(t("Restored {count} files to their pre-task state.", count=len(result["restored"])))
    else:
        gr.Info(t("No changes to revert in this task."))
    return refresh_task_changes()


def active_processes_text() -> str:
    processes = state.processes.list()
    running = [item for item in processes if item["status"] == "running"]
    if not running:
        return t("<small>No long-running operation is running right now.</small>")
    lines = [t("**Running operations: {count}**", count=len(running))]
    for item in running:
        command = item["command"].replace("\n", " ")
        if len(command) > 80:
            command = command[:77] + "…"
        lines.append(f"- `{command}` · {item['elapsed_seconds']:.0f} s")
    return "\n".join(lines)


def refresh_processes():
    running = any(item["status"] == "running" for item in state.processes.list())
    return active_processes_text(), gr.update(interactive=running)


def stop_all_processes():
    stopped = state.processes.terminate_all()
    if stopped:
        gr.Info(t("Stopped long-running operations: {count}", count=len(stopped)))
    else:
        gr.Info(t("No long-running operation is running right now."))
    return refresh_processes()


def context_inspector_text() -> str:
    info = state.session.context_breakdown()
    limit = cfg.context_size(state.model_key)
    tokens = state.agent.estimate_context_tokens()
    pct = min(100, tokens * 100 // max(1, limit))
    lines = [
        t("**Context: ~{used}k / {limit}k tokens ({pct}%)**",
          used=f"{tokens / 1000:.1f}", limit=limit // 1000, pct=pct),
        t("- The model sees {visible} of {total} messages",
          visible=info["visible_messages"], total=info["total_messages"]),
        t("- Images in the active context: {count}", count=info["images"]),
        t("- Older history: compressed") if info["compressed"] else t("- Older history: full"),
    ]
    pins = info.get("pinned_files") or []
    if pins:
        lines.append(t("- Pinned files: {count}", count=len(pins)))
        lines.extend(f"  - `{Path(path).name}`" for path in pins)
    else:
        lines.append(t("- Pinned files: none"))
    return "\n".join(lines)


def clear_pinned_context():
    pins = list(state.session.meta.get("pinned_files") or [])
    for path in pins:
        state.session.unpin_context_file(Path(path))
    if pins:
        gr.Info(t("Unpinned files: {count}", count=len(pins)))
    return context_inspector_text(), gr.update(interactive=False)


def refresh_context_inspector():
    has_pins = bool(state.session.meta.get("pinned_files"))
    return context_inspector_text(), gr.update(interactive=has_pins)


def research_status_text() -> str:
    if state.work_mode != "research":
        return t("<small>The research ledger activates in Research mode.</small>")
    ledger = getattr(state.agent.ctx, "research", None)
    status = ledger.status() if ledger else {"active": False}
    if not status.get("active"):
        return t("<small>Research starts after you send a question.</small>")
    phase = {"collecting": t("collecting sources"),
             "complete": t("synthesis complete")}.get(
        status.get("status"), status.get("status", t("waiting")))
    return (f"{t('**Research: {phase}**', phase=phase)}\n"
            f"{t('- Search queries: {count}', count=status.get('queries', 0))}\n"
            f"{t('- Links found: {count}', count=status.get('candidates', 0))}\n"
            f"{t('- Sources read: {count}', count=status.get('sources', 0))}\n"
            f"{t('- Sources are not filtered or ranked by origin')}")


def export_research_ledger():
    ledger = getattr(state.agent.ctx, "research", None)
    if ledger is None or not ledger.path.is_file():
        gr.Warning(t("The current chat has no research ledger yet."))
        return gr.update()
    return gr.update(value=str(ledger.path), visible=True)


def export_research_synthesis(fmt: str):
    from harness.documents import export_document
    ledger = getattr(state.agent.ctx, "research", None)
    run = ledger.current() if ledger else None
    synthesis = run.get("synthesis") if run else None
    if not synthesis:
        gr.Warning(t("The current research has no completed synthesis yet."))
        return gr.update()
    output_dir = Path(state.workspace) / "exports" if state.workspace else state.session.dir / "exports"
    title = str(run.get("question") or t("Research synthesis"))
    target = export_document(synthesis, output_dir, "research-synthesis", fmt, title)
    return gr.update(value=str(target), visible=True)


def _clear_inputs():
    """Vyčisti vstupní pole a upload po odeslání."""
    return gr.update(value=""), gr.update(value=None)


# ------------------------------------------------------------- jazyk UI
# reload smí používat jen samostatný proces (launcher spouští webapp.py);
# qwen_app.py běží in-process → tam stačí hint k restartu
RELOAD_ENABLED = True
_ACTIVE_DEMO: gr.Blocks | None = None


def request_ui_reload() -> None:
    """Zavři aktuální Blocks - obslužná smyčka v __main__ je přestaví v novém jazyce."""
    state.ui_reload.set()
    demo = _ACTIVE_DEMO
    if demo is not None:
        threading.Timer(1.0, _close_demo, args=(demo,)).start()


def _close_demo(demo) -> None:
    try:
        demo.close()
    except Exception:
        pass


def change_language(value: str):
    """Přepni jazyk UI: dynamické texty hned, statické popisky po reloadu."""
    state.language = set_language(value)
    state.save_ui_state()
    if RELOAD_ENABLED:
        gr.Info(t("Language changed — reloading the interface…"))
        request_ui_reload()
    else:
        gr.Info(t("Language saved — restart the app to apply it fully."))


# ------------------------------------------------------------- UI
CUSTOM_CSS = """
/* === PROFESIONÁLNÍ DARK THEME (gradio .dark + akcenty) == */
html, body, gradio-app, .gradio-container {
  background: #0b0e14 !important;
}
body { background: #0b0e14 !important; }
.gradio-container {
  width: 100% !important;
  max-width: 1600px !important; margin: 0 auto !important;
  font-family: 'Segoe UI Variable Text','Segoe UI','Segoe UI Emoji','Segoe UI Symbol','Noto Color Emoji',system-ui,sans-serif !important;
  color-scheme: dark !important;
}
/* povrchy do tmavé škály */
.dark, .gradio-container.dark { color-scheme: dark !important; }
.form, .gap, .block, [data-testid="group"] { background: transparent !important; }
.panel, .grp, .form { border-color: #21262d !important; }

/* tlačítka */
button {
  border-radius: 10px !important; border: 1px solid #30363d !important;
  background: #21262d !important; color: #e6edf3 !important;
  transition: all .15s ease !important; font-weight: 500 !important;
}
button:hover { background: #2d333b !important; border-color: #8b949e !important; transform: translateY(-1px); }
button.primary, .primary-wrap button {
  background: linear-gradient(135deg,#0d9488,#0ea5e9) !important;
  border: none !important; color: #fff !important;
  box-shadow: 0 2px 12px rgba(13,148,136,.3) !important;
}
button.primary:hover { filter: brightness(1.12) !important; }

/* čtvercová tlačítka: ikona nad textem */
.sqbtn button, button.sqbtn {
  min-width: 72px !important; height: 56px !important; padding: 5px 4px !important;
  display: flex !important; flex-direction: column !important; align-items: center !important;
  justify-content: center !important; gap: 3px !important; font-size: 10px !important;
  text-transform: uppercase !important; letter-spacing: .05em !important; border-radius: 12px !important;
}
.sqbtn button .icon { display: none !important; }
#btn-start button::before { content:"▶"; font-size:16px; }
#btn-stop-srv button::before { content:"⏹"; font-size:16px; }
#btn-refresh button::before { content:"⟳"; font-size:16px; }
#btn-compress button::before { content:"🗜"; font-size:16px; }
#btn-handoff button::before { content:"📦"; font-size:16px; }
#btn-new button::before { content:"✚"; font-size:16px; }
#btn-proj-new button::before { content:"📁✚"; font-size:14px; }
#btn-proj-attach button::before { content:"📂"; font-size:16px; }

/* chat - inverzní: user vpravo (modrý), asistent vlevo (tmavý) */
#main-chat { height: calc(100vh - 248px) !important; min-height: 340px !important; border-radius: 12px !important; }
#main-chat .user-row, #main-chat [class*="user"] { justify-content: flex-end !important; }
#main-chat .bot-row, #main-chat [class*="bot"] { justify-content: flex-start !important; }
#main-chat .message { border-radius: 14px !important; padding: 10px 14px !important; }
#main-chat .user-row .message, #main-chat .message-user {
  background: #1d4ed8 !important; color: #f0f6ff !important;
  border: 1px solid #3b82f6 !important;
}
#main-chat .bot-row .message, #main-chat .message-bot,
#main-chat .message-assistant {
  background: #161b22 !important; color: #e6edf3 !important;
  border: 1px solid #30363d !important;
}
/* vstup */
#composer-layout { align-items: stretch !important; flex-wrap: nowrap !important; gap: 8px !important; }
#prompt-column, #composer-side { gap: 6px !important; min-width: 0 !important; }
#composer-side { flex: 0 0 150px !important; max-width: 150px !important; }
#msg-in textarea { min-height: 72px !important; max-height: 132px !important; border-radius: 8px !important; }
#prompt-actions { width: 100% !important; gap: 6px !important; }
#prompt-actions button { min-height: 32px !important; height: 32px !important;
  padding: 4px 8px !important; border-radius: 6px !important; font-size: 12px !important; }
.prompt-action { min-width: 0 !important; flex: 1 1 0 !important; width: 0 !important; }
#composer-side button { min-height: 32px !important; height: 32px !important;
  padding: 4px 7px !important; border-radius: 6px !important; font-size: 12px !important; }
#files-in { height: 42px !important; min-height: 42px !important; max-height: 42px !important;
  overflow: hidden !important; border: 1px dashed #3c5162 !important;
  border-radius: 6px !important; background: #0e151d !important; }
#files-in > div, #files-in .wrap, #files-in [data-testid="file"] {
  min-height: 38px !important; height: 38px !important; padding: 2px 5px !important;
  font-size: 11px !important; overflow: hidden !important; }
#files-in .or, #files-in [class*="or"] { display: none !important; }
#files-in button { min-height: 28px !important; height: 28px !important; padding: 2px 5px !important;
  font-size: 0 !important; color: transparent !important; }
#files-in button * { display: none !important; }
#files-in button::after { content: "＋"; font-size: 13px !important;
  color: #c9d7e3 !important; font-weight: 600 !important; }
@media (max-width: 760px) {
  #composer-side { flex-basis: 132px !important; max-width: 132px !important; }
  #prompt-actions button { font-size: 11px !important; padding: 3px 5px !important; }
}
/* historie - tabulka */
#sessions-df table { font-size: 0.92em !important; }
#sessions-df tr:hover td { background: #1c2430 !important; }
/* drobnosti */
.hdr p { margin: 0 !important; font-size: 0.9em !important; }
.gap { gap: 6px !important; }
#status-pill { border: 1px solid #30363d !important; border-radius: 10px !important;
  padding: 6px 12px !important; line-height: 1.45 !important; background: #0d1117 !important; }
#status-pill p { margin: 0 !important; font-size: 12.5px !important; }
/* scrollbar */
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-thumb { background: #30363d !important; border-radius: 6px; }
::-webkit-scrollbar-track { background: transparent !important; }
/* ===== LAYOUT: sidebar + hlavní chat (styl ZCode/Codex) ===== */
#app-row { gap: 10px !important; align-items: stretch !important; flex-wrap: nowrap !important; }
#sidebar > * { flex-shrink: 0 !important; }
#sidebar {
  min-width: 332px !important; max-width: 332px !important;
  background: #10141b !important; border: 1px solid #21262d !important;
  border-radius: 14px !important; padding: 14px 12px !important;
  height: calc(100vh - 40px) !important; overflow-y: auto !important;
  flex-wrap: nowrap !important;   /* jinak Gradio balí přebytecne potomky do sloupcu vedle sebe */
}
#main { flex: 1 1 0 !important; min-width: 0 !important; }
#main-chat { width: 100% !important; max-width: 100% !important; }
#main-chat img { max-width: 100% !important; height: auto !important; }
.side-title { margin-bottom: 2px !important; }
.app-version { color: #8b949e; font-size: 12px; font-weight: 500; }
.side-h { color: #2dd4bf !important; font-weight: 700 !important;
  letter-spacing: .08em !important; margin: 14px 0 4px 2px !important; display: block; }
.sqsm { min-height: 34px !important; font-size: 12px !important; border-radius: 9px !important; }
#sidebar button { min-height: 32px !important; padding: 5px 9px !important;
  border-radius: 6px !important; font-size: 12px !important; }
.compact-btn button, button.compact-btn { min-height: 30px !important; height: 30px !important;
  padding: 3px 8px !important; border-radius: 5px !important; font-weight: 600 !important; }
.compact-btn { min-width: 0 !important; flex: 1 1 0 !important; }
#server-control-bar { background: #151a21 !important; border: 1px solid #303844 !important;
  border-radius: 7px !important; padding: 7px !important; margin-bottom: 4px !important; }
#server-control-bar #server-control-bar { background: transparent !important; border: 0 !important;
  border-radius: 0 !important; padding: 0 !important; margin: 0 !important; }
#server-control-bar .styler { background: transparent !important; }
.server-panel-title p { color: #2dd4bf !important; font-size: 10px !important;
  font-weight: 700 !important; letter-spacing: .08em !important; margin: 0 0 4px 1px !important; }
.server-start, .server-stop, .server-restart { min-width: 0 !important;
  flex: 1 1 0 !important; width: 0 !important; }
#server-control-bar .gap { flex-wrap: nowrap !important; width: 100% !important; }
.server-start button, button.server-start, .chat-new button, button.chat-new,
.soft-positive button, button.soft-positive {
  background: #123523 !important; border-color: #1f6b46 !important; color: #baf7d2 !important;
}
.server-start button:hover, .chat-new button:hover, .soft-positive button:hover {
  background: #17472e !important; border-color: #2d8a5e !important;
}
.server-stop button, button.server-stop, .chat-delete button, button.chat-delete,
.soft-danger button, button.soft-danger {
  background: #3a1d22 !important; border-color: #7b3540 !important; color: #ffc7ce !important;
}
.server-stop button:hover, .chat-delete button:hover, .soft-danger button:hover {
  background: #51252d !important; border-color: #a14552 !important;
}
.server-restart button, button.server-restart {
  background: #123346 !important; border-color: #27617e !important; color: #c9ebff !important;
}
.server-restart button:hover { background: #17455e !important; border-color: #347da1 !important; }
.sidebar-section { border-radius: 7px !important; margin: 3px 0 !important; overflow: hidden !important; }
.sidebar-section.info-section { border-color: #293442 !important; background: #101720 !important; }
.sidebar-section.action-section { border-color: #3b3831 !important; background: #171714 !important; }
.sidebar-section.settings-section { border-color: #2f3d39 !important; background: #111a18 !important; }
.sidebar-section.attention-section { border-color: #66532b !important; background: #211c11 !important; }
#active-chat-panel { background: #141a22 !important; border: 1px solid #354453 !important;
  border-left: 3px solid #2dd4bf !important; border-radius: 7px !important;
  padding: 8px !important; margin-top: 10px !important; gap: 6px !important; }
#active-chat-panel #active-chat-panel { background: transparent !important; border: 0 !important;
  border-left: 0 !important; border-radius: 0 !important; padding: 0 !important;
  margin: 0 !important; gap: 6px !important; }
#active-chat-panel .styler { background: transparent !important; }
#active-chat-panel > .gap { flex-wrap: nowrap !important; width: 100% !important; }
#active-chat-panel .chat-new, #active-chat-panel .chat-delete {
  min-width: 0 !important; flex: 1 1 0 !important; width: 0 !important; }
#rename-chat-input, #move-dd { background: #0d1219 !important; border: 1px solid #35404d !important;
  border-radius: 6px !important; min-height: 34px !important; }
#rename-chat-input textarea, #rename-chat-input input { font-size: 12.5px !important; }
#proj-dd { background: #0d1219 !important; border: 1px solid #35404d !important;
  border-radius: 6px !important; }
#del-state p { color: #f87171 !important; font-size: 12px !important; margin: 2px 0 0 4px !important; }
#chats-radio label, #noproj-radio label { padding: 5px 8px !important; border-radius: 8px !important;
  font-size: 12.5px !important; }
#chats-radio label:hover, #noproj-radio label:hover { background: #1c2430 !important; }
#chats-radio label.selected, #noproj-radio label.selected { background: #14323c !important; border: 1px solid #2dd4bf55 !important; }
#main-chat { height: calc(100vh - 234px) !important; min-height: 340px !important; border-radius: 12px !important; }
#footer-hint { margin-top: 4px !important; }
/* blikající kurzor */
@keyframes qwen-blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }
.blink-cursor { animation: qwen-blink 1s step-end infinite; }
"""


# Nový build_ui - layout ve stylu ZCode/Codex: levý sidebar + hlavní chat
def build_ui() -> gr.Blocks:
    model_choices = [
        (str(model.get("status_label") or key), key)
        for key, model in cfg.data["models"].items()
    ]
    with gr.Blocks(title=f"Qwen3.8-27B Harness v{APP_VERSION}") as ui:
        with gr.Row(elem_id="app-row", elem_classes=["gap"]):
            # ================= LEVÝ SIDEBAR =================
            with gr.Column(scale=0, elem_id="sidebar"):
                gr.Markdown(
                    f"## 🤖 <span style='color:#2dd4bf'>Qwen</span>3.8 "
                    f"<small class='app-version'>v{APP_VERSION}</small>",
                            elem_classes=["hdr", "side-title"])
                status_box = gr.Markdown(refresh_status, elem_id="status-pill")
                work_mode_dd = gr.Dropdown(
                    choices=[(t(spec.label), spec.id) for spec in WORK_MODES.values()],
                    value=state.work_mode,
                    label=t("Work mode"),
                )

                gr.Markdown(f"<small class='side-h'>{t('⚙ FEATURES')}</small>", elem_classes=["hdr"])
                with gr.Group(elem_id="server-control-bar"):
                    gr.Markdown("SERVER", elem_classes=["server-panel-title"])
                    with gr.Row(elem_classes=["gap"]):
                        btn_start = gr.Button(
                            "start", size="sm", scale=1,
                            elem_classes=["compact-btn", "server-start"])
                        btn_stop = gr.Button(
                            "stop", size="sm", scale=1,
                            elem_classes=["compact-btn", "server-stop"])
                        btn_refresh = gr.Button(
                            "restart", size="sm", scale=1,
                            elem_classes=["compact-btn", "server-restart"])

                with gr.Accordion(
                        t("Context & handoff"), open=False,
                        elem_classes=["sidebar-section", "action-section"]):
                    with gr.Row(elem_classes=["gap"]):
                        btn_compress = gr.Button(
                            t("Compress"), size="sm", scale=1,
                            elem_classes=["compact-btn"])
                        btn_handoff = gr.Button(
                            t("Hand off"), size="sm", scale=1,
                            elem_classes=["compact-btn"])

                with gr.Accordion(
                        t("Changes in this task"), open=False,
                        visible=state.work_mode in ("writing", "development", "computer"),
                        elem_classes=["sidebar-section", "info-section"]) as changes_panel:
                    task_changes = gr.Markdown(task_changes_text, elem_classes=["hdr"])
                    btn_undo_task = gr.Button(
                        t("Revert task changes"), size="sm", variant="stop",
                        interactive=False,
                    )

                with gr.Accordion(
                        t("Unfinished task"), open=True,
                        visible=state.agent.has_resumable_task,
                        elem_classes=["sidebar-section", "attention-section"]) as resumable_panel:
                    resumable_status = gr.Markdown(resumable_task_text, elem_classes=["hdr"])
                    btn_resume_task = gr.Button(
                        t("Continue task"), size="sm",
                        interactive=state.agent.has_resumable_task,
                    )

                with gr.Accordion(
                        t("Long-running operations"), open=False,
                        visible=state.work_mode in ("development", "computer"),
                        elem_classes=["sidebar-section", "info-section"]) as process_panel:
                    process_status = gr.Markdown(active_processes_text, elem_classes=["hdr"])
                    btn_stop_processes = gr.Button(
                        t("Stop running operations"), size="sm", variant="stop",
                        interactive=False,
                    )

                with gr.Accordion(
                        t("What the model currently sees"), open=False,
                        elem_classes=["sidebar-section", "info-section"]):
                    context_info = gr.Markdown(context_inspector_text, elem_classes=["hdr"])
                    with gr.Row(elem_classes=["gap"]):
                        btn_pin_file = gr.Button(t("Pin file"), size="sm", scale=1)
                        btn_clear_pins = gr.Button(
                            t("Unpin all"), size="sm", scale=1,
                            interactive=bool(state.session.meta.get("pinned_files")),
                        )

                with gr.Accordion(
                        t("Available skills"), open=False,
                        elem_classes=["sidebar-section", "info-section"]):
                    skills_info = gr.Markdown(skills_info_text, elem_classes=["hdr"])
                    btn_open_skills = gr.Button(t("Open skills folder"), size="sm")

                with gr.Accordion(
                        t("Help & manuals"), open=False,
                        elem_classes=["sidebar-section", "info-section"]):
                    with gr.Row(elem_classes=["gap"]):
                        btn_manual_en = gr.Button(
                            t("English manual (PDF)"), size="sm", scale=1)
                        btn_manual_cs = gr.Button(
                            t("Czech manual (PDF)"), size="sm", scale=1)

                with gr.Accordion(
                        t("Research progress"), open=False,
                        visible=state.work_mode == "research",
                        elem_classes=["sidebar-section", "info-section"]) as research_panel:
                    research_status = gr.Markdown(research_status_text, elem_classes=["hdr"])
                    btn_export_research = gr.Button(t("Export all sources"), size="sm")
                    with gr.Row(elem_classes=["gap"]):
                        btn_export_research_docx = gr.Button(t("Synthesis DOCX"), size="sm")
                        btn_export_research_pdf = gr.Button(t("Synthesis PDF"), size="sm")
                    research_export_file = gr.File(
                        label="Research ledger", visible=False, interactive=False)

                with gr.Accordion(
                        t("⚙️ Settings"), open=False,
                        elem_classes=["sidebar-section", "settings-section"]):
                    model_dd = gr.Dropdown(
                        model_choices, value=state.model_key, label=t("Model"),
                        interactive=not state.model_switch.snapshot().busy,
                    )
                    kv_cache_dd = gr.Dropdown(
                        choices=kv_cache_choices(state.model_key),
                        value=cfg.kv_cache_mode(state.model_key),
                        label=t("KV cache precision"),
                        interactive=(len(kv_cache_choices(state.model_key)) > 1
                                     and not state.model_switch.snapshot().busy),
                    )
                    autonomy_dd = gr.Dropdown(["supervised", "semi", "auto"], value=state.autonomy,
                                              label=t("Autonomy"))
                    thinking_dd = gr.Dropdown(["xhigh", "medium", "low", "off"],
                                              value=("off" if not state.thinking else state.reasoning_effort),
                                              label=t("Thinking"))
                    lang_dd = gr.Dropdown(
                        choices=language_choices(), value=get_language(),
                        label=t("Language"))
                    settings_info = gr.Markdown("")
                    gr.Markdown(f"<small class='side-h'>{t('🧠 MEMORY')}</small>", elem_classes=["hdr"])
                    gr.Markdown(t("The model reads memory on every task and after compression; "
                                  "it stores facts on request (“remember…”)."),
                                elem_classes=["hdr"])
                    mem_g_info = gr.Markdown(_mem_g_text(), elem_classes=["hdr"])
                    btn_mem_g = gr.Button(t("Global memory — open"), size="sm")
                    mem_mode_info = gr.Markdown(_mem_mode_text(), elem_classes=["hdr"])
                    btn_mem_mode = gr.Button(t("Mode memory — open"), size="sm")
                    mem_p_info = gr.Markdown(_mem_p_text(), elem_classes=["hdr"])
                    btn_mem_p = gr.Button(t("Project memory — open"), size="sm")

                gr.Markdown(f"<small class='side-h'>{t('📁 PROJECTS')}</small>", elem_classes=["hdr"])
                proj_dd = gr.Dropdown(choices=project_choices(), value=current_project_name(),
                                      interactive=True, show_label=False, container=False,
                                      elem_id="proj-dd", info=None)
                with gr.Accordion(
                        t("Project management"), open=False,
                        elem_classes=["sidebar-section", "action-section"]):
                    with gr.Row(elem_classes=["gap"]):
                        btn_proj_new = gr.Button(
                            t("New"), size="sm", scale=1,
                            elem_classes=["compact-btn", "soft-positive"])
                        btn_proj_attach = gr.Button(
                            t("Attach"), size="sm", scale=1,
                            elem_classes=["compact-btn"])
                    btn_proj_delete = gr.Button(
                        t("Delete project + folder"), size="sm", variant="stop",
                        elem_classes=["compact-btn", "soft-danger"])
                    proj_delete_state = gr.Markdown("", elem_classes=["hdr"])
                    with gr.Row(visible=False) as proj_new_row:
                        proj_new_tb = gr.Textbox(
                            placeholder=t("new project name…"),
                            show_label=False, container=False, scale=3)
                        btn_proj_create = gr.Button(
                            "OK", variant="primary", size="sm", scale=1,
                            elem_classes=["compact-btn"])

                gr.Markdown(f"<small class='side-h'>{t('💬 PROJECT CHATS')}</small>", elem_classes=["hdr"])
                chats_radio = gr.Radio(choices=chat_choices(), value=state.session.id,
                                       show_label=False, container=False, elem_id="chats-radio",
                                       info=None)

                gr.Markdown(f"<small class='side-h'>{t('💬 CHATS WITHOUT PROJECT')}</small>", elem_classes=["hdr"])
                noproj_radio = gr.Radio(choices=noproj_chat_choices(), value=None,
                                        show_label=False, container=False, elem_id="noproj-radio",
                                        info=None)

                with gr.Accordion(t("Search all chats"), open=False):
                    history_query = gr.Textbox(
                        placeholder=t("word or phrase…"), show_label=False,
                        container=False,
                    )
                    btn_history_search = gr.Button(t("Search"), size="sm")
                    history_results = gr.Radio(choices=[], show_label=False, container=False)

                with gr.Group(elem_id="active-chat-panel"):
                    with gr.Row(elem_classes=["gap"]):
                        btn_new = gr.Button(
                            t("New chat"), size="sm", scale=1,
                            elem_classes=["compact-btn", "chat-new"])
                        btn_del_chat = gr.Button(
                            t("Delete"), size="sm", scale=1,
                            elem_classes=["compact-btn", "chat-delete"])
                    del_state = gr.Markdown("", elem_id="del-state", elem_classes=["hdr"])
                    rename_tb = gr.Textbox(
                        placeholder=t("Rename and press Enter…"),
                        show_label=False, container=False,
                        elem_id="rename-chat-input")
                    move_dd = gr.Dropdown(
                        choices=move_project_choices(), value="",
                        show_label=False, container=False,
                        elem_id="move-dd", info=None)

                    with gr.Accordion(
                            t("Export / import"), open=False,
                            elem_classes=["sidebar-section", "info-section"]):
                        with gr.Row(elem_classes=["gap"]):
                            btn_export_md = gr.Button(
                                "Markdown", size="sm", elem_classes=["compact-btn"])
                            btn_export_jsonl = gr.Button(
                                "JSONL", size="sm", elem_classes=["compact-btn"])
                        export_file = gr.File(label=t("Prepared export"), visible=False,
                                              interactive=False)
                        import_file = gr.File(label=t("Import JSONL"), file_types=[".jsonl"],
                                              type="filepath")
                        btn_import_chat = gr.Button(
                            t("Import as new chat"), size="sm",
                            elem_classes=["compact-btn"])

            # ================= HLAVNÍ CHAT =================
            with gr.Column(scale=5, elem_id="main"):
                chat = gr.Chatbot(value=chat_view(), show_label=False, height=560,
                                  render_markdown=True, elem_id="main-chat")
                with gr.Row(elem_id="composer-layout", elem_classes=["gap"]):
                    with gr.Column(scale=6, min_width=0, elem_id="prompt-column"):
                        msg_in = gr.Textbox(
                            placeholder=t("Type a message…  (Enter / Ctrl+Enter = send, Shift+Enter = new line)"),
                            show_label=False, container=False, lines=1, max_lines=8,
                            elem_id="msg-in")
                        with gr.Row(elem_id="prompt-actions", elem_classes=["gap"]):
                            btn_retry = gr.Button(
                                t("Retry"), size="sm", scale=1, elem_classes=["prompt-action"])
                            btn_undo_round = gr.Button(
                                t("Undo"), size="sm", scale=1, elem_classes=["prompt-action"])
                            btn_fork = gr.Button(
                                t("Fork"), size="sm", scale=1, elem_classes=["prompt-action"])
                    with gr.Column(scale=1, min_width=150, elem_id="composer-side"):
                        with gr.Row(elem_classes=["gap"]):
                            btn_send = gr.Button(
                                t("Send"), variant="primary", size="sm", scale=1,
                                elem_id="btn-send")
                            btn_stop_run = gr.Button("Stop", size="sm", scale=1)
                        files_in = gr.File(
                            label=None, show_label=False, container=False,
                            file_count="multiple", file_types=["image"], type="filepath",
                            elem_id="files-in")
                submission_state = gr.State({})
                with gr.Row(visible=False) as confirm_row:
                    gr.Markdown(t("⚠️ **Agent is waiting for action confirmation**"), scale=3)
                    btn_yes = gr.Button(t("Allow"), variant="primary", size="sm", scale=1)
                    btn_no = gr.Button(t("Deny"), variant="stop", size="sm", scale=1)

        # ---------------- události ----------------
        # projekty
        proj_dd.change(set_project_handler, proj_dd, proj_dd, queue=True,
                       concurrency_id="chat-run", concurrency_limit=1)\
            .then(update_chats_radio, None, [chats_radio, noproj_radio, del_state], queue=False)\
            .then(lambda: gr.update(value=state.work_mode), None, work_mode_dd, queue=False)\
            .then(work_mode_panel_updates, None,
                  [changes_panel, process_panel, research_panel], queue=False)\
            .then(memory_info_updates, None,
                  [mem_g_info, mem_mode_info, mem_p_info], queue=False)
        btn_proj_attach.click(attach_project_handler, None,
                              [proj_dd, proj_new_row], queue=True,
                              concurrency_id="chat-run", concurrency_limit=1)\
            .then(update_chats_radio, None, [chats_radio, noproj_radio, del_state], queue=False)\
            .then(memory_info_updates, None,
                  [mem_g_info, mem_mode_info, mem_p_info], queue=False)
        btn_proj_new.click(lambda: gr.update(visible=True), None, proj_new_row, queue=False)
        btn_proj_create.click(create_project_handler, proj_new_tb,
                              [proj_dd, proj_new_row, proj_new_tb], queue=True,
                              concurrency_id="chat-run", concurrency_limit=1)\
            .then(update_chats_radio, None, [chats_radio, noproj_radio, del_state], queue=False)\
            .then(memory_info_updates, None,
                  [mem_g_info, mem_mode_info, mem_p_info], queue=False)
        btn_proj_delete.click(
            delete_project_handler, proj_dd,
            [proj_dd, chat, chats_radio, noproj_radio, status_box,
             proj_delete_state, move_dd],
            queue=True, concurrency_id="chat-run", concurrency_limit=1)\
            .then(memory_info_updates, None,
                  [mem_g_info, mem_mode_info, mem_p_info], queue=False)

        # chaty (radio = přepnutí chatu; druhé radio = chaty bez projektu)
        chats_radio.input(load_session_handler, chats_radio,
                           [chat, confirm_row, status_box, proj_dd, work_mode_dd], queue=True,
                           concurrency_id="chat-run", concurrency_limit=1)\
            .then(update_chats_radio, None, [chats_radio, noproj_radio, del_state], queue=False)\
            .then(work_mode_panel_updates, None,
                  [changes_panel, process_panel, research_panel], queue=False)\
            .then(memory_info_updates, None,
                  [mem_g_info, mem_mode_info, mem_p_info], queue=False)
        noproj_radio.input(load_session_handler, noproj_radio,
                            [chat, confirm_row, status_box, proj_dd, work_mode_dd], queue=True,
                            concurrency_id="chat-run", concurrency_limit=1)\
            .then(update_chats_radio, None, [chats_radio, noproj_radio, del_state], queue=False)\
            .then(work_mode_panel_updates, None,
                  [changes_panel, process_panel, research_panel], queue=False)\
            .then(memory_info_updates, None,
                  [mem_g_info, mem_mode_info, mem_p_info], queue=False)
        btn_history_search.click(search_chat_history, history_query, history_results, queue=False)
        history_query.submit(search_chat_history, history_query, history_results, queue=False)
        history_results.input(load_session_handler, history_results,
                              [chat, confirm_row, status_box, proj_dd, work_mode_dd], queue=True,
                              concurrency_id="chat-run", concurrency_limit=1)\
            .then(update_chats_radio, None, [chats_radio, noproj_radio, del_state], queue=False)\
            .then(work_mode_panel_updates, None,
                  [changes_panel, process_panel, research_panel], queue=False)\
            .then(memory_info_updates, None,
                  [mem_g_info, mem_mode_info, mem_p_info], queue=False)
        btn_new.click(new_chat, None, [chat, confirm_row, status_box], queue=True,
                      concurrency_id="chat-run", concurrency_limit=1)\
            .then(update_chats_radio, None, [chats_radio, noproj_radio, del_state], queue=False)
        btn_del_chat.click(delete_current_chat, None,
                           [chat, chats_radio, noproj_radio, status_box, del_state], queue=True,
                           concurrency_id="chat-run", concurrency_limit=1)
        rename_tb.submit(rename_session, rename_tb,
                         [rename_tb, chats_radio, noproj_radio, del_state], queue=False)
        move_dd.input(move_chat_to, move_dd,
                       [chats_radio, noproj_radio, del_state, proj_dd, move_dd], queue=True,
                       concurrency_id="chat-run", concurrency_limit=1)\
            .then(memory_info_updates, None,
                  [mem_g_info, mem_mode_info, mem_p_info], queue=False)
        btn_export_md.click(lambda: export_current_chat("md"), None, export_file, queue=False)
        btn_export_jsonl.click(lambda: export_current_chat("jsonl"), None, export_file, queue=False)
        btn_import_chat.click(import_chat_file, import_file,
                              [chat, import_file, status_box], queue=False)\
            .then(update_chats_radio, None, [chats_radio, noproj_radio, del_state], queue=False)

        # paměť (otevřít v editoru)
        btn_mem_g.click(lambda: open_in_editor(_memory_paths().global_path),
                        None, mem_g_info, queue=False)
        btn_mem_mode.click(lambda: open_in_editor(_memory_paths().mode_path()),
                           None, mem_mode_info, queue=False)
        btn_mem_p.click(lambda: (open_in_editor(_memory_paths().project_path())
                                 if _memory_paths().project_path() else t("Select a project first")),
                        None, mem_p_info, queue=False)

        # jazyk UI (server se přestaví; klient čeká a refreshne stránku)
        lang_dd.input(change_language, lang_dd, None)\
            .then(None, None, None, js="""
            () => {
              const reload = () => fetch('/', {cache: 'no-store'})
                .then(r => { if (r.ok) { location.reload(); } else { setTimeout(reload, 500); } })
                .catch(() => setTimeout(reload, 500));
              setTimeout(reload, 1500);
            }
            """)

        # chat zprávy
        btn_send.click(
            prepare_submission, [msg_in, files_in],
            [submission_state, chat, msg_in, files_in], queue=False)\
            .then(run_prepared_submission, [submission_state, chat],
                  [chat, confirm_row, status_box], queue=True,
                  concurrency_id="chat-run", concurrency_limit=1)\
            .then(update_chats_radio, None, [chats_radio, noproj_radio, del_state], queue=False)
        msg_in.submit(
            prepare_submission, [msg_in, files_in],
            [submission_state, chat, msg_in, files_in], queue=False)\
            .then(run_prepared_submission, [submission_state, chat],
                  [chat, confirm_row, status_box], queue=True,
                  concurrency_id="chat-run", concurrency_limit=1)\
            .then(update_chats_radio, None, [chats_radio, noproj_radio, del_state], queue=False)
        btn_yes.click(confirm_yes, chat, [chat, confirm_row, status_box], queue=True,
                      concurrency_id="chat-run", concurrency_limit=1)
        btn_no.click(confirm_no, chat, [chat, confirm_row, status_box], queue=True,
                     concurrency_id="chat-run", concurrency_limit=1)
        btn_stop_run.click(stop_run, chat, [chat, confirm_row, status_box], queue=False)
        btn_retry.click(retry_last_answer, None, [chat, confirm_row, status_box], queue=True,
                        concurrency_id="chat-run", concurrency_limit=1)
        btn_undo_round.click(undo_last_round, None,
                             [chat, confirm_row, status_box], queue=True,
                             concurrency_id="chat-run", concurrency_limit=1)
        btn_fork.click(fork_last_round, None, [chat, confirm_row, status_box], queue=True,
                       concurrency_id="chat-run", concurrency_limit=1)\
            .then(update_chats_radio, None, [chats_radio, noproj_radio, del_state], queue=False)
        btn_handoff.click(handoff_to_new_session, None,
                          [chat, confirm_row, status_box], queue=True,
                          concurrency_id="chat-run", concurrency_limit=1)\
            .then(update_chats_radio, None, [chats_radio, noproj_radio, del_state], queue=False)
        btn_compress.click(compress_now, chat, [chat, confirm_row, status_box], queue=True,
                           concurrency_id="chat-run", concurrency_limit=1)
        btn_undo_task.click(undo_current_task, None, [task_changes, btn_undo_task], queue=False)
        btn_resume_task.click(continue_saved_task, None,
                              [chat, confirm_row, status_box], queue=True,
                              concurrency_id="chat-run", concurrency_limit=1)
        btn_stop_processes.click(stop_all_processes, None,
                                 [process_status, btn_stop_processes], queue=False)
        btn_clear_pins.click(clear_pinned_context, None,
                             [context_info, btn_clear_pins], queue=False)
        btn_pin_file.click(pin_context_file_dialog, None,
                           [context_info, btn_clear_pins], queue=False)
        btn_open_skills.click(open_skills_folder, None, None, queue=False)
        btn_manual_en.click(lambda: open_user_manual("en"), None, None, queue=False)
        btn_manual_cs.click(lambda: open_user_manual("cs"), None, None, queue=False)
        btn_export_research.click(export_research_ledger, None,
                                  research_export_file, queue=False)
        btn_export_research_docx.click(
            lambda: export_research_synthesis("docx"), None,
            research_export_file, queue=False)
        btn_export_research_pdf.click(
            lambda: export_research_synthesis("pdf"), None,
            research_export_file, queue=False)
        model_dd.input(change_model, model_dd, [status_box, model_dd, kv_cache_dd])
        kv_cache_dd.input(
            change_kv_cache, kv_cache_dd, [status_box, model_dd, kv_cache_dd])
        work_mode_dd.change(
            change_work_mode, work_mode_dd,
            [settings_info, changes_panel, process_panel, research_panel,
             mem_g_info, mem_mode_info, mem_p_info],
            concurrency_id="chat-run", concurrency_limit=1)
        autonomy_dd.change(change_autonomy, autonomy_dd, settings_info)
        thinking_dd.change(change_thinking, thinking_dd, settings_info)
        btn_start.click(lambda: server_cmd("start"), None, [status_box, model_dd, kv_cache_dd])
        btn_stop.click(lambda: server_cmd("stop"), None, [status_box, model_dd, kv_cache_dd])
        btn_refresh.click(lambda: server_cmd("restart"), None, [status_box, model_dd, kv_cache_dd])

        failsafe_hint = t("🛡️ FAILSAFE: mouse to the top-left corner aborts GUI actions · "
                          "read-only commands run without confirmation · everything runs locally")
        gr.Markdown(f"<small>{failsafe_hint}</small>",
                    elem_classes=["hdr"], elem_id="footer-hint")

        # Ctrl+Enter + VYNUCENÝ DARK MODE + chytrý autoscroll
        ui.load(None, None, None, js="""
        () => {
          const setDark = () => {
            [document.body, document.documentElement,
             document.querySelector('gradio-app'),
             document.querySelector('.gradio-container')].forEach(e => e && e.classList.add('dark'));
          };
          setDark(); setTimeout(setDark, 500); setTimeout(setDark, 2000);
          new MutationObserver(setDark).observe(document.body, {childList: true, subtree: true});
          document.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
              const btn = document.getElementById('btn-send');
              if (btn) { e.preventDefault(); btn.click(); }
            }
          });
          const setup = () => {
            const root = document.getElementById('main-chat');
            if (!root) return;
            let el = null;
            for (const c of root.querySelectorAll('div')) {
              if (c.scrollHeight > c.clientHeight + 4) { el = c; break; }
            }
            el = el || root;
            let stick = true;
            el.addEventListener('scroll', () => {
              stick = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
            }, {passive: true});
            const mo = new MutationObserver(() => { if (stick) el.scrollTop = el.scrollHeight; });
            mo.observe(el, {childList: true, subtree: true, characterData: true});
          };
          setup(); setTimeout(setup, 2500);
        }
        """)

        # F5: aktuální konverzace
        def on_page_load():
            r1, r2, ds = update_chats_radio()
            return (chat_view(), gr.update(visible=False), refresh_status(), r1, r2, ds,
                    gr.update(value=state.work_mode))

        ui.load(on_page_load, None,
                [chat, confirm_row, status_box, chats_radio, noproj_radio, del_state,
                 work_mode_dd])\
            .then(memory_info_updates, None,
                  [mem_g_info, mem_mode_info, mem_p_info], queue=False)

        # živý status (⏳ načítám model → 🟢) každých 5 s
        if hasattr(gr, "Timer"):
            gr.Timer(5.0).tick(
                refresh_runtime_controls, outputs=[status_box, model_dd, kv_cache_dd])
            gr.Timer(2.0).tick(refresh_task_changes, outputs=[task_changes, btn_undo_task])
            gr.Timer(2.0).tick(
                refresh_resumable_task,
                outputs=[resumable_status, btn_resume_task, resumable_panel])
            gr.Timer(2.0).tick(refresh_processes, outputs=[process_status, btn_stop_processes])
            gr.Timer(5.0).tick(refresh_context_inspector,
                               outputs=[context_info, btn_clear_pins])
            gr.Timer(10.0).tick(skills_info_text, outputs=skills_info)
            gr.Timer(2.0).tick(research_status_text, outputs=research_status)
    return ui


def _port_busy(host: str, port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


def _is_our_webui(host: str, port: int) -> bool:
    import requests
    try:
        r = requests.get(f"http://{host}:{port}/config", timeout=2)
        return r.status_code == 200 and "components" in r.text
    except Exception:
        return False


if __name__ == "__main__":
    import os
    import webbrowser

    host = cfg.web["host"]
    port = int(os.environ.get("QWEN_WEB_PORT") or cfg.web["port"])

    if os.environ.get("QWEN_AUTOSTART_SERVER"):
        _autostart_server_thread()

    if _port_busy(host, port):
        if _is_our_webui(host, port):
            url = f"http://{host}:{port}"
            print(f"[INFO] Web UI already running at {url} — not starting a second "
                  f"instance, opening the browser.")
            if not os.environ.get("QWEN_NO_BROWSER"):
                webbrowser.open(url)
            sys.exit(0)
        # port drží cizí proces → najdi nejbližší volný
        new_port = port
        while _port_busy(host, new_port) and new_port < port + 20:
            new_port += 1
        print(f"[INFO] Port {port} is taken by another process — starting Web UI on port {new_port}.")
        port = new_port

    # obslužná smyčka: po přepnutí jazyka se UI přestaví (jinak normální běh)
    browser_opened = False
    while True:
        _ACTIVE_DEMO = build_ui()
        for attempt in range(3):  # port po close chvíli odmítá bind → retry
            try:
                _ACTIVE_DEMO.launch(
                    server_name=host,
                    server_port=port,
                    css=CUSTOM_CSS,
                    show_error=True,  # detail chyb při ladění (jen localhost)
                    inbrowser=not browser_opened and not os.environ.get("QWEN_NO_BROWSER"),
                    allowed_paths=[str(cfg.path("paths.sessions_dir"))],
                )
                break
            except OSError:
                if attempt == 2:
                    raise
                time.sleep(1.0)
        browser_opened = True
        if not state.ui_reload.is_set():
            break
        state.ui_reload.clear()
        time.sleep(0.5)
