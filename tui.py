"""Terminálové UI pro Qwen3.8-27B harness.

Spuštění:  .venv/Scripts/python tui.py
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from harness.agent import Agent, Status, build_registry
from harness.config import load_config
from harness.llm import LLMClient
from harness.prompts import system_prompt
from harness.safety import SafetyPolicy
from harness.session import Session
from harness.work_modes import WORK_MODES, normalize_work_mode
from harness.version import APP_VERSION

console = Console()

BANNER = (f"[bold cyan]Qwen3.8-27B v{APP_VERSION}"
          "  •  lokální harness  •  RTX 5090[/bold cyan]")

HELP = """[bold]Příkazy:[/bold]
  /memory                zobraz trvalou paměť (globální + režim + projekt)
  /model q4|q5           přepnutí modelu (restart serveru)
  /work discussion|research|writing|development|computer   pracovní režim
  /mode chat|agent|computer     kompatibilní zkratka
  /autonomy supervised|semi|auto   úroveň autonomie
  /thinking xhigh|medium|low|off   hloubka uvažování modelu
  /img <cesta>           přiložit obrázek k další zprávě
  /screenshot            přiložit screenshot obrazovky
  /new                   nová session   /sessions  seznam   /load <id>
  /server status|start|stop    správa inference serveru
  /help                  tato nápověda  /exit  konec
Vstup: Enter odeslat  •  Ctrl+C přerušit generování"""


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
            self.cfg, system_prompt=system_prompt(self.mode, self.work_mode),
            work_mode=self.work_mode)
        self.abort = threading.Event()
        self.auto_approve = False
        self._rebuild_agent()

    def _rebuild_agent(self) -> None:
        safety = SafetyPolicy(
            autonomy=self.autonomy,
            max_steps=int(self.cfg.agent.get("max_steps", 40)),
            semi_max_steps=int(self.cfg.agent.get("semi_max_steps", 15)),
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
            self.session.messages[0]["content"] = system_prompt(self.mode, self.work_mode)
        self.auto_approve = False

    # ------------------------------------------------------------------
    def status_line(self) -> str:
        think = "[red]off[/red]" if not self.thinking else f"[green]{self.reasoning_effort}[/green]"
        ws = self.agent.workspace if self.agent else Path.cwd()
        try:
            est = self.session.estimate_context_tokens()
            limit = int(self.cfg.model().get("ctx_size", 32768))
            ctx = f"  [bold]ctx[/bold]=~{est / 1000:.1f}k/{limit // 1000}k"
        except Exception:
            ctx = ""
        return (f"[bold]model[/bold]={self.model_key}  "
                f"[bold]režim[/bold]={WORK_MODES[self.work_mode].label}  "
                f"[bold]autonomie[/bold]={self.autonomy}  [bold]thinking[/bold]={think}{ctx}\n"
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
                           title="[bold red]⚠ Potvrzení akce[/bold red]",
                           subtitle=f"režim: {self.autonomy}"))
        if self.auto_approve:
            console.print("[dim](schváleno automaticky - 'a' v předchozím potvrzení)[/dim]")
            return True
        ans = Prompt.ask("  Povolit?  [y/n/a]  (a = vše do konce úlohy)",
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
                    console.print(f"\n[bold red]CHYBA:[/bold red] {result.text}")
                    if "Connection" in result.text or "health" in result.text:
                        console.print("[dim]Zkus /server start[/dim]")
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
            console.print("\n[bold yellow]⛔ Přerušeno (Ctrl+C)[/bold yellow]")
            # doruč zprávu o přerušení do session, aby model věděl kontext
            # (user role - Qwen šablona neumí system uprostřed konverzace)
            self.session.add("user", "[Interrupted by user]")

    # ------------------------------------------------------------------
    def cmd_model(self, key: str) -> None:
        if key not in self.cfg.data["models"]:
            console.print(f"[red]Neznámý model '{key}'. Dostupné: {', '.join(self.cfg.data['models'])}[/red]")
            return
        from harness import servermgmt
        with console.status(f"[bold]Přepínám na model '{key}' (restart llama-server)...[/bold]"):
            ok = servermgmt.ensure(self.cfg, key)
        if ok:
            self.model_key = key
            self.cfg.data["default_model"] = key  # ctx limit sleduje model
            console.print(f"[green]✓ Model {key} běží.[/green]")
            console.print(self.status_line())
        else:
            console.print("[red]Přepnutí selhalo - viz runtime/llama-server.log[/red]")

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
            console.print("[yellow]⚠ llama-server neběží.[/yellow]")
            if Prompt.ask("  Spustit teď?", choices=["y", "n"], default="y") == "y":
                if servermgmt.start(self.cfg, self.model_key) != 0:
                    console.print("[red]Server se nepodařilo spustit - zkus /server start později.[/red]")
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
                console.print("\n[dim]Nashledanou![/dim]")
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
                            console.print("[red]Použití: /mode chat|agent|computer[/red]")
                    elif cmd == "/work":
                        if arg in WORK_MODES:
                            self._set_work_mode(arg)
                            console.print(self.status_line())
                        else:
                            console.print("[red]Použití: /work discussion|research|writing|development|computer[/red]")
                    elif cmd == "/autonomy":
                        if arg in ("supervised", "semi", "auto"):
                            self.autonomy = arg
                            self._rebuild_agent()
                            self.auto_approve = False
                            console.print(self.status_line())
                        else:
                            console.print("[red]Použití: /autonomy supervised|semi|auto[/red]")
                    elif cmd == "/thinking":
                        arg_l = arg.strip().lower()
                        if arg_l in ("xhigh", "medium", "low", "on", "off", ""):
                            if arg_l == "":
                                cur = "off" if not self.thinking else self.reasoning_effort
                                console.print(f"Přemýšlení: [bold]{cur}[/bold] "
                                              f"(volby: xhigh | medium | low | off)")
                            elif arg_l == "off":
                                self.thinking = False
                            else:
                                self.thinking = True
                                self.reasoning_effort = arg_l if arg_l != "on" else "xhigh"
                                self.cfg.data["reasoning_effort"] = self.reasoning_effort
                            console.print(self.status_line())
                        else:
                            console.print("[red]Použití: /thinking xhigh|medium|low|off[/red]")
                    elif cmd == "/ws":
                        if arg:
                            try:
                                p = self.agent.set_workspace(arg)
                                # aktualizuj system prompt v session
                                if self.session.messages and self.session.messages[0]["role"] == "system":
                                    from harness.prompts import system_prompt as _sp
                                    self.session.messages[0]["content"] = (
                                        _sp(self.mode, self.work_mode) +
                                        f"\n\nCurrent project workspace: {p}. "
                                        f"Relative paths in tools resolve against it. "
                                        f"The user keeps project sources and documents there.")
                                console.print(f"[green]✓ Workspace nastaven:[/green] {p}")
                            except ValueError as e:
                                console.print(f"[red]{e}[/red]")
                        else:
                            console.print(f"Workspace: [bold]{self.agent.workspace}[/bold]")
                    elif cmd == "/memory":
                        from harness.memory import MemoryStore
                        store = MemoryStore(
                            self.cfg, self.agent.workspace, self.work_mode)
                        console.print(f"[bold]Globální paměť:[/bold] {store.global_path}")
                        console.print(f"[bold]Paměť režimu {WORK_MODES[self.work_mode].label}:[/bold] "
                                      f"{store.mode_path()}")
                        console.print(f"[bold]Projektová paměť:[/bold] "
                                      f"{store.project_path() or '— (nastav /ws)'}")
                        console.print(f"[dim]{store.context_block()[:1200]}[/dim]")
                    elif cmd == "/img":
                        p = Path(arg).expanduser()
                        if p.exists():
                            self.pending_images.append(p)
                            console.print(f"[green]✓ Obrázek přiložen:[/green] {p.name}")
                        else:
                            console.print(f"[red]Soubor nenalezen: {p}[/red]")
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
                        console.print(f"[green]✓ Nová session:[/green] {self.session.id}")
                    elif cmd == "/sessions":
                        for s in Session.list_sessions(self.cfg):
                            console.print(f"  {s['id']}  ({s['messages']} zpráv)")
                    elif cmd == "/load":
                        try:
                            self.session = Session.load(
                                self.cfg, arg, system_prompt(self.mode, self.work_mode))
                            saved_mode = self.session.meta.get("work_mode")
                            if saved_mode in WORK_MODES:
                                self.work_mode = saved_mode
                                self.mode = WORK_MODES[saved_mode].agent_mode
                            self._rebuild_agent()
                            if self.session.messages and self.session.messages[0]["role"] == "system":
                                self.session.messages[0]["content"] = system_prompt(
                                    self.mode, self.work_mode)
                            console.print(f"[green]✓ Načteno:[/green] {arg} "
                                          f"({len(self.session.messages)} zpráv)")
                        except FileNotFoundError as e:
                            console.print(f"[red]{e}[/red]")
                    elif cmd == "/server":
                        self.cmd_server(arg or "status")
                    else:
                        console.print(f"[red]Neznámý příkaz {cmd} - /help[/red]")
                except Exception as e:
                    console.print(f"[red]Chyba příkazu: {e}[/red]")
                console.print()
                continue

            # běžná zpráva
            self.handle_turn(line)
            console.print()


if __name__ == "__main__":
    TUIApp().run()
