"""Webové UI pro Qwen3.8-27B harness (Gradio, pouze localhost).

Spuštění:  .venv/Scripts/python webapp.py  →  http://127.0.0.1:7860
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

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
        cfg.data["thinking"] = bool(self.thinking)
        self.workspace = saved.get("workspace") or cfg.agent.get("workspace")
        self.recent_ws: list[str] = saved.get("recent", [])
        if self.workspace:
            cfg.agent["workspace"] = self.workspace  # převezme každý nový Agent
        self._restore_session()

    def _restore_session(self) -> None:
        """Načti poslední session (pokud existuje), jinak nová."""
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
        })

    def new_session(self) -> None:
        self.session = Session(cfg, system_prompt=self._system_prompt())
        self.rebuild_agent()

    def rebuild_agent(self) -> None:
        safety = SafetyPolicy(
            autonomy=self.autonomy,
            max_steps=int(cfg.agent.get("max_steps", 40)),
            semi_max_steps=int(cfg.agent.get("semi_max_steps", 15)),
        )
        self.abort = threading.Event()
        self.agent = Agent(cfg, llm, self.session, build_registry(self.mode),
                           safety, mode=self.mode, abort_flag=self.abort)
        if self.workspace:
            try:
                self.agent.set_workspace(self.workspace)
            except ValueError:
                pass

    def _system_prompt(self) -> str:
        base = system_prompt(self.mode)
        if self.workspace:
            base += (f"\n\nCurrent project workspace: {self.workspace}. "
                     f"Relative paths in tools resolve against it. "
                     f"The user keeps project sources and documents there - read them with tools "
                     f"instead of asking the user to paste content.")
        return base

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
        return p


state = AppState()


# ------------------------------------------------------------- render helpers
def chat_view() -> list[dict]:
    """Převeď session messages do formátu gr.Chatbot (type='messages')."""
    out = []
    for m in state.session.messages:
        role = m["role"]
        if role == "system" or (role == "assistant" and not m.get("content")):
            continue
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
    """Společný generátor: krokuj agentem dokud FINAL/NEEDS_CONFIRMATION/stop.

    Výjimky zachytává a vrací jako zprávu v chatu (nikdy nenechá spadnout UI).
    `approve` platí jen pro první krok (schválení čekajících akcí).
    """
    try:
        first = True
        while True:
            r = state.agent.step(approve=approve if first else None)
            first = False
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


def session_choices() -> list[str]:
    return [f"{s['id']}  ({s['messages']} zpráv)" for s in Session.list_sessions(cfg)[:15]]


def load_session_handler(selection: str):
    """Načte starou session podle výběru z dropdownu."""
    try:
        if not selection:
            yield chat_view(), gr.update(visible=False), refresh_status()
            return
        sid = selection.split("  (")[0].strip()
        state.session = Session.load(cfg, sid, state._system_prompt())
        state.rebuild_agent()
        gr.Info(f"✅ Session načtena: {sid}")
        yield chat_view(), gr.update(visible=False), refresh_status()
    except Exception as e:
        gr.Warning(f"❌ {e}")
        yield chat_view(), gr.update(visible=False), refresh_status()


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


def change_thinking(on: bool):
    state.thinking = on
    cfg.data["thinking"] = on
    state.save_ui_state()
    return f"Thinking: **{'on' if on else 'off'}**"


def refresh_status():
    if servermgmt.health(cfg):
        s = f"🟢 {servermgmt.running_model(cfg) or state.model_key} · {servermgmt.vram_str()}"
    else:
        s = "🔴 server stojí (▶ start)"
    # ukazatel kontextu
    try:
        est = state.session.estimate_context_tokens()
        limit = int(cfg.model().get("ctx_size", 32768))
        pct = min(100, est * 100 // max(limit, 1))
        warn = " 🟠" if pct >= 70 else (" 🔴" if pct >= 85 else "")
        s += f" · 📊 ctx ~{est / 1000:.1f}k/{limit // 1000}k{warn}"
    except Exception:
        pass
    return s


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
    return refresh_status()


# ------------------------------------------------------------- UI
CUSTOM_CSS = """
.gradio-container { max-width: 1500px !important; padding: 8px 12px !important; }
/* chat přes (téměř) celou výšku okna */
#main-chat { height: calc(100vh - 216px) !important; min-height: 340px !important; }
/* tenký vstup */
#msg-in textarea { min-height: 40px !important; max-height: 110px !important; }
/* kompaktní upload obrázků */
#files-in { max-height: 72px !important; overflow-y: auto !important; }
#files-in .wrap { padding: 4px !important; min-height: 0 !important; }
/* drobná hlavička */
.hdr p { margin: 0 !important; font-size: 0.9em !important; }
/* menší mezery mezi prvky */
.gap { gap: 4px !important; }
"""


def build_ui() -> gr.Blocks:
    model_choices = list(cfg.data["models"].keys())
    with gr.Blocks(title="Qwen3.8-27B lokální harness") as ui:
        # --- hlavička: titulek + stav + server tlačítka (jedna řádka) ---
        with gr.Row(elem_classes=["hdr", "gap"]):
            gr.Markdown("🤖 **Qwen3.8-27B**", elem_classes=["hdr"], scale=1, min_width=120)
            status_box = gr.Markdown(refresh_status, elem_classes=["hdr"], scale=4)
            btn_start = gr.Button("▶", size="sm", min_width=36)
            btn_stop = gr.Button("⏹", size="sm", min_width=36)
            btn_refresh = gr.Button("🔄", size="sm", min_width=36)
            btn_handoff = gr.Button("📦 Předej", size="sm", min_width=80)
            btn_new = gr.Button("🆕", size="sm", min_width=36)

        # --- workspace: jedna řádka (dropdown = naposledy použité + ruční cesta) ---
        with gr.Row(elem_classes=["gap"]):
            ws_pick = gr.Dropdown(
                choices=state.recent_ws or [],
                value=state.workspace,
                allow_custom_value=True, interactive=True, filterable=True,
                show_label=False, container=False, scale=5,
                info="📁 Složka projektu — vyber z nedávných, napiš cestu, nebo klikni na 📂 Vybrat",
                elem_id="ws-pick")
            btn_ws_browse = gr.Button("📂 Vybrat složku", size="sm", min_width=110)

        # --- chat (hlavní plocha; na startu obnovená poslední session) ---
        chat = gr.Chatbot(value=chat_view(), label=None, show_label=False, height=600,
                          render_markdown=True, elem_id="main-chat")

        # --- vstup: tenký textbox + tlačítka (jedna řádka) ---
        with gr.Row(elem_classes=["gap"]):
            msg_in = gr.Textbox(
                placeholder="Napiš zprávu…  (Enter / Ctrl+Enter = odeslat, Shift+Enter = nový řádek)",
                show_label=False, container=False, lines=1, max_lines=8,
                elem_id="msg-in", scale=6)
            btn_send = gr.Button("📨", variant="primary", size="sm", min_width=48,
                                 elem_id="btn-send")
            btn_stop_run = gr.Button("⏹", size="sm", min_width=36)

        files_in = gr.File(label=None, show_label=False, container=False,
                           file_count="multiple", file_types=["image"], type="filepath",
                           elem_id="files-in")

        # --- potvrzovací lišta (skrytá, dokud agent nečeká na souhlas) ---
        with gr.Row(visible=False) as confirm_row:
            gr.Markdown("⚠️ **Agent čeká na potvrzení akce** (ovládání PC / zápisy)", scale=4)
            btn_yes = gr.Button("✅ Povolit", variant="primary", size="sm", scale=1)
            btn_no = gr.Button("❌ Zamítnout", variant="stop", size="sm", scale=1)

        # --- nastavení (sbalené) ---
        with gr.Accordion("⚙️ Nastavení — model / režim / autonomie / thinking / sessions", open=False):
            with gr.Row():
                model_dd = gr.Dropdown(model_choices, value=state.model_key,
                                       label="Model (přepnutí = restart serveru)")
                mode_dd = gr.Dropdown(["chat", "agent", "computer"], value=state.mode,
                                      label="Režim")
                autonomy_dd = gr.Dropdown(["supervised", "semi", "auto"], value=state.autonomy,
                                          label="Autonomie")
                thinking_cb = gr.Checkbox(value=state.thinking, label="Thinking režim")
            with gr.Row():
                sessions_dd = gr.Dropdown(choices=session_choices(), label="Načíst starou session",
                                          interactive=True, scale=4)
                btn_load_session = gr.Button("📂 Načíst", size="sm", scale=1)
            settings_info = gr.Markdown("")

        # události - workspace
        # nativní dialog: queue=False, aby nezablokoval chat během otevřeného okna
        btn_ws_browse.click(browse_workspace, None, ws_pick, queue=False)
        ws_pick.change(set_workspace_handler, ws_pick, ws_pick, queue=False)

        # události - chat
        btn_send.click(send_message, [msg_in, files_in, chat],
                       [chat, confirm_row, status_box], queue=True)\
            .then(lambda: ("", None), None, [msg_in, files_in])
        msg_in.submit(send_message, [msg_in, files_in, chat],
                      [chat, confirm_row, status_box], queue=True)\
            .then(lambda: ("", None), None, [msg_in, files_in])
        btn_yes.click(confirm_yes, chat, [chat, confirm_row, status_box], queue=True)
        btn_no.click(confirm_no, chat, [chat, confirm_row, status_box], queue=True)
        btn_stop_run.click(stop_run, chat, [chat, confirm_row, status_box], queue=True)
        btn_new.click(new_chat, None, [chat, confirm_row, status_box])\
            .then(lambda: gr.update(choices=session_choices()), None, sessions_dd)
        btn_handoff.click(handoff_to_new_session, None, [chat, confirm_row, status_box], queue=True)\
            .then(lambda: gr.update(choices=session_choices()), None, sessions_dd)
        btn_load_session.click(load_session_handler, sessions_dd, [chat, confirm_row, status_box])\
            .then(lambda: gr.update(choices=session_choices()), None, sessions_dd)
        model_dd.change(change_model, model_dd, status_box)
        mode_dd.change(change_mode, mode_dd, settings_info)
        autonomy_dd.change(change_autonomy, autonomy_dd, settings_info)
        thinking_cb.change(change_thinking, thinking_cb, settings_info)
        btn_start.click(lambda: server_cmd("start"), None, status_box)
        btn_stop.click(lambda: server_cmd("stop"), None, status_box)
        btn_refresh.click(refresh_status, None, status_box)

        gr.Markdown("<small>🛡️ FAILSAFE: myš do levého horního rohu obrazovky přeruší GUI akce · "
                    "čtecí příkazy nevyžadují potvrzení · vše běží lokálně</small>",
                    elem_classes=["hdr"])

        # Ctrl+Enter odesílá zprávu (vedle klasického Enteru)
        ui.load(None, None, None, js="""
        () => {
          document.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
              const btn = document.getElementById('btn-send');
              if (btn) { e.preventDefault(); btn.click(); }
            }
          });
        }
        """)
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
