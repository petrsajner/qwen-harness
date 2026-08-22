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
import re
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

# --- komunikační protokol (vynucený harnessem) ------------------------------
TASK_PROTOCOL_NOTE = (
    "[TASK PROTOCOL - follow for this task] "
    "(1) START: before any tool call, briefly confirm (1-2 sentences, user's language) "
    "what the task is and your plan. "
    "(2) PROGRESS: during longer work include one-sentence status updates "
    "(what you found/did, what you do next). "
    "(3) FINISH: when the task is done, ALWAYS end with a structured summary with these sections "
    "(use the user's language; skip sections that do not apply): "
    "✅ Done/Changed - exact files (paths) and what changed; "
    "🔍 Found - relevant findings (read-only, nothing changed); "
    "📋 Next steps - concrete suggested follow-up; "
    "⏸️ Postponed - what was deliberately left out and why."
)
PROGRESS_NOTE = (
    "[PROGRESS UPDATE REQUIRED] Before or together with your next tool call, give the user "
    "a ONE-sentence status update in their language: what you found/did so far and what you "
    "are doing next."
)
SUMMARY_NOTE = (
    "[FINAL SUMMARY REQUIRED] The task ended without the required structured summary. "
    "Write it now, in the user's language, short and concrete: "
    "✅ Done/Changed (exact files + what changed) · 🔍 Found (read-only findings) · "
    "📋 Next steps (concrete) · ⏸️ Postponed (why)."
)
_PROTOCOL_MARKS = ("[TASK PROTOCOL", "[PROGRESS UPDATE", "[FINAL SUMMARY")
TOOL_STEPS_BEFORE_UPDATE = 4   # tool-kroky bez slov k uživateli → vnutit status
MIN_TOOLS_FOR_SUMMARY = 3      # úloha s ≥N nástroji musí skončit strukturovaným souhrnem
COMPRESS_AT = 0.75             # auto-komprese při 75 % kontextu (bezpečná rezerva)
OVERFLOW_RE = re.compile(
    r"exceeds.{0,40}context|context.{0,40}(exceed|full|too (large|long))|"
    r"prompt is too long|maximum context",
    re.IGNORECASE,
)


def _looks_structured(text: str) -> bool:
    if len(text) > 800:
        return True
    return any(m in text for m in ("✅", "🔍", "📋", "⏸", "##", "- **", "\n- "))


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
        self._tools_used_this_task = 0
        self._tool_steps_since_update = 0
        self._summary_requested = False

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

    def set_workspace(self, path: str | Path) -> Path:
        """Nastaví pracovní adresář (workspace) pro nástroje.

        Pokud je zadán soubor, použije jeho nadřazený adresář.
        Vrací absolutní cestu; při neexistující cestě vyhodí ValueError.
        """
        p = Path(str(path).strip().strip('"').strip("'")).expanduser()
        p = p.resolve()
        if p.is_file():
            p = p.parent
        if not p.is_dir():
            raise ValueError(f"Adresář neexistuje: {p}")
        self.ctx.workspace = p
        return p

    @property
    def workspace(self) -> Path:
        return self.ctx.workspace

    @property
    def tools_enabled(self) -> bool:
        return self.mode != "chat"

    # ------------------------------------------------------------------
    def new_task(self, text: str, images: list[Path] | None = None) -> None:
        """Zaloguje uživatelský vstup a resetuje počítadla."""
        self._steps = 0
        self._pending = []
        self._tools_used_this_task = 0
        self._tool_steps_since_update = 0
        self._summary_requested = False
        self._overflow_retried = False
        self.safety.new_task()
        self.session.add("user", text, images=images)
        if self.tools_enabled:
            # jsou-staré protokolové poznámky → jedna čerstvá
            # (poznámky jdou jako user-role: Qwen šablona zakazuje system uprostřed konverzace)
            self.session.messages = [
                m for m in self.session.messages
                if not any(str(m.get("content", "")).startswith(mark) for mark in _PROTOCOL_MARKS)
            ]
            self.session.add("user", TASK_PROTOCOL_NOTE)

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
        """Jeden krok agenta (jedno LLM volání + vykonání nástrojů).

        Neočekávané výjimky zachytí a vrátí jako Status.ERROR (nikdy nevyhazuje).
        """
        try:
            return self._step(approve)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            import traceback
            tail = traceback.format_exc(limit=3).strip().splitlines()
            tail_s = tail[-1][:300] if tail else ""
            return StepResult(Status.ERROR,
                              text=f"{type(e).__name__}: {e}",
                              reasoning=tail_s)

    # ------------------------------------------------------------------
    def _ctx_limit(self) -> int:
        try:
            return int(self.cfg.model().get("ctx_size", 32768))
        except (KeyError, ValueError):
            return 32768

    def _maybe_compress(self, force: bool = False) -> None:
        """Auto-komprese kontextu při COMPRESS_AT % limitu (nebo vynuceně po přetečení).

        Ne-destruktivní: historie zůstává pro UI, model vidí souhrn + poslední zprávy.
        Fallback při selhání sumarizace: posun cutu (hard trim) na 50 % limitu.
        """
        limit = self._ctx_limit()
        est = self.session.estimate_context_tokens()
        if not force and est < int(limit * COMPRESS_AT):
            return
        self.emit("info", f"📦 Kontext ~{est} tok (>85 % z {limit}) - vytvářím souhrn starší konverzace ...")
        try:
            from harness.context import summarize_messages
            # sumarizuj to, co mizí z modelova pohledu (příp. včetně starého souhrnu)
            if self.session.compression:
                to_summarize = ([{"role": "user",
                                  "content": "Previous compression summary:\n" + self.session.compression["summary"]}]
                                + self.session.messages[self.session.compression["cut"]:-4])
            else:
                to_summarize = self.session.messages[1:-4]
            summary = summarize_messages(self.llm, to_summarize)
            ok = self.session.compress_to_summary(summary)
            if not ok:
                self.session.trim_to_budget(int(limit * 0.5))
            new_est = self.session.estimate_context_tokens()
            self.emit("info", f"📦 Kontext komprimován: ~{est} → ~{new_est} tokenů (historie v UI zůstává)")
        except Exception as e:
            self.session.trim_to_budget(int(limit * 0.5))
            self.emit("info", f"📦 Sumarizace selhala ({type(e).__name__}: {e}) - aplikován tvrdý trim")

    def _step(self, approve: bool | None = None) -> StepResult:
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

        # 2b) auto-komprese kontextu (příliš dlouhá konverzace)
        self._maybe_compress()

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
            # 🔄 PŘETEČENÍ KONTEXTU: komprimuj hned a zkus znovu (1× za úlohu)
            if not self._overflow_retried and OVERFLOW_RE.search(str(e)):
                self._overflow_retried = True
                self.emit("info", "⚡ Přetečení kontextu - komprimuji a zkouším znovu ...")
                self._maybe_compress(force=True)
                return StepResult(Status.CONTINUE,
                                  text="Kontext přetekl - byl komprimován, zkouším pokračovat.")
            return StepResult(Status.ERROR, text=f"LLM chyba: {type(e).__name__}: {e}")

        self._steps += 1

        # 4) tool calls?
        if res.has_tool_calls:
            risky = []
            for c in res.tool_calls:
                tool = self.registry.get(c["function"]["name"])
                if tool is None:
                    risky.append(c)
                    continue
                # dynamická klasifikace rizika (např. read-only shell příkazy)
                risk = tool.risk
                risk_for = getattr(tool, "risk_for", None)
                if risk_for is not None:
                    try:
                        args = parse_tool_arguments(c["function"]["arguments"])
                        risk = risk_for(args)
                    except ValueError:
                        risk = Risk.WRITE
                if self.safety.needs_confirmation(risk):
                    risky.append(c)
            if risky:
                self._pending = res.tool_calls
                return StepResult(Status.NEEDS_CONFIRMATION,
                                  pending_calls=res.tool_calls,
                                  pending_summary=self._summarize_calls(res.tool_calls),
                                  reasoning=res.reasoning)
            trace = self._execute_calls(res.tool_calls)
            # 📢 progress nudge: dlouhá série kroků bez slov k uživateli
            self._tools_used_this_task += len(trace)
            self._tool_steps_since_update += 1
            if self._tool_steps_since_update >= TOOL_STEPS_BEFORE_UPDATE:
                self._tool_steps_since_update = 0
                self.session.add("user", PROGRESS_NOTE)
            return StepResult(Status.CONTINUE, tool_trace=trace, reasoning=res.reasoning)

        # 5) finální odpověď (+ 📋 vynucení strukturovaného souhrnu)
        if (self.tools_enabled
                and self._tools_used_this_task >= MIN_TOOLS_FOR_SUMMARY
                and not self._summary_requested
                and not _looks_structured(res.content or "")):
            self._summary_requested = True
            self.session.add("assistant", res.content)
            self.session.add("user", SUMMARY_NOTE)
            return StepResult(Status.CONTINUE, text=res.content, reasoning=res.reasoning)
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
