"""Webové UI pro Qwen3.8-27B harness (Gradio, pouze localhost).

Spuštění:  .venv/Scripts/python webapp.py  →  http://127.0.0.1:7860
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# pythonw (bez konzole) nemá stdout/stderr → redirect do logu, ať není tichá smrt
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
from harness.llm import LLMClient
from harness.prompts import system_prompt
from harness.safety import SafetyPolicy
from harness.session import Session, IMG_MIMES
from harness import servermgmt

cfg = load_config()
llm = LLMClient(cfg)

# trvalá paměť: prázdný soubor globální paměti zakládáme hned na startu
from harness.memory import MemoryStore
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
        saved = _load_ui_state()
        self.model_key = saved.get("model") or cfg.model_key()
        if self.model_key not in cfg.data["models"]:
            self.model_key = cfg.model_key()
        cfg.data["default_model"] = self.model_key  # agent podle toho zná ctx limit
        self.mode = saved.get("mode") or cfg.agent.get("mode", "agent")
        if self.mode not in ("chat", "agent", "computer"):
            self.mode = "agent"
        self.autonomy = saved.get("autonomy") or cfg.agent.get("autonomy", "supervised")
        self.thinking = saved.get("thinking", cfg.data.get("thinking", True))
        self.reasoning_effort = saved.get("reasoning_effort") or cfg.data.get("reasoning_effort", "xhigh")
        if self.reasoning_effort not in ("xhigh", "medium", "low"):
            self.reasoning_effort = "xhigh"
        cfg.data["thinking"] = bool(self.thinking)
        cfg.data["reasoning_effort"] = self.reasoning_effort
        self.workspace = saved.get("workspace") or cfg.agent.get("workspace")
        self.recent_ws: list[str] = saved.get("recent", [])
        if self.workspace:
            cfg.agent["workspace"] = self.workspace  # převezme každý nový Agent
            from harness.projects import Projects
            Projects(cfg).ensure_registered(self.workspace)  # migrace → projekt
        self._restore_session()

    def _restore_session(self) -> None:
        """Obnov session uloženou jako aktivní (fallback: poslední na disku)."""
        saved = _load_ui_state().get("session_id")
        if saved:
            try:
                self.session = Session.load(cfg, saved, self._system_prompt())
                self.rebuild_agent()
                return
            except FileNotFoundError:
                pass
        try:
            latest = Session.list_sessions(cfg)
            if latest and latest[0]["messages"] > 1:
                self.session = Session.load(cfg, latest[0]["id"], self._system_prompt())
                self.rebuild_agent()
                return
        except Exception:
            pass
        self.new_session()

    def save_ui_state(self) -> None:
        _save_ui_state({
            "workspace": self.workspace,
            "recent": self.recent_ws,
            "model": self.model_key,
            "mode": self.mode,
            "autonomy": self.autonomy,
            "thinking": bool(self.thinking),
            "reasoning_effort": self.reasoning_effort,
            "session_id": getattr(self, "session", None).id if getattr(self, "session", None) else None,
        })

    def new_session(self) -> None:
        self.session = Session(cfg, system_prompt=self._system_prompt(),
                               workspace=self.workspace, transient=True)
        self.rebuild_agent()
        try:
            self.save_ui_state()
        except Exception:
            pass

    def rebuild_agent(self) -> None:
        safety = SafetyPolicy(
            autonomy=self.autonomy,
            max_steps=int(cfg.agent.get("max_steps", 40)),
            semi_max_steps=int(cfg.agent.get("semi_max_steps", 15)),
        )
        self.abort = threading.Event()
        self.agent = Agent(cfg, llm, self.session, build_registry(self.mode),
                           safety, mode=self.mode, abort_flag=self.abort,
                           on_event=self.hub.on_event)
        if self.workspace:
            try:
                self.agent.set_workspace(self.workspace)
            except ValueError:
                pass

    def _system_prompt(self) -> str:
        from harness.prompts import build_system_prompt
        return build_system_prompt(self.mode, cfg, self.workspace)

    def _refresh_system_prompt(self) -> None:
        """Aktualizuj system prompt existující session (změna workspace/režimu)."""
        if self.session.messages and self.session.messages[0]["role"] == "system":
            self.session.messages[0]["content"] = self._system_prompt()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.rebuild_agent()
        self._refresh_system_prompt()

    def set_workspace(self, path: str) -> Path:
        """Nastav workspace + persist do state souboru."""
        p = self.agent.set_workspace(path)  # ValueError pokud neexistuje
        self.workspace = str(p)
        cfg.agent["workspace"] = str(p)
        self.recent_ws = [str(p)] + [w for w in self.recent_ws if w != str(p)]
        self.recent_ws = self.recent_ws[:8]
        self.save_ui_state()
        self._refresh_system_prompt()
        try:
            MemoryStore(cfg, p).ensure_project()  # založ projektovou paměť, pokud chybí
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


# ------------------------------------------------------------- live streaming
import queue as _queue
import threading as _threading


class StreamHub:
    """Sbírá stream události z agenta (volané z worker vlákna) pro live render."""

    def __init__(self) -> None:
        import time as _t
        self._lock = _threading.Lock()
        self.text = ""
        self.reasoning = ""
        self.rev = 0  # inkrement při každé změně
        self.last_activity = _t.time()  # poslední jakákoli událost (tokeny i nástroje)

    def reset(self) -> None:
        import time as _t
        with self._lock:
            self.text = ""
            self.reasoning = ""
            self.rev += 1
            self.last_activity = _t.time()

    def on_event(self, kind: str, payload) -> None:
        import time as _t
        with self._lock:
            if kind == "text" and payload:
                self.text += payload
                self.rev += 1
                self.last_activity = _t.time()
            elif kind == "reasoning" and payload:
                self.reasoning += payload
                self.rev += 1
                self.last_activity = _t.time()
            elif kind in ("tool_start", "tool_result"):
                self.last_activity = _t.time()

    def snapshot(self) -> tuple[str, str, int, float]:
        with self._lock:
            return self.text, self.reasoning, self.rev, self.last_activity


def _live_message(hub: StreamHub, elapsed_s: int = 0) -> dict:
    """Živá zpráva: streamovaný text, nebo 'uvažuji' s poctivým indikátorem aktivity.

    elapsed_s = sekundy od posledního toku - roste, i když model mlčí (poctivé:
    uživatel vidí, že se nic neděje; blikající kurzor se obnovuje jen s daty).
    """
    text, reasoning, _, _ = hub.snapshot()
    cursor = ' <span class="blink-cursor">▍</span>'
    if text:
        suffix = f"\n\n<i>⏳ {elapsed_s}s bez nových tokenů</i>" if elapsed_s >= 5 else ""
        return {"role": "assistant", "content": text + cursor + suffix}
    tail = reasoning[-200:].replace("\n", " ") if reasoning else ""
    head = f"💭 <i>uvažování… ({elapsed_s}s)</i>"
    return {"role": "assistant",
            "content": (head + f" <small>{tail}</small>" if tail else head) + cursor}


def _step_threaded(agent, approve: bool | None) -> tuple:
    """Spusť jeden agent.step ve vlákně; vrať (result, exception)."""
    box: dict = {}

    def _worker():
        try:
            box["r"] = agent.step(approve=approve)
        except BaseException as e:  # noqa: BLE001 - posíláme ven
            box["e"] = e

    t = _threading.Thread(target=_worker, daemon=True, name="agent-step")
    t.start()
    return t, box


state = AppState()


# ------------------------------------------------------------- render helpers
_HIDDEN_NOTE_PREFIXES = ("[TASK PROTOCOL", "[PROGRESS UPDATE", "[FINAL SUMMARY", "[Interrupted by user]")


def chat_view() -> list[dict]:
    """Převeď session messages do formátu gr.Chatbot (celá historie včetně komprimované části)."""
    from harness.agent import _PROTOCOL_MARKS
    hidden = tuple(_PROTOCOL_MARKS) + _HIDDEN_NOTE_PREFIXES[3:]
    out = []
    cut = state.session.compression["cut"] if state.session.compression else None
    for idx, m in enumerate(state.session.messages):
        if cut is not None and idx == cut:
            out.append({"role": "assistant",
                        "content": "📦 **Kontext komprimován** — vše nad tímto markerem model už nevidí "
                                   "(pracuje se souhrnem). Pro tebe je historie zachovaná celá."})
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
            out.append({"role": "assistant", "content": f"🖼️ přiložen obrázek: {Path(imgs[-1]).name}"})
            continue
        msg: dict = {"role": role, "content": content or "…"}
        if role == "user" and imgs:
            msg["content"] = (content + "\n" if content else "") + f"🖼️ +{len(imgs)} obrázek(ky)"
        out.append(msg)
    return out


TOOL_ICON = {"screenshot": "📸", "click": "🖱️", "type_text": "⌨️", "press_key": "⌨️",
             "scroll": "🖱️", "move_mouse": "🖱️", "run_command": "💻", "view_image": "🖼️"}


def _content_str(msg: dict) -> str:
    """Obsah zprávy jako string - zvládá plain string i Gradio list-of-parts formát."""
    c = msg.get("content", "")
    if isinstance(c, str):
        return c
    if isinstance(c, list):  # [{"type": "text", "text": "..."}, ...]
        return " ".join(str(p.get("text", "")) for p in c if isinstance(p, dict))
    return str(c)


def _is_pending_question(msg: dict) -> bool:
    return msg.get("role") == "assistant" and "Čekám na potvrzení" in _content_str(msg)


def _error_message(e: BaseException) -> str:
    """Jemná chybová zpráva do chatu (místo červeného overlay Gradia)."""
    import traceback
    lines = traceback.format_exc(limit=4).strip().splitlines()
    tail = lines[-1][:200] if len(lines) > 1 else ""
    msg = (f"❌ **Došlo k chybě** — `{type(e).__name__}: {e}`\n\n"
           f"<small>`{tail}`</small>\n\n"
           f"Můžeš zkusit pokračovat další zprávou. Pokud problém přetrvává, "
           f"zkus **🆕 Novou session** nebo **▶ Start serveru**.")
    return msg


def _agent_error_message(r) -> str:
    """Chybový stav agentu (Status.ERROR) jako srozumitelná zpráva."""
    hint = ""
    if "Connection" in r.text or "Connect" in r.text or "timeout" in r.text.lower():
        hint = "\n\n💡 *Vypadá to na problém s inference serverem — zkus **▶ Start serveru**.*"
    elif "tool" in r.text.lower():
        hint = "\n\n💡 *Nástroj selhal — zkus zadat úkol jinak.*"
    return f"⚠️ **{r.text}**{hint}"


def _run_steps(history: list[dict], approve: bool | None = None):
    """Generátor: krokuj agentem; tokeny streamuje živě (~7×/s).

    Krok agenta běží ve vlákně, události (text/reasoning) tečou přes StreamHub,
    tady se pollingují a promítají do dočasné "live" zprávy v chatu.
    Výjimky zachytává a vrací jako zprávu v chatu (nikdy nenechá spadnout UI).
    """
    import time as _time

    try:
        first = True
        seen_rev = state.session.compression_rev
        while True:
            state.hub.reset()
            t, box = _step_threaded(state.agent, approve if first else None)
            first = False
            live_idx: int | None = None
            last_rev = -1
            last_change = _time.time()
            shown_sec = -1
            prev_yield_rev = -1
            idle_strikes = 0  # počítadla pro dead-man detekci zombie streamu
            while t.is_alive():
                _, _, rev, last_activity = state.hub.snapshot()
                now = _time.time()
                if rev != last_rev:
                    last_rev = rev
                    last_change = now
                elapsed = int(now - last_change)
                # yield při nových datech, nebo každou sekundu (poctivý indikátor)
                if rev != prev_yield_rev or elapsed != shown_sec:
                    prev_yield_rev = rev
                    shown_sec = elapsed
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
                                        "content": "🔌 **Spojení se serverem se zaseklo** (server už "
                                                   "negeneruje, ale odpověď nedorazila). "
                                                   "Odpověď nebyla dokončena — zkus zprávu odeslat znovu."})
                        yield history, gr.update(visible=False), refresh_status()
                        return
                else:
                    idle_strikes = 0
                _time.sleep(0.15)
            t.join()
            # live zprávu odstraň - finální obsah přijdou níže (plný text / tool trace)
            if live_idx is not None:
                history.pop(live_idx)
            if "e" in box:
                raise box["e"]
            r = box.get("r")
            # live marker, pokud během kroku došlo ke kompresi kontextu
            if state.session.compression_rev != seen_rev:
                seen_rev = state.session.compression_rev
                history.append({"role": "assistant",
                                "content": "📦 **Kontext automaticky komprimován** — model nyní pracuje se "
                                           "souhrnem starší konverzace. Celá historie zůstává nahoře k nahlédnutí."})
            if r is None:
                raise RuntimeError("agent step skončil bez výsledku")
            if r.status is Status.CONTINUE:
                for name, args, result in r.tool_trace:
                    icon = TOOL_ICON.get(name, "🔧")
                    short = result if len(result) <= 300 else result[:300] + " …"
                    history.append({"role": "assistant", "content": f"{icon} **{name}** → {short}"})
                yield history, gr.update(visible=False), refresh_status()
            elif r.status is Status.FINAL:
                history.append({"role": "assistant", "content": r.text or "…"})
                yield history, gr.update(visible=False), refresh_status()
                return
            elif r.status is Status.NEEDS_CONFIRMATION:
                lines = "\n".join(f"⚠️ `{a}`" for a in r.pending_summary)
                history.append({"role": "assistant",
                                "content": f"**Čekám na potvrzení akce:**\n{lines}"})
                yield history, gr.update(visible=True), refresh_status()
                return
            else:  # ABORTED / ERROR
                text = _agent_error_message(r) if r.status is Status.ERROR else f"⛔ {r.text}"
                history.append({"role": "assistant", "content": text})
                yield history, gr.update(visible=False), refresh_status()
                return
    except Exception as e:  # pojistka - žádné spadnutí UI
        history.append({"role": "assistant", "content": _error_message(e)})
        yield history, gr.update(visible=False), refresh_status()


# ------------------------------------------------------------- handlery
def send_message(message: str, files, history: list[dict]):
    try:
        if not (message or "").strip() and not files:
            yield history, gr.update(visible=False), refresh_status()
            return
        cfg.data["thinking"] = state.thinking
        imgs = [Path(f) for f in (files or []) if Path(f).suffix.lower() in IMG_MIMES]
        shown = (message.strip() or "") + (f"\n🖼️ +{len(imgs)} obrázek(ky)" if imgs else "")
        history.append({"role": "user", "content": shown})
        yield history, gr.update(visible=False), refresh_status()
        state.agent.new_task(message.strip() or "Please analyze the attached image(s).", images=imgs)
        yield from _run_steps(history)
    except Exception as e:
        history.append({"role": "assistant", "content": _error_message(e)})
        yield history, gr.update(visible=False), refresh_status()


def confirm(approve: bool, history: list[dict]):
    """Reakce na tlačítka Povolit/Zamítnout."""
    try:
        if not state.agent._pending:
            # není co potvrzovat (např. po dvojkliku) - jen zavři panel
            if history and _is_pending_question(history[-1]):
                history.pop()
            yield history, gr.update(visible=False), refresh_status()
            return
        # odeber zprávu s dotazem a zaloguj rozhodnutí uživatele
        if history and _is_pending_question(history[-1]):
            history.pop()
        history.append({"role": "user", "content": "✅ Povolit" if approve else "❌ Zamítnout"})
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


def stop_run(history: list[dict]):
    state.abort.set()
    yield history, gr.update(visible=False), refresh_status()


def new_chat():
    state.new_session()
    return [], gr.update(visible=False), refresh_status()


def compress_now(history: list[dict]):
    """📦 Ruční komprese: souhrn starší konverzace (i pod prahem 85 %)."""
    try:
        est = state.session.estimate_context_tokens()
        rev_before = state.session.compression_rev
        gr.Info("📦 Vytvářím souhrn starší konverzace (chvíli trvá) ...")
        state.agent._maybe_compress(force=True)
        if state.session.compression_rev == rev_before:
            gr.Warning("Není co komprimovat (příliš krátká konverzace).")
            yield history, gr.update(visible=False), refresh_status()
            return
        est2 = state.session.estimate_context_tokens()
        history.append({"role": "assistant",
                        "content": f"📦 **Ruční komprese dokončena** — ~{est / 1000:.1f}k → ~{est2 / 1000:.1f}k "
                                   f"tokenů. Model pracuje se souhrnem, historie zůstává celá."})
        gr.Info(f"✅ Komprimováno: ~{est / 1000:.1f}k → ~{est2 / 1000:.1f}k tokenů")
        yield history, gr.update(visible=False), refresh_status()
    except Exception as e:
        history.append({"role": "assistant", "content": _error_message(e)})
        yield history, gr.update(visible=False), refresh_status()


def handoff_to_new_session():
    """📦 Předat práci do nové session: souhrn stávající konverzace + čistý kontext."""
    try:
        if len(state.session.messages) <= 2:
            gr.Warning("Session je prázdná - není co předávat.")
            yield chat_view(), gr.update(visible=False), refresh_status()
            return
        gr.Info("📦 Vytvářím souhrn konverzace (chvíli trvá) ...")
        from harness.context import summarize_messages
        summary = summarize_messages(llm, state.session.messages[1:])
        state.new_session()
        state.session.add(
            "user",
            "[HANDOFF from previous session]\n" + summary +
            "\n\nThis is a summary of the previous session. Continue the work from this state.")
        gr.Info("✅ Nová session se souhrnem připravena")
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
        return "právě teď"
    if d < 3600:
        return f"před {int(d // 60)} min"
    if d < 86400:
        return f"před {int(d // 3600)} h"
    return f"před {int(d // 86400)} d"


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
        proj = Path(ws).name if ws else "— bez projektu —"
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
    """Přejmenuj aktuální chat (potvrdí tlačítko Uložit)."""
    try:
        name = (name or "").strip()
        if not name:
            gr.Warning("Zadej nový název chatu.")
            return gr.update(), gr.update(), gr.update(), gr.update()
        state.session.meta["title"] = name[:100]
        state.session._save_meta()
        gr.Info(f"✅ Chat přejmenován: {name[:60]}")
        r1, r2, b = update_chats_radio()
        return gr.update(value=""), r1, r2, b
    except Exception as e:
        gr.Warning(f"❌ {e}")
        return gr.update(), gr.update(), gr.update(), gr.update()


def load_session_handler(selection: str):
    """Načti session; pokud patří jinému projektu, přepni i workspace."""
    try:
        if not selection or selection == state.session.id:
            yield chat_view(), gr.update(visible=False), refresh_status(), gr.update()
            return
        state.session = Session.load(cfg, selection, state._system_prompt())
        state.rebuild_agent()
        # session jiného projektu → přepni workspace (multi-project switching)
        s_ws = state.session.meta.get("workspace")
        if s_ws and s_ws != state.workspace:
            try:
                state.set_workspace(s_ws)
                gr.Info(f"✅ Session načtena + workspace přepnuta na {Path(s_ws).name}")
            except ValueError:
                gr.Info(f"✅ Session načtena (workspace {s_ws} už neexistuje)")
        else:
            gr.Info(f"✅ Session načtena: {state.session.meta.get('title', selection)[:50]}")
        state.save_ui_state()
        yield chat_view(), gr.update(visible=False), refresh_status(), \
            gr.update(choices=project_choices(), value=current_project_name())
    except Exception as e:
        gr.Warning(f"❌ {e}")
        yield chat_view(), gr.update(visible=False), refresh_status(), gr.update()


def change_model(key: str):
    if servermgmt.ensure(cfg, key):
        state.model_key = key
        cfg.data["default_model"] = key  # agent/ctx-limit sledují aktuální model
        state.save_ui_state()
        return refresh_status()
    return "❌ Přepnutí modelu selhalo (viz runtime/llama-server.log)"


def change_mode(mode: str):
    state.set_mode(mode)
    state.save_ui_state()
    return f"Režim: **{mode}** · {refresh_status()}"


def change_autonomy(a: str):
    state.autonomy = a
    state.rebuild_agent()
    state.save_ui_state()
    return f"Autonomie: **{a}**"


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
    return f"Přemýšlení: **{mode_txt}**"


def _ctx_pct() -> int:
    try:
        est = state.session.estimate_context_tokens()
        limit = int(cfg.model().get("ctx_size", 32768))
        return min(200, est * 100 // max(limit, 1))
    except Exception:
        return 0


def _check_ctx_warning() -> None:
    """Toast varování při překročení prahů kontextu (jen při přechodu, ne opakovaně)."""
    pct = _ctx_pct()
    prev = getattr(state, "last_ctx_pct", 0)
    state.last_ctx_pct = pct
    if prev < 70 <= pct < 85:
        gr.Warning(f"📊 Kontext na {pct} % — auto-komprese proběhne při 85 %")
    elif prev < 85 <= pct:
        gr.Warning(f"📊 Kontext na {pct} % — blízko limitu! Zvaž 📦 Předej (souhrn do nové session)")


def _memory_paths():
    from harness.memory import MemoryStore
    store = MemoryStore(cfg, Path(state.workspace) if state.workspace else None)
    return store


# ------------------------------------------------------------- projekty
from harness.projects import Projects


def _projects() -> Projects:
    return Projects(cfg)


NOPROJ_NAME = "žádný projekt"


def project_choices() -> list[str]:
    return [NOPROJ_NAME] + [p["name"] for p in _projects().list_all()]


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
            gr.Info("∅ Bez projektu - nové chatty budou bez příslušnosti")
            return gr.update(choices=project_choices(), value=NOPROJ_NAME)
        proj = next((p for p in _projects().list_all() if p["name"] == name), None)
        if not proj:
            return gr.update()
        if proj.get("missing"):
            gr.Warning(f"Složka projektu neexistuje: {proj['path']}")
            return gr.update()
        state.set_workspace(proj["path"])
        gr.Info(f"📁 Projekt: {proj['name']}")
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
        gr.Info(f"📁 Připojen projekt: {proj['name']}")
        return gr.update(choices=project_choices(), value=proj["name"]), gr.update(visible=False)
    except Exception as e:
        gr.Warning(f"❌ {e}")
        return gr.update(), gr.update(visible=False)


def create_project_handler(name: str):
    """➕ Nový projekt: vytvoří složku v projects/ a zaregistruje."""
    try:
        name = (name or "").strip()
        if not name:
            gr.Warning("Zadej název projektu.")
            return gr.update(), gr.update(visible=False), ""
        proj = _projects().create_new(name)
        state.set_workspace(proj["path"])
        gr.Info(f"📁 Vytvořen projekt {proj['name']} → {proj['path']}")
        return gr.update(choices=project_choices(), value=proj["name"]), gr.update(visible=False), ""
    except Exception as e:
        gr.Warning(f"❌ {e}")
        return gr.update(), gr.update(visible=False), ""


def _active_entry() -> tuple[str, str] | None:
    """Aktivní session jako položka seznamu (i transient - hned viditelná)."""
    s = getattr(state, "session", None)
    if s is None:
        return None
    title = (s.meta.get("title") or "(bez titulku)")[:38]
    when = "právě teď" if s.transient else _rel_time(s.meta.get("updated") or time.time())
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
        if act and act[1] not in {sid for _, sid in out} and state.session.meta.get("workspace"):
            out.insert(0, act)  # aktivní (transient) chat hned nahoře
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
    return gr.update(value="⚠️ **Potvrď smazání — klikni znovu do 6 s**" if armed else "")


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
        gr.Warning("Potvrď smazání: klikni na tlačítko znovu do 6 s.")
        # no-op pro ostatní komponenty = rychlé apply (jinak Gradio spolkne rychlé 2. kliknutí)
        return gr.update(), gr.update(), gr.update(), gr.update(), _del_state(True)
    _del_arm["ts"] = 0.0                     # 2. klik → smazat
    sid = state.session.id
    if state.session.transient:
        gr.Info("Aktivní chat není uložený (prázdný) - není co mazat.")
        return gr.update(), gr.update(), gr.update(), gr.update(), _del_state(False)
    state.new_session()                      # náhrada je transient - nic se neukládá
    ok = Session.delete(cfg, sid)
    gr.Info("🗑 Chat smazán" if ok else "Chat už neexistuje")
    r1, r2, _ = update_chats_radio()
    return chat_view(), r1, r2, refresh_status(), _del_state(False)


def open_in_editor(path: Path | str):
    """Otevři soubor ve výchozím editoru uživatele."""
    import os as _os
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch(exist_ok=True)
        _os.startfile(str(p))  # noqa: S606 - Windows default app
        return f"Otevírám: {p}"
    except Exception as e:
        return f"❌ {e}"


# ------------------------------------------------------------- mazání chatů
_selected_sid: dict = {"id": None}


def _selected_info_text() -> str:
    sid = _selected_sid.get("id")
    if not sid:
        return "<small>klikni na řádek v tabulce → vybere se chat (nic se nenačte)</small>"
    row = next((s for s in _sessions_rows if s["id"] == sid), None)
    if not row:
        return "<small>vybraný chat už neexistuje</small>"
    proj = Path(row["workspace"]).name if row.get("workspace") else "bez projektu"
    return (f"<small>📄 vybráno: <b>{row['title'][:60]}</b> · {proj} · "
            f"{row['messages']} zpráv</small>")


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
        gr.Warning("Nejdřív klikni na řádek chatu v tabulce (výběr).")
        return
    yield from load_session_handler(sid)


def delete_selected_session():
    """🗑 Smaž vybraný chat (jde i aktuální - nahradí se novým prázdným)."""
    try:
        sid = _selected_sid.get("id")
        if not sid:
            gr.Warning("Nejdřív klikni na řádek chatu v tabulce (výběr).")
            yield chat_view(), sessions_refresh(), _selected_info_text(), gr.update(visible=False), refresh_status()
            return
        if sid == state.session.id:
            state.new_session()  # otevřený chat nahraď novým, pak maž
        ok = Session.delete(cfg, sid)
        gr.Info("🗑 Chat smazán" if ok else "Chat nenalezen (už smazán?)")
        _selected_sid["id"] = None
        yield chat_view(), sessions_refresh(), _selected_info_text(), gr.update(visible=False), refresh_status()
    except Exception as e:
        gr.Warning(f"❌ {e}")
        yield chat_view(), sessions_refresh(), _selected_info_text(), gr.update(visible=False), refresh_status()


def _mem_infos():
    """Info texty o souborech paměti (cesty k otevření)."""
    try:
        store = _memory_paths()
        g = f"**Globální:** `{store.global_path}`"
        p = store.project_path()
        p_txt = f"**Projektová:** `{p}`" if p else "**Projektová:** — nejdřív vyber projekt"
    except Exception:
        g, p_txt = "—", "—"
    return f"<small>{g}</small>", f"<small>{p_txt}</small>"


def _mem_g_text() -> str:
    return _mem_infos()[0]


def _mem_p_text() -> str:
    return _mem_infos()[1]


def load_memory_global() -> str:
    try:
        return _memory_paths().read("global")
    except Exception:
        return ""


def load_memory_project() -> str:
    try:
        store = _memory_paths()
        if store.project_path() is None:
            return "(nastav workspace - pak se paměť projektu váže k němu)"
        return store.read("project")
    except Exception:
        return ""


def save_memory_handler(global_text: str, project_text: str):
    """Ulož obě paměti (plná uživatelská kontrola) + občerstvi system prompt."""
    try:
        store = _memory_paths()
        store.global_path.parent.mkdir(parents=True, exist_ok=True)
        store.global_path.write_text(global_text, encoding="utf-8")
        pp = store.project_path()
        if pp is not None and not project_text.startswith("(nastav workspace"):
            pp.write_text(project_text, encoding="utf-8")
        state._refresh_system_prompt()
        gr.Info("✅ Paměť uložena - model ji uvidí od další zprávy")
        return gr.update(), gr.update()
    except Exception as e:
        gr.Warning(f"❌ {type(e).__name__}: {e}")
        return gr.update(), gr.update()


def refresh_status():
    """Status ve 3 řádcích: model / VRAM / tokeny."""
    st = servermgmt.server_state(cfg)
    key = servermgmt.running_model(cfg) or state.model_key
    mfile = cfg.data["models"].get(key, {}).get("file", "")
    verze = mfile.replace("Qwen3.8-27B-", "").replace(".gguf", "") or key
    model_name = f"Qwen3.8-27B · {verze}"
    if st == "running":
        line1 = f"🟢 {model_name}"
        line2 = f"🖥️ GPU VRAM: {servermgmt.vram_str()}"
    elif st == "starting":
        line1 = f"⏳ načítám {model_name} do VRAM…"
        line2 = "🖥️ GPU VRAM: —"
    else:
        line1 = f"🔴 {model_name} — server stojí"
        line2 = "🖥️ GPU VRAM: —"
    try:
        est = state.session.estimate_context_tokens()
        limit = int(cfg.data["models"].get(key, {}).get("ctx_size", 32768))
        pct = min(100, est * 100 // max(limit, 1))
        warn = " 🟠" if pct >= 70 else (" 🔴" if pct >= 85 else "")
        line3 = f"📊 ctx ~{est / 1000:.1f}k / {limit // 1000}k tokenů{warn}"
    except Exception:
        line3 = "📊 ctx —"
    _check_ctx_warning()
    return f"{line1}<br>{line2}<br>{line3}"


def _autostart_server_thread() -> None:
    """Launcher nastaví QWEN_AUTOSTART_SERVER=1 → model se nahodí na pozadí,
    UI zobrazuje ⏳ stav (UI first, model second)."""
    def _run():
        try:
            if servermgmt.server_state(cfg) != "down":
                return  # už běží nebo startuje (někdo jiný)
            print("[AUTOSTART] na pozadí startuji llama-server ...", flush=True)
            servermgmt.start(cfg)
            print("[AUTOSTART] model připraven.", flush=True)
        except Exception as e:
            print(f"[AUTOSTART] selhalo: {e}", flush=True)
    threading.Thread(target=_run, daemon=True, name="autostart-server").start()


# ------------------------------------------------------------- workspace
WS_JUNK = {".git", "node_modules", ".venv", "venv", "__pycache__", ".idea", ".vscode", "dist", "build"}

# PowerShell FolderBrowserDialog (fallback, když by chyběl tkinter).
# -STA je nutné pro Windows Forms dialogy; topmost owner drží dialog nad prohlížečem.
_PS_FOLDER_DIALOG = r"""
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true; $owner.ShowInTaskbar = $false
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = 'Vyber slozku projektu (workspace)'
if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
    Write-Output $d.SelectedPath
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
            path = filedialog.askdirectory(title="Vyber složku projektu (workspace)")
        finally:
            root.destroy()
        if path:
            return path
    except Exception:
        pass
    # 2) PowerShell fallback
    try:
        import subprocess
        out = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", _PS_FOLDER_DIALOG],
            capture_output=True, text=True, timeout=600,
        )
        p = (out.stdout or "").strip().strip('"')
        if p and Path(p).is_dir():
            return p
    except Exception:
        pass
    return None


def set_workspace_handler(path: str):
    """Nastaví workspace (z dropdownu/ručního zadání). Feedback jako toast."""
    try:
        p = state.set_workspace(path)
        gr.Info(f"✅ Workspace: {p}")
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
    """Text s aktuálním workspace (používáno ve zpětné vazbi / debugu)."""
    if not state.workspace:
        return "📁 Workspace: nenastaven"
    return f"📁 Workspace: {state.workspace}"


def server_cmd(cmd: str):
    if cmd == "start":
        return change_model(state.model_key)
    if cmd == "stop":
        servermgmt.stop(cfg, quiet=True)
    if cmd == "restart":
        servermgmt.stop(cfg, quiet=True)
        return change_model(state.model_key)
    return refresh_status()


def _clear_inputs():
    """Vyčisti vstupní pole a upload po odeslání."""
    return gr.update(value=""), gr.update(value=None)


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
  font-family: 'Segoe UI Variable Text','Segoe UI','Segoe UI Symbol','Segoe UI Emoji','Noto Color Emoji',system-ui,sans-serif !important;
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
#msg-in textarea { min-height: 44px !important; max-height: 110px !important; border-radius: 10px !important; }
#files-in { max-height: 72px !important; overflow-y: auto !important; }
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
.side-h { color: #2dd4bf !important; font-weight: 700 !important;
  letter-spacing: .08em !important; margin: 14px 0 4px 2px !important; display: block; }
.sqsm { min-height: 34px !important; font-size: 12px !important; border-radius: 9px !important; }
#del-state p { color: #f87171 !important; font-size: 12px !important; margin: 2px 0 0 4px !important; }
#chats-radio label, #noproj-radio label { padding: 5px 8px !important; border-radius: 8px !important;
  font-size: 12.5px !important; }
#chats-radio label:hover, #noproj-radio label:hover { background: #1c2430 !important; }
#chats-radio label.selected, #noproj-radio label.selected { background: #14323c !important; border: 1px solid #2dd4bf55 !important; }
#main-chat { height: calc(100vh - 210px) !important; min-height: 340px !important; border-radius: 12px !important; }
#footer-hint { margin-top: 4px !important; }
/* blikající kurzor */
@keyframes qwen-blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }
.blink-cursor { animation: qwen-blink 1s step-end infinite; }
"""


# Nový build_ui - layout ve stylu ZCode/Codex: levý sidebar + hlavní chat
def build_ui() -> gr.Blocks:
    model_choices = list(cfg.data["models"].keys())
    with gr.Blocks(title="Qwen3.8-27B Harness") as ui:
        with gr.Row(elem_id="app-row", elem_classes=["gap"]):
            # ================= LEVÝ SIDEBAR =================
            with gr.Column(scale=0, elem_id="sidebar"):
                gr.Markdown("## 🤖 <span style='color:#2dd4bf'>Qwen</span>3.8",
                            elem_classes=["hdr", "side-title"])
                status_box = gr.Markdown(refresh_status, elem_id="status-pill")

                gr.Markdown("<small class='side-h'>⚙ FUNKCE</small>", elem_classes=["hdr"])
                with gr.Row(elem_classes=["gap"]):
                    btn_start = gr.Button("start server", size="sm", elem_classes=["sqsm"], scale=1)
                    btn_stop = gr.Button("stop server", size="sm", elem_classes=["sqsm"], scale=1)
                    btn_refresh = gr.Button("restart server", size="sm", elem_classes=["sqsm"], scale=1)
                btn_compress = gr.Button("komprimuj aktuální chat", size="sm", elem_classes=["sqsm"])
                btn_handoff = gr.Button("předej novému chatu", size="sm", elem_classes=["sqsm"])

                with gr.Accordion("⚙️ Nastavení", open=False):
                    model_dd = gr.Dropdown(model_choices, value=state.model_key, label="Model")
                    mode_dd = gr.Dropdown(["chat", "agent", "computer"], value=state.mode, label="Režim")
                    autonomy_dd = gr.Dropdown(["supervised", "semi", "auto"], value=state.autonomy,
                                              label="Autonomie")
                    thinking_dd = gr.Dropdown(["xhigh", "medium", "low", "off"],
                                              value=("off" if not state.thinking else state.reasoning_effort),
                                              label="Přemýšlení")
                    settings_info = gr.Markdown("")
                    gr.Markdown("<small class='side-h'>🧠 PAMĚŤ</small>", elem_classes=["hdr"])
                    gr.Markdown("<small>Model paměti čte při každé úloze a po kompresi; "
                                "fakta ukládá na požádání („zapamatuj si…“).</small>",
                                elem_classes=["hdr"])
                    mem_g_info = gr.Markdown(_mem_g_text(), elem_classes=["hdr"])
                    btn_mem_g = gr.Button("Globální paměť — otevřít", size="sm")
                    mem_p_info = gr.Markdown(_mem_p_text(), elem_classes=["hdr"])
                    btn_mem_p = gr.Button("Projektová paměť — otevřít", size="sm")

                gr.Markdown("<small class='side-h'>📁 PROJEKTY</small>", elem_classes=["hdr"])
                proj_dd = gr.Dropdown(choices=project_choices(), value=current_project_name(),
                                      interactive=True, show_label=False, container=False,
                                      elem_id="proj-dd", info=None)
                with gr.Row(elem_classes=["gap"]):
                    btn_proj_new = gr.Button("+ nový projekt", size="sm", scale=1, elem_classes=["sqsm"])
                    btn_proj_attach = gr.Button("připojit adresář", size="sm", scale=1, elem_classes=["sqsm"])
                with gr.Row(visible=False) as proj_new_row:
                    proj_new_tb = gr.Textbox(placeholder="název nového projektu…",
                                             show_label=False, container=False, scale=3)
                    btn_proj_create = gr.Button("OK", variant="primary", size="sm", scale=1)

                gr.Markdown("<small class='side-h'>💬 CHATY PROJEKTU</small>", elem_classes=["hdr"])
                chats_radio = gr.Radio(choices=chat_choices(), value=state.session.id,
                                       show_label=False, container=False, elem_id="chats-radio",
                                       info=None)

                gr.Markdown("<small class='side-h'>💬 CHATY BEZ PROJEKTU</small>", elem_classes=["hdr"])
                noproj_radio = gr.Radio(choices=noproj_chat_choices(), value=None,
                                        show_label=False, container=False, elem_id="noproj-radio",
                                        info=None)

                gr.Markdown("<small class='side-h'>🔧 AKTIVNÍ CHAT</small>", elem_classes=["hdr"])
                with gr.Row(elem_classes=["gap"]):
                    btn_new = gr.Button("+ Nový chat", size="sm", scale=2, elem_classes=["sqsm"])
                    btn_del_chat = gr.Button("Smaž chat", size="sm", scale=1, elem_classes=["sqsm"])
                del_state = gr.Markdown("", elem_id="del-state", elem_classes=["hdr"])
                with gr.Row(elem_classes=["gap"]):
                    rename_tb = gr.Textbox(placeholder="přejmenovat aktuální…", show_label=False,
                                           container=False, scale=3)
                    btn_rename = gr.Button("Uložit", size="sm", scale=1, elem_classes=["sqsm"])

            # ================= HLAVNÍ CHAT =================
            with gr.Column(scale=5, elem_id="main"):
                chat = gr.Chatbot(value=chat_view(), show_label=False, height=560,
                                  render_markdown=True, elem_id="main-chat")
                with gr.Row(elem_classes=["gap"]):
                    msg_in = gr.Textbox(
                        placeholder="Napiš zprávu…  (Enter / Ctrl+Enter = odeslat, Shift+Enter = nový řádek)",
                        show_label=False, container=False, lines=1, max_lines=8,
                        elem_id="msg-in", scale=6)
                    btn_send = gr.Button("Odeslat", variant="primary", size="sm", min_width=52,
                                         elem_id="btn-send")
                    btn_stop_run = gr.Button("Stop", size="sm", min_width=40)
                files_in = gr.File(label=None, show_label=False, container=False,
                                   file_count="multiple", file_types=["image"], type="filepath",
                                   elem_id="files-in")
                with gr.Row(visible=False) as confirm_row:
                    gr.Markdown("⚠️ **Agent čeká na potvrzení akce**", scale=3)
                    btn_yes = gr.Button("Povolit", variant="primary", size="sm", scale=1)
                    btn_no = gr.Button("Zamítnout", variant="stop", size="sm", scale=1)

        # ---------------- události ----------------
        # projekty
        proj_dd.change(set_project_handler, proj_dd, proj_dd, queue=False)\
            .then(update_chats_radio, None, [chats_radio, noproj_radio, del_state], queue=False)
        btn_proj_attach.click(attach_project_handler, None,
                              [proj_dd, proj_new_row], queue=False)\
            .then(update_chats_radio, None, [chats_radio, noproj_radio, del_state], queue=False)
        btn_proj_new.click(lambda: gr.update(visible=True), None, proj_new_row, queue=False)
        btn_proj_create.click(create_project_handler, proj_new_tb,
                              [proj_dd, proj_new_row, proj_new_tb], queue=False)\
            .then(update_chats_radio, None, [chats_radio, noproj_radio, del_state], queue=False)

        # chaty (radio = přepnutí chatu; druhé radio = chaty bez projektu)
        chats_radio.input(load_session_handler, chats_radio,
                           [chat, confirm_row, status_box, proj_dd], queue=True)\
            .then(update_chats_radio, None, [chats_radio, noproj_radio, del_state], queue=False)
        noproj_radio.input(load_session_handler, noproj_radio,
                            [chat, confirm_row, status_box, proj_dd], queue=True)\
            .then(update_chats_radio, None, [chats_radio, noproj_radio, del_state], queue=False)
        btn_new.click(new_chat, None, [chat, confirm_row, status_box])\
            .then(update_chats_radio, None, [chats_radio, noproj_radio, del_state], queue=False)
        btn_del_chat.click(delete_current_chat, None,
                           [chat, chats_radio, noproj_radio, status_box, del_state], queue=False)
        btn_rename.click(rename_session, rename_tb,
                         [rename_tb, chats_radio, noproj_radio, del_state], queue=False)

        # paměť (otevřít v editoru)
        btn_mem_g.click(lambda: open_in_editor(_memory_paths().global_path),
                        None, mem_g_info, queue=False)
        btn_mem_p.click(lambda: (open_in_editor(_memory_paths().project_path())
                                 if _memory_paths().project_path() else "Nejdřív vyber projekt"),
                        None, mem_p_info, queue=False)

        # chat zprávy
        btn_send.click(send_message, [msg_in, files_in, chat],
                       [chat, confirm_row, status_box], queue=True)\
            .then(_clear_inputs, None, [msg_in, files_in])\
            .then(update_chats_radio, None, [chats_radio, noproj_radio, del_state], queue=False)
        msg_in.submit(send_message, [msg_in, files_in, chat],
                      [chat, confirm_row, status_box], queue=True)\
            .then(_clear_inputs, None, [msg_in, files_in])\
            .then(update_chats_radio, None, [chats_radio, noproj_radio, del_state], queue=False)
        btn_yes.click(confirm_yes, chat, [chat, confirm_row, status_box], queue=True)
        btn_no.click(confirm_no, chat, [chat, confirm_row, status_box], queue=True)
        btn_stop_run.click(stop_run, chat, [chat, confirm_row, status_box], queue=True)
        btn_handoff.click(handoff_to_new_session, None,
                          [chat, confirm_row, status_box], queue=True)\
            .then(update_chats_radio, None, [chats_radio, noproj_radio, del_state], queue=False)
        btn_compress.click(compress_now, chat, [chat, confirm_row, status_box], queue=True)
        model_dd.change(change_model, model_dd, status_box)
        mode_dd.change(change_mode, mode_dd, settings_info)
        autonomy_dd.change(change_autonomy, autonomy_dd, settings_info)
        thinking_dd.change(change_thinking, thinking_dd, settings_info)
        btn_start.click(lambda: server_cmd("start"), None, status_box)
        btn_stop.click(lambda: server_cmd("stop"), None, status_box)
        btn_refresh.click(lambda: server_cmd("restart"), None, status_box)

        gr.Markdown("<small>🛡️ FAILSAFE: myš do levého horního rohu přeruší GUI akce · "
                    "čtecí příkazy bez potvrzení · vše lokálně</small>", elem_classes=["hdr"],
                    elem_id="footer-hint")

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
            return (chat_view(), gr.update(visible=False), refresh_status(), r1, r2, ds)

        ui.load(on_page_load, None,
                [chat, confirm_row, status_box, chats_radio, noproj_radio, del_state])

        # živý status (⏳ načítám model → 🟢) každých 5 s
        if hasattr(gr, "Timer"):
            gr.Timer(5.0).tick(refresh_status, outputs=status_box)
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
    port = int(cfg.web["port"])

    if _port_busy(host, port):
        if _is_our_webui(host, port):
            url = f"http://{host}:{port}"
            print(f"[INFO] Web UI už běží na {url} — nespouštím druhou instanci, otevírám prohlížeč.")
            if not os.environ.get("QWEN_NO_BROWSER"):
                webbrowser.open(url)
            sys.exit(0)
        # port drží cizí proces → najdi nejbližší volný
        new_port = port
        while _port_busy(host, new_port) and new_port < port + 20:
            new_port += 1
        print(f"[INFO] Port {port} je obsazený cizím procesem — Web UI spouštím na portu {new_port}.")
        port = new_port

    build_ui().launch(
        server_name=host,
        server_port=port,
        css=CUSTOM_CSS,
        show_error=True,  # detail chyb při ladění (jen localhost)
        inbrowser=not os.environ.get("QWEN_NO_BROWSER"),
        allowed_paths=[str(cfg.path("paths.sessions_dir"))],
    )
