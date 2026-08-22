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
class AppState:
    def __init__(self) -> None:
        self.model_key = cfg.model_key()
        self.mode = cfg.agent.get("mode", "agent")
        self.autonomy = cfg.agent.get("autonomy", "supervised")
        self.thinking = bool(cfg.data.get("thinking", True))
        self.new_session()

    def new_session(self) -> None:
        self.session = Session(cfg, system_prompt=system_prompt(self.mode))
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

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.rebuild_agent()
        if self.session.messages and self.session.messages[0]["role"] == "system":
            self.session.messages[0]["content"] = system_prompt(mode)


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
                yield history, gr.update(visible=False), gr.update(visible=True)
            elif r.status is Status.FINAL:
                history.append({"role": "assistant", "content": r.text or "…"})
                yield history, gr.update(visible=False), gr.update(visible=True)
                return
            elif r.status is Status.NEEDS_CONFIRMATION:
                lines = "\n".join(f"⚠️ `{a}`" for a in r.pending_summary)
                history.append({"role": "assistant",
                                "content": f"**Čekám na potvrzení akce:**\n{lines}"})
                yield history, gr.update(visible=True), gr.update(visible=False)
                return
            else:  # ABORTED / ERROR
                text = _agent_error_message(r) if r.status is Status.ERROR else f"⛔ {r.text}"
                history.append({"role": "assistant", "content": text})
                yield history, gr.update(visible=False), gr.update(visible=True)
                return
    except Exception as e:  # pojistka - žádné spadnutí UI
        history.append({"role": "assistant", "content": _error_message(e)})
        yield history, gr.update(visible=False), gr.update(visible=True)


# ------------------------------------------------------------- handlery
def send_message(message: str, files, history: list[dict]):
    try:
        if not (message or "").strip() and not files:
            yield history, gr.update(visible=False), gr.update(visible=True)
            return
        cfg.data["thinking"] = state.thinking
        imgs = [Path(f) for f in (files or []) if Path(f).suffix.lower() in IMG_MIMES]
        shown = (message.strip() or "") + (f"\n🖼️ +{len(imgs)} obrázek(ky)" if imgs else "")
        history.append({"role": "user", "content": shown})
        yield history, gr.update(visible=False), gr.update(visible=False)
        state.agent.new_task(message.strip() or "Please analyze the attached image(s).", images=imgs)
        yield from _run_steps(history)
    except Exception as e:
        history.append({"role": "assistant", "content": _error_message(e)})
        yield history, gr.update(visible=False), gr.update(visible=True)


def confirm(approve: bool, history: list[dict]):
    """Reakce na tlačítka Povolit/Zamítnout."""
    try:
        if not state.agent._pending:
            # není co potvrzovat (např. po dvojkliku) - jen obnov vstup
            if history and _is_pending_question(history[-1]):
                history.pop()
            yield history, gr.update(visible=False), gr.update(visible=True)
            return
        # odeber zprávu s dotazem a zaloguj rozhodnutí uživatele
        if history and _is_pending_question(history[-1]):
            history.pop()
        history.append({"role": "user", "content": "✅ Povolit" if approve else "❌ Zamítnout"})
        yield history, gr.update(visible=False), gr.update(visible=False)
        yield from _run_steps(history, approve=approve)
    except Exception as e:
        history.append({"role": "assistant", "content": _error_message(e)})
        yield history, gr.update(visible=False), gr.update(visible=True)


def confirm_yes(history: list[dict]):
    """btn_yes handler - MUSÍ být generátor (Gradio iteruje yieldy)."""
    yield from confirm(True, history)


def confirm_no(history: list[dict]):
    """btn_no handler - MUSÍ být generátor."""
    yield from confirm(False, history)


def stop_run(history: list[dict]):
    state.abort.set()
    yield history, gr.update(visible=False), gr.update(visible=True)


def new_chat():
    state.new_session()
    return [], gr.update(visible=False), gr.update(visible=True)


def change_model(key: str):
    if servermgmt.ensure(cfg, key):
        state.model_key = key
        return f"✅ Model **{key}** běží ({servermgmt.vram_str()})"
    return "❌ Přepnutí modelu selhalo (viz runtime/llama-server.log)"


def change_mode(mode: str):
    state.set_mode(mode)
    return f"Režim: **{mode}**"


def change_autonomy(a: str):
    state.autonomy = a
    state.rebuild_agent()
    return f"Autonomie: **{a}**"


def change_thinking(on: bool):
    state.thinking = on
    cfg.data["thinking"] = on
    return f"Thinking: **{'on' if on else 'off'}**"


def refresh_status():
    if servermgmt.health(cfg):
        return f"🟢 llama-server běží · model: {servermgmt.running_model(cfg)} · {servermgmt.vram_str()}"
    return "🔴 llama-server stojí — spusť: python scripts/server.py start"


def server_cmd(cmd: str):
    if cmd == "start":
        return change_model(state.model_key)
    if cmd == "stop":
        servermgmt.stop(cfg, quiet=True)
    return refresh_status()


# ------------------------------------------------------------- UI
def build_ui() -> gr.Blocks:
    model_choices = list(cfg.data["models"].keys())
    with gr.Blocks(title="Qwen3.8-27B lokální harness") as ui:
        gr.Markdown("## 🤖 Qwen3.8-27B — lokální harness (RTX 5090)")
        with gr.Row():
            with gr.Column(scale=3):
                status_box = gr.Markdown(refresh_status)
            with gr.Column(scale=2):
                with gr.Row():
                    btn_start = gr.Button("▶ Start serveru", size="sm")
                    btn_stop = gr.Button("⏹ Stop", size="sm")
                    btn_refresh = gr.Button("🔄", size="sm")

        chat = gr.Chatbot(height=480, label="Konverzace", render_markdown=True)

        with gr.Row(visible=True) as input_row:
            with gr.Column(scale=5):
                msg_in = gr.Textbox(placeholder="Napiš zprávu… (Enter = odeslat, Shift+Enter = nový řádek)",
                                    label="Zpráva", lines=2)
            with gr.Column(scale=2):
                files_in = gr.File(label="Obrázky", file_count="multiple",
                                   file_types=["image"], type="filepath")
                btn_send = gr.Button("📨 Odeslat", variant="primary", elem_id="btn-send")
                btn_stop_run = gr.Button("⏹ Přerušit")

        with gr.Row(visible=False) as confirm_row:
            gr.Markdown("⚠️ **Agent čeká na potvrzení akce** (souhlas s ovládáním PC / zápisy)")
            btn_yes = gr.Button("✅ Povolit", variant="primary")
            btn_no = gr.Button("❌ Zamítnout", variant="stop")

        with gr.Accordion("⚙️ Nastavení", open=False):
            with gr.Row():
                model_dd = gr.Dropdown(model_choices, value=state.model_key,
                                       label="Model (přepnutí = restart serveru)")
                mode_dd = gr.Dropdown(["chat", "agent", "computer"], value=state.mode,
                                      label="Režim")
                autonomy_dd = gr.Dropdown(["supervised", "semi", "auto"], value=state.autonomy,
                                          label="Autonomie")
                thinking_cb = gr.Checkbox(value=state.thinking, label="Thinking režim")
                btn_new = gr.Button("🆕 Nová session")
            settings_info = gr.Markdown("")

        # události
        btn_send.click(send_message, [msg_in, files_in, chat],
                       [chat, confirm_row, input_row], queue=True)\
            .then(lambda: ("", None), None, [msg_in, files_in])
        msg_in.submit(send_message, [msg_in, files_in, chat],
                      [chat, confirm_row, input_row], queue=True)\
            .then(lambda: ("", None), None, [msg_in, files_in])
        btn_yes.click(confirm_yes, chat, [chat, confirm_row, input_row], queue=True)
        btn_no.click(confirm_no, chat, [chat, confirm_row, input_row], queue=True)
        btn_stop_run.click(stop_run, chat, [chat, confirm_row, input_row], queue=True)
        btn_new.click(new_chat, None, [chat, confirm_row, input_row])
        model_dd.change(change_model, model_dd, status_box)
        mode_dd.change(change_mode, mode_dd, settings_info)
        autonomy_dd.change(change_autonomy, autonomy_dd, settings_info)
        thinking_cb.change(change_thinking, thinking_cb, settings_info)
        btn_start.click(lambda: server_cmd("start"), None, status_box)
        btn_stop.click(lambda: server_cmd("stop"), None, status_box)
        btn_refresh.click(refresh_status, None, status_box)

        gr.Markdown("🛡️ **FAILSAFE:** myš do levého horního rohu obrazovky okamžitě přeruší GUI akce. "
                    "V režimu `supervised` se každá akce potvrzuje. Vše běží lokálně.")

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


if __name__ == "__main__":
    import os
    build_ui().launch(
        server_name=cfg.web["host"],
        server_port=int(cfg.web["port"]),
        show_error=True,  # detail chyb při ladění (jen localhost)
        inbrowser=not os.environ.get("QWEN_NO_BROWSER"),
        allowed_paths=[str(cfg.path("paths.sessions_dir"))],
    )
