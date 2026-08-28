"""Terminálové UI pro Qwen3.8-27B harness.

Spuštění:  .venv/Scripts/python tui.py
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# jazyk UI před module-level texty (BANNER/HELP)
from harness.i18n import detect_language, set_language, t
set_language(detect_language(ROOT) or "en")

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from harness.agent import Agent, Status, build_registry
from harness.config import load_config
from harness.llm import LLMClient
from harness.prompts import build_system_prompt
from harness.safety import SafetyPolicy
from harness.session import Session
from harness.work_modes import WORK_MODES, normalize_work_mode
from harness.version import APP_VERSION

console = Console()

BANNER = (f"[bold cyan]Marvin v{APP_VERSION}"
          f"{t('  •  local harness  •  RTX 5090')}[/bold cyan]")

HELP = t("[bold]Commands:[/bold]\n"
         "  /memory                show persistent memory (global + mode + project)\n"
         "  /model q4|q5           switch model (server restart)\n"
         "  /work discussion|research|writing|development|computer   work mode\n"
         "  /mode chat|agent|computer     compatibility shortcut\n"
         "  /autonomy supervised|semi|auto   autonomy level\n"
         "  /thinking xhigh|medium|low|off   model reasoning depth\n"
         "  /img <path>           attach an image to the next message\n"
         "  /screenshot            attach a screen capture\n"
         "  /new                   new session   /sessions  list   /load <id>\n"
         "  /server status|start|stop    inference server management\n"
         "  /help                  this help  /exit  quit\n"
         "Input: Enter send  •  Ctrl+C interrupt generation")


class TUIApp:
    def __init__(self) -> None:
        self.cfg = load_config()
        self.model_key = self.cfg.model_key()
        self.work_mode = normalize_work_mode(
            self.cfg.data.get("work_mode"), self.cfg.agent.get("mode", "agent"))
        self.mode = WORK_MODES[self.work_mode].agent_mode
        self.autonomy = self.cfg.agent.get("autonomy", "supervised")
        self.thinking = bool(self.cfg.data.get("thinking", True))
        self.reasoning_effort = self.cfg.data.get("reasoning_effort", "xhigh")
        self.auto_approve = False  # "a" v potvrzení = schvalovat vše do konce úlohy
        self.session: Session | None = None
        self.agent: Agent | None = None
        self.llm = LLMClient(self.cfg)
        self.pending_images: list[Path] = []
        self.abort = threading.Event()
        self._new_session()

    # ------------------------------------------------------------------
    def _new_session(self) -> None:
        self.session = Session(
            self.cfg,
            system_prompt=build_system_prompt(
                self.mode, self.cfg, getattr(self.agent, "workspace", None), self.work_mode),
            work_mode=self.work_mode)
        self.abort = threading.Event()
        self.auto_approve = False
        self._rebuild_agent()

    def _rebuild_agent(self) -> None:
        safety = SafetyPolicy(
            autonomy=self.autonomy,
        )
        self.agent = Agent(
            self.cfg, self.llm, self.session, build_registry(self.mode, self.work_mode),
            safety, mode=self.mode, on_event=self._on_event, abort_flag=self.abort,
            work_mode=self.work_mode,
        )

    def _set_mode(self, mode: str) -> None:
        self._set_work_mode(normalize_work_mode(None, mode))

    def _set_work_mode(self, work_mode: str) -> None:
        self.work_mode = normalize_work_mode(work_mode, self.mode)
        self.mode = WORK_MODES[self.work_mode].agent_mode
        self.cfg.data["work_mode"] = self.work_mode
        self.cfg.agent["mode"] = self.mode
        self.session.meta["work_mode"] = self.work_mode
        self.session._save_meta()
        self._rebuild_agent()
        # aktualizuj system prompt v session
        if self.session.messages and self.session.messages[0]["role"] == "system":
            self.session.messages[0]["content"] = build_system_prompt(
                self.mode, self.cfg, getattr(self.agent, "workspace", None), self.work_mode)
        self.auto_approve = False

    # ------------------------------------------------------------------
    def status_line(self) -> str:
        think = "[red]off[/red]" if not self.thinking else f"[green]{self.reasoning_effort}[/green]"
        ws = self.agent.workspace if self.agent else Path.cwd()
        try:
            est = self.agent.estimate_context_tokens()
            limit = self.cfg.context_size()
            ctx = f"  [bold]ctx[/bold]=~{est / 1000:.1f}k/{limit // 1000}k"
        except Exception:
            ctx = ""
        return (f"[bold]model[/bold]={self.model_key}  "
                f"[bold]{t('mode')}[/bold]={t(WORK_MODES[self.work_mode].label)}  "
                f"[bold]{t('autonomy')}[/bold]={self.autonomy}  [bold]thinking[/bold]={think}{ctx}\n"
                f"[bold]workspace[/bold]={ws}")

    def _on_event(self, kind: str, payload) -> None:
        if kind == "text":
            console.print(payload, end="", markup=False, highlight=False)
        elif kind == "reasoning":
            console.print(payload, end="", markup=False, highlight=False, style="dim")
        elif kind == "tool_start":
            name, args = payload
            short = ""
            if args:
                short = ", ".join(f"{k}={str(v)[:40]!r}" for k, v in list(args.items())[:4])
            console.print(f"\n[cyan]▸ {name}[/cyan][dim]({short})[/dim]", markup=True, highlight=False)
        elif kind == "tool_result":
            name, result = payload
            res = result if len(result) <= 500 else result[:500] + " …"
            console.print(f"[dim]  ↳ {res}[/dim]", markup=False, highlight=False)

    def _confirm(self, actions: list[str]) -> bool:
        console.print(Panel("\n".join(f"[yellow]• {a}[/yellow]" for a in actions),
                           title=f"[bold red]{t('⚠ Action confirmation')}[/bold red]",
                           subtitle=f"{t('autonomy')}: {self.autonomy}"))
        if self.auto_approve:
            note = t("(approved automatically - 'a' in a previous confirmation)")
            console.print(f"[dim]{note}[/dim]")
            return True
        ans = Prompt.ask(t("  Allow?  [y/n/a]  (a = all until the end of the task)"),
                         choices=["y", "n", "a"], default="n")
        if ans == "a":
            self.auto_approve = True
            return True
        return ans == "y"

    # ------------------------------------------------------------------
    def handle_turn(self, text: str) -> None:
        self.abort.clear()
        self.cfg.data["thinking"] = self.thinking
        images = list(self.pending_images)
        self.pending_images.clear()
        try:
            for result in self.agent.run(text, images=images, confirm_cb=self._confirm):
                if result.status is Status.ERROR:
                    console.print(f"\n[bold red]{t('ERROR:')}[/bold red] {result.text}")
                    if "Connection" in result.text or "health" in result.text:
                        console.print(f"[dim]{t('Try /server start')}[/dim]")
                    return
                if result.status is Status.ABORTED:
                    console.print(f"\n[bold yellow]⛔ {result.text}[/bold yellow]")
                    return
                if result.status is Status.NEEDS_CONFIRMATION:
                    continue  # potvrzeno přes callback v run()
                if result.status is Status.CONTINUE:
                    continue
                # FINAL - text už byl streamován
                console.print("\n")
        except KeyboardInterrupt:
            self.abort.set()
            console.print(f"\n[bold yellow]{t('⛔ Interrupted (Ctrl+C)')}[/bold yellow]")
            # doruč zprávu o přerušení do session, aby model věděl kontext
            # (user role - Qwen šablona neumí system uprostřed konverzace)
            self.session.add("user", "[Interrupted by user]")

    # ------------------------------------------------------------------
    def cmd_model(self, key: str) -> None:
        if key not in self.cfg.data["models"]:
            console.print("[red]" + t("Unknown model '{key}'. Available: {models}",
                                      key=key, models=", ".join(self.cfg.data["models"])) + "[/red]")
            return
        from harness import servermgmt
        with console.status("[bold]" + t("Switching to model '{key}' (llama-server restart)...",
                                         key=key) + "[/bold]"):
            ok = servermgmt.ensure(self.cfg, key)
        if ok:
            self.model_key = key
            self.cfg.data["default_model"] = key  # ctx limit sleduje model
            console.print("[green]" + t("✓ Model {key} is running.", key=key) + "[/green]")
            console.print(self.status_line())
        else:
            console.print(f"[red]{t('Switch failed - see runtime/llama-server.log')}[/red]")

    def cmd_server(self, sub: str) -> None:
        from harness import servermgmt
        if sub == "start":
            servermgmt.start(self.cfg, self.model_key)
        elif sub == "stop":
            servermgmt.stop(self.cfg)
        else:
            servermgmt.status(self.cfg)

    # ------------------------------------------------------------------
    def run(self) -> None:
        console.print(BANNER)
        console.print(HELP + "\n")
        console.print(self.status_line())
        console.print(f"[dim]session: {self.session.id}  ({self.session.dir})[/dim]\n")

        # server check
        from harness import servermgmt
        if not servermgmt.health(self.cfg):
            console.print(f"[yellow]{t('⚠ llama-server is not running.')}[/yellow]")
            if Prompt.ask(t("  Start now?"), choices=["y", "n"], default="y") == "y":
                if servermgmt.start(self.cfg, self.model_key) != 0:
                    console.print(f"[red]{t('Server failed to start - try /server start later.')}[/red]")
            console.print()

        # vstup: prompt_toolkit (historie, editace), fallback na input() mimo TTY
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import FileHistory
            history = FileHistory(str(ROOT / ".tui_history"))
            pt = PromptSession(history=history)

            def read_line() -> str:
                return pt.prompt("› ")
        except Exception:
            def read_line() -> str:
                return input("› ")

        while True:
            try:
                line = read_line()
            except (KeyboardInterrupt, EOFError):
                console.print(f"\n[dim]{t('Goodbye!')}[/dim]")
                break
            line = line.strip()
            if not line:
                continue

            # slash příkazy
            if line.startswith("/"):
                parts = line.split(maxsplit=2)
                cmd = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else ""
                try:
                    if cmd == "/exit":
                        break
                    elif cmd == "/help":
                        console.print(HELP)
                    elif cmd == "/model":
                        self.cmd_model(arg or self.model_key)
                    elif cmd == "/mode":
                        if arg in ("chat", "agent", "computer"):
                            self._set_mode(arg)
                            console.print(self.status_line())
                        else:
                            console.print(f"[red]{t('Usage: /mode chat|agent|computer')}[/red]")
                    elif cmd == "/work":
                        if arg in WORK_MODES:
                            self._set_work_mode(arg)
                            console.print(self.status_line())
                        else:
                            console.print(f"[red]{t('Usage: /work discussion|research|writing|development|computer')}[/red]")
                    elif cmd == "/autonomy":
                        if arg in ("supervised", "semi", "auto"):
                            self.autonomy = arg
                            self._rebuild_agent()
                            self.auto_approve = False
                            console.print(self.status_line())
                        else:
                            console.print(f"[red]{t('Usage: /autonomy supervised|semi|auto')}[/red]")
                    elif cmd == "/thinking":
                        arg_l = arg.strip().lower()
                        if arg_l in ("xhigh", "medium", "low", "on", "off", ""):
                            if arg_l == "":
                                cur = "off" if not self.thinking else self.reasoning_effort
                                console.print(t("Thinking: {level} (options: xhigh | medium | low | off)",
                                                level=f"[bold]{cur}[/bold]"))
                            elif arg_l == "off":
                                self.thinking = False
                            else:
                                self.thinking = True
                                self.reasoning_effort = arg_l if arg_l != "on" else "xhigh"
                                self.cfg.data["reasoning_effort"] = self.reasoning_effort
                            console.print(self.status_line())
                        else:
                            console.print(f"[red]{t('Usage: /thinking xhigh|medium|low|off')}[/red]")
                    elif cmd == "/ws":
                        if arg:
                            try:
                                p = self.agent.set_workspace(arg)
                                # aktualizuj system prompt v session
                                if self.session.messages and self.session.messages[0]["role"] == "system":
                                    self.session.messages[0]["content"] = build_system_prompt(
                                        self.mode, self.cfg, p, self.work_mode)
                                console.print(f"[green]{t('✓ Workspace set: {path}', path=p)}[/green]")
                            except ValueError as e:
                                console.print(f"[red]{e}[/red]")
                        else:
                            console.print(f"Workspace: [bold]{self.agent.workspace}[/bold]")
                    elif cmd == "/memory":
                        from harness.memory import MemoryStore
                        store = MemoryStore(
                            self.cfg, self.agent.workspace, self.work_mode)
                        console.print(f"[bold]{t('Global memory:')}[/bold] {store.global_path}")
                        console.print(f"[bold]{t('Mode memory ({mode}):', mode=t(WORK_MODES[self.work_mode].label))}[/bold] "
                                      f"{store.mode_path()}")
                        console.print(f"[bold]{t('Project memory:')}[/bold] "
                                      f"{store.project_path() or t('— (set via /ws)')}")
                        console.print(f"[dim]{store.context_block()[:1200]}[/dim]")
                    elif cmd == "/img":
                        p = Path(arg).expanduser()
                        if p.exists():
                            self.pending_images.append(p)
                            console.print(f"[green]{t('✓ Image attached: {name}', name=p.name)}[/green]")
                        else:
                            console.print(f"[red]{t('File not found: {path}', path=p)}[/red]")
                    elif cmd == "/screenshot":
                        from harness.tools.computer import ScreenshotTool
                        from harness.tools.base import AgentContext
                        ctx = AgentContext(cfg=self.cfg, session=self.session,
                                           workspace=self.agent.ctx.workspace)
                        msg = ScreenshotTool().run(ctx)
                        self.pending_images.extend(ctx.pending_images)
                        ctx.pending_images.clear()
                        console.print(f"[green]✓[/green] {msg}")
                    elif cmd == "/new":
                        self._new_session()
                        console.print(f"[green]{t('✓ New session: {id}', id=self.session.id)}[/green]")
                    elif cmd == "/sessions":
                        for s in Session.list_sessions(self.cfg):
                            console.print(f"  {s['id']}  ({t('{count} messages', count=s['messages'])})")
                    elif cmd == "/load":
                        try:
                            self.session = Session.load(
                                self.cfg, arg,
                                build_system_prompt(self.mode, self.cfg, None, self.work_mode))
                            saved_mode = self.session.meta.get("work_mode")
                            if saved_mode in WORK_MODES:
                                self.work_mode = saved_mode
                                self.mode = WORK_MODES[saved_mode].agent_mode
                            self._rebuild_agent()
                            if self.session.messages and self.session.messages[0]["role"] == "system":
                                self.session.messages[0]["content"] = build_system_prompt(
                                    self.mode, self.cfg, getattr(self.agent, "workspace", None),
                                    self.work_mode)
                            console.print("[green]" + t("✓ Loaded: {id} ({count} messages)",
                                                         id=arg, count=len(self.session.messages)) + "[/green]")
                        except FileNotFoundError as e:
                            console.print(f"[red]{e}[/red]")
                    elif cmd == "/server":
                        self.cmd_server(arg or "status")
                    else:
                        console.print(f"[red]{t('Unknown command {command} - /help', command=cmd)}[/red]")
                except Exception as e:
                    console.print(f"[red]{t('Command error: {error}', error=e)}[/red]")
                console.print()
                continue

            # běžná zpráva
            self.handle_turn(line)
            console.print()


if __name__ == "__main__":
    TUIApp().run()
