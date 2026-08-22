"""Agent loop - jádro harnesu.

Dva způsoby použití:
  1) TUI:  for ev in agent.run(text): ...          (potvrzování přes callback)
  2) Web:  r = agent.step() / agent.step(approve=True|False)  (resumable pro UI tlačítka)

Stavy kroku (StepResult.status):
  FINAL               - model odpověděl, úloha hotová
  CONTINUE            - nástroje vykonány, pokračuj dalším step()
  NEEDS_CONFIRMATION  - čeká se na schválení akcí (pending_calls)
  ABORTED             - přerušeno (limit kroků / abort flag)
  ERROR               - chyba (API, parsing)
"""
from __future__ import annotations

import enum
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Generator

from harness.config import Config
from harness.llm import LLMClient, parse_tool_arguments
from harness.prompts import system_prompt
from harness.safety import Risk, SafetyPolicy
from harness.session import Session
from harness.tools.base import AgentContext, ToolRegistry


class Status(str, enum.Enum):
    FINAL = "final"
    CONTINUE = "continue"
    NEEDS_CONFIRMATION = "needs_confirmation"
    ABORTED = "aborted"
    ERROR = "error"


@dataclass
class StepResult:
    status: Status
    text: str = ""
    pending_calls: list = field(default_factory=list)   # tool_calls čekající na schválení
    pending_summary: list[str] = field(default_factory=list)  # lidsky čitelný popis akcí
    tool_trace: list = field(default_factory=list)       # [(name, args, result), ...] z tohoto kroku
    reasoning: str = ""


EventCb = Callable[[str, object], None]  # ("text"|"reasoning"|"tool_start"|"tool_result", payload)


class Agent:
    def __init__(self, cfg: Config, llm: LLMClient, session: Session, registry: ToolRegistry,
                 safety: SafetyPolicy, mode: str = "agent", on_event: EventCb | None = None,
                 abort_flag: threading.Event | None = None):
        self.cfg = cfg
        self.llm = llm
        self.session = session
        self.registry = registry
        self.safety = safety
        self.mode = mode
        self.on_event = on_event
        self.abort_flag = abort_flag or threading.Event()
        self.ctx = AgentContext(
            cfg=cfg, session=session,
            workspace=Path(cfg.agent.get("workspace") or Path.cwd()).resolve(),
        )
        self._steps = 0
        self._pending: list[dict] = []          # tool_calls čekající na potvrzení

    # ------------------------------------------------------------------
    def emit(self, kind: str, payload) -> None:
        if self.on_event:
            try:
                self.on_event(kind, payload)
            except Exception:
                pass

    def set_mode(self, mode: str) -> None:
        if mode not in ("chat", "agent", "computer"):
            raise ValueError(f"Neznámý režim: {mode}")
        self.mode = mode

    @property
    def tools_enabled(self) -> bool:
        return self.mode != "chat"

    # ------------------------------------------------------------------
    def new_task(self, text: str, images: list[Path] | None = None) -> None:
        """Zaloguje uživatelský vstup a resetuje počítadla."""
        self._steps = 0
        self._pending = []
        self.safety.new_task()
        self.session.add("user", text, images=images)

    def _check_abort(self) -> StepResult | None:
        if self.abort_flag.is_set():
            return StepResult(Status.ABORTED, text="Přerušeno uživatelem.")
        if self._steps >= self.safety.step_limit():
            return StepResult(Status.ABORTED,
                              text=f"Dosažen limit {self.safety.step_limit()} kroků agenta. "
                                   f"Zvyš limit (/autonomy, config agent.max_steps) nebo zadej úkol znovu.")
        return None

    # ------------------------------------------------------------------
    def _execute_calls(self, calls: list[dict]) -> list[tuple]:
        """Vykoná tool calls a přidá výsledky do session. Vrátí trace."""
        # asistentova zpráva s tool_calls (přesně jak ji vrátil model)
        self.session.add("assistant", "", tool_calls=calls)
        trace = []
        for call in calls:
            name = call["function"]["name"]
            try:
                args = parse_tool_arguments(call["function"]["arguments"])
            except ValueError as e:
                args = None
                result = f"ERROR: {e}"
            self.emit("tool_start", (name, args))
            if args is not None:
                result = self.registry.execute(name, args, self.ctx)
            trace.append((name, args, result))
            self.emit("tool_result", (name, result))
            self.session.add("tool", result, tool_call_id=call["id"], name=name)
        # obrázky vytvořené nástroji (screenshot, view_image) přilož jako user zprávu
        if self.ctx.pending_images:
            imgs = list(self.ctx.pending_images)
            self.ctx.pending_images.clear()
            self.session.add(
                "user",
                "[The following image(s) were captured/attached by tools - use them for your next step:]",
                images=imgs,
            )
        return trace

    def _summarize_calls(self, calls: list[dict]) -> list[str]:
        out = []
        for c in calls:
            try:
                args = parse_tool_arguments(c["function"]["arguments"])
                short = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in list(args.items())[:4])
            except ValueError:
                short = (c["function"]["arguments"] or "")[:80]
            out.append(f"{c['function']['name']}({short})")
        return out

    # ------------------------------------------------------------------
    def step(self, approve: bool | None = None) -> StepResult:
        """Jeden krok agenta (jedno LLM volání + vykonání nástrojů)."""
        # 1) čekající potvrzení
        if self._pending:
            if approve is None:
                return StepResult(Status.NEEDS_CONFIRMATION,
                                  pending_calls=self._pending,
                                  pending_summary=self._summarize_calls(self._pending))
            if not approve:
                calls = self._pending
                self._pending = []
                self.session.add("assistant", "", tool_calls=calls)
                for c in calls:
                    self.session.add("tool", "User DECLINED this action. Do not retry it; "
                                             "ask the user or propose an alternative.",
                                     tool_call_id=c["id"], name=c["function"]["name"])
                return StepResult(Status.CONTINUE, text="Akce zamítnuta uživatelem.")
            calls = self._pending
            self._pending = []
            self.safety.mark_confirmed()
            trace = self._execute_calls(calls)
            return StepResult(Status.CONTINUE, tool_trace=trace)

        # 2) abort / limit kontrola
        stop = self._check_abort()
        if stop:
            return stop

        # 3) LLM volání
        tools = self.registry.schemas() if self.tools_enabled else None
        try:
            res = self.llm.stream(
                self.session.to_api_messages(),
                tools=tools,
                max_tokens=int(self.cfg.agent.get("max_tokens", 16384)),
                on_text=lambda t: self.emit("text", t),
                on_reasoning=lambda t: self.emit("reasoning", t),
            )
        except KeyboardInterrupt:
            raise
        except Exception as e:
            return StepResult(Status.ERROR, text=f"LLM chyba: {type(e).__name__}: {e}")

        self._steps += 1

        # 4) tool calls?
        if res.has_tool_calls:
            risky = []
            for c in res.tool_calls:
                tool = self.registry.get(c["function"]["name"])
                risk = tool.risk if tool else Risk.WRITE
                if self.safety.needs_confirmation(risk):
                    risky.append(c)
            if risky:
                self._pending = res.tool_calls
                return StepResult(Status.NEEDS_CONFIRMATION,
                                  pending_calls=res.tool_calls,
                                  pending_summary=self._summarize_calls(res.tool_calls),
                                  reasoning=res.reasoning)
            trace = self._execute_calls(res.tool_calls)
            return StepResult(Status.CONTINUE, tool_trace=trace, reasoning=res.reasoning)

        # 5) finální odpověď
        self.session.add("assistant", res.content)
        return StepResult(Status.FINAL, text=res.content, reasoning=res.reasoning)

    # ------------------------------------------------------------------
    def run(self, text: str, images: list[Path] | None = None,
            confirm_cb: Callable[[list[str]], bool] | None = None,
            max_rounds: int = 200) -> Generator[StepResult, None, None]:
        """TUI convenience: celá úloha, potvrzování přes confirm_cb."""
        self.new_task(text, images)
        for _ in range(max_rounds):
            result = self.step()
            if result.status is Status.NEEDS_CONFIRMATION:
                if confirm_cb is None:
                    approved = True  # bez callbacku neschvaluj nic destruktivního
                else:
                    approved = confirm_cb(result.pending_summary)
                result = self.step(approve=approved)
                yield result
                continue
            yield result
            if result.status is not Status.CONTINUE:
                return


def build_registry(mode: str) -> ToolRegistry:
    """Postav registry nástrojů podle režimu."""
    from harness.tools import computer, fs, shell, vision
    reg = ToolRegistry()
    if mode == "chat":
        return reg
    fs.register_fs_tools(reg)
    shell.register_shell_tools(reg)
    vision.register_vision_tools(reg)
    if mode == "computer":
        computer.register_computer_tools(reg)
    return reg
