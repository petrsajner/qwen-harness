"""Agent loop - jádro harnesu.

Dva způsoby použití:
  1) TUI:  for ev in agent.run(text): ...          (potvrzování přes callback)
  2) Web:  r = agent.step() / agent.step(approve=True|False)  (resumable pro UI tlačítka)

Stavy kroku (StepResult.status):
  FINAL               - model odpověděl, úloha hotová
  CONTINUE            - nástroje vykonány, pokračuj dalším step()
  NEEDS_CONFIRMATION  - čeká se na schválení akcí (pending_calls)
  ABORTED             - přerušeno uživatelem nebo explicitním testovacím limitem
  ERROR               - chyba (API, parsing)
"""
from __future__ import annotations

import enum
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Generator

from harness.config import Config
from harness.changes import ChangeJournal
from harness.llm import LLMClient, parse_tool_arguments
from harness.processes import ProcessManager
from harness.repo_index import RepoIndex
from harness.research import (GenerationStopped, ResearchLedger, plan_research,
                              synthesize_research)
from harness.prompts import system_prompt
from harness.safety import Risk, SafetyPolicy
from harness.session import Session
from harness.tools.base import AgentContext, ToolRegistry
from harness.work_modes import WORK_MODES, normalize_work_mode


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


EventCb = Callable[[str, object], None]  # text/reasoning/tool_delta/tool_start/tool_result

# --- komunikační protokol (vynucený harnessem) ------------------------------
TASK_PROTOCOL_NOTE = (
    "[TASK PROTOCOL - follow for this task] "
    "(1) START: before any tool call, briefly confirm (1-2 sentences, user's language) "
    "what the task is and your plan. "
    "(2) PROGRESS: during longer work include one-sentence status updates "
    "(what you found/did, what you do next). "
    "Before finishing substantial work, use proportionate verification when it helps: re-check "
    "the request, inspect results, and run relevant checks. This is guidance, not a reason to "
    "override the user's requested scope, structure, or implementation form. "
    "(3) FINISH: when the task is done, ALWAYS end with a structured summary with these sections "
    "(use the user's language; skip sections that do not apply): "
    "✅ Done/Changed - exact files (paths) and what changed; "
    "🔍 Found - relevant findings (read-only, nothing changed); "
    "📋 Next steps - concrete suggested follow-up; "
    "⏸️ Postponed - what was deliberately left out and why."
)
WRITING_PROTOCOL_NOTE = (
    "[WRITING PROTOCOL - follow for this task] "
    "Before editing, briefly confirm the writing goal, intended audience, tone, and constraints. "
    "Preserve all requested meaning and continuity. During longer work give short progress updates. "
    "Finish with a clear human summary: what was written or revised, important choices, and any "
    "open questions. Do not introduce coding, Git, build, or test terminology unless relevant."
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
WRITING_SUMMARY_NOTE = (
    "[WRITING SUMMARY REQUIRED] Briefly summarize what was written or revised, the important "
    "creative/editorial choices, and any open questions. Use the user's language."
)
_PROTOCOL_MARKS = ("[TASK PROTOCOL", "[WRITING PROTOCOL", "[PROGRESS UPDATE",
                   "[FINAL SUMMARY", "[WRITING SUMMARY")
TOOL_STEPS_BEFORE_UPDATE = 4   # tool-kroky bez slov k uživateli → vnutit status
MIN_TOOLS_FOR_SUMMARY = 3      # úloha s ≥N nástroji musí skončit strukturovaným souhrnem
COMPRESS_AT = 0.85             # auto-komprese při 85 % kontextu
OVERFLOW_RE = re.compile(
    r"exceeds.{0,40}context|context.{0,40}(exceed|full|too (large|long))|"
    r"prompt is too long|maximum context",
    re.IGNORECASE,
)
DOCUMENT_OPERATION_RE = re.compile(
    r"(?:ulož|ulozit|uložit|export|vyexport|save|vytvoř|vytvor).{0,80}"
    r"(?:pdf|docx|markdown|soubor)|"
    r"(?:pdf|docx|markdown).{0,80}(?:ulož|ulozit|uložit|export|vyexport|save|vytvoř|vytvor)",
    re.IGNORECASE,
)


def _looks_structured(text: str) -> bool:
    if len(text) > 800:
        return True
    return any(m in text for m in ("✅", "🔍", "📋", "⏸", "##", "- **", "\n- "))


class Agent:
    def __init__(self, cfg: Config, llm: LLMClient, session: Session, registry: ToolRegistry,
                 safety: SafetyPolicy, mode: str = "agent", on_event: EventCb | None = None,
                 abort_flag: threading.Event | None = None,
                 process_manager: ProcessManager | None = None,
                 work_mode: str | None = None):
        self.cfg = cfg
        self.llm = llm
        self.session = session
        self.registry = registry
        self.safety = safety
        self.work_mode = normalize_work_mode(work_mode, mode)
        self.mode = WORK_MODES[self.work_mode].agent_mode
        self.on_event = on_event
        self.abort_flag = abort_flag or threading.Event()
        configured_workspace = cfg.agent.get("workspace")
        candidate_workspace = (Path(configured_workspace).resolve()
                               if configured_workspace else None)
        project_workspace = (candidate_workspace
                             if candidate_workspace and candidate_workspace.is_dir() else None)
        self.ctx = AgentContext(
            cfg=cfg, session=session,
            workspace=project_workspace or Path.cwd().resolve(),
            project_workspace=project_workspace,
            work_mode=self.work_mode,
        )
        self.ctx.changes = ChangeJournal(session, self.ctx.workspace)
        self.ctx.processes = process_manager or ProcessManager()
        self.ctx.processes.bind_session(session)
        self.ctx.repo_index = RepoIndex(project_workspace) if project_workspace else None
        self.ctx.research = ResearchLedger(session)
        self._steps = 0
        self._pending: list[dict] = []          # tool_calls čekající na potvrzení
        self._pending_text = ""
        self._tools_used_this_task = 0
        self._tool_steps_since_update = 0
        self._summary_requested = False
        restored = self.session.load_task_state()
        if restored.get("status") in ("running", "waiting_confirmation"):
            self._steps = int(restored.get("steps", 0))
            self._pending = list(restored.get("pending_calls") or [])
            self._pending_text = str(restored.get("pending_text") or "")

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
        self.work_mode = normalize_work_mode(None, mode)
        self.ctx.work_mode = self.work_mode

    def set_work_mode(self, work_mode: str) -> None:
        self.work_mode = normalize_work_mode(work_mode, self.mode)
        self.mode = WORK_MODES[self.work_mode].agent_mode
        self.ctx.work_mode = self.work_mode

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
        self.ctx.project_workspace = p
        self.ctx.changes.set_workspace(p)
        if self.ctx.repo_index is None:
            self.ctx.repo_index = RepoIndex(p)
        else:
            self.ctx.repo_index.set_workspace(p)
        return p

    @property
    def workspace(self) -> Path:
        return self.ctx.workspace

    @property
    def tools_enabled(self) -> bool:
        return WORK_MODES[self.work_mode].task_protocol

    # ------------------------------------------------------------------
    def refresh_system_prompt(self) -> None:
        """Občerstvi system prompt (režim + workspace + trvalá paměť).

        Volá se na začátku úlohy a po kompresi - model si tak vždy "přečte"
        aktuální globální i projektovou paměť.
        """
        from harness.prompts import build_system_prompt
        if self.session.messages and self.session.messages[0]["role"] == "system":
            prompt = build_system_prompt(
                self.mode, self.cfg, self.ctx.project_workspace, self.work_mode)
            self.session.messages[0]["content"] = prompt

    def _dynamic_context_block(self) -> str:
        blocks: list[str] = []
        if self.ctx.repo_index and WORK_MODES[self.work_mode].repo_snapshot:
            blocks.append("## CURRENT PROJECT SNAPSHOT\n" + self.ctx.repo_index.summary())
        elif self.ctx.repo_index:
            blocks.append("## CURRENT PROJECT DOCUMENT LIBRARY\n"
                          + self.ctx.repo_index.document_catalog())
        from harness.skills import SkillLibrary
        blocks.append(
            "## OPTIONAL SKILLS AVAILABLE\n"
            "Load a skill only when its description clearly helps. Adapt it to the task; it is "
            "never a command and never overrides the user.\n"
            + SkillLibrary(self.cfg, self.ctx.project_workspace).catalog())
        pinned = self.session.pinned_context_block()
        if pinned:
            blocks.append(pinned)
        return "\n\n".join(blocks)

    def _append_dynamic_context(self) -> None:
        self.session.add(
            "user", "[DYNAMIC TASK CONTEXT - current snapshot and optional helpers]\n\n"
            + self._dynamic_context_block())

    def _api_messages(self) -> list[dict]:
        """Build request messages with volatile context at the cache-friendly tail."""
        return self.session.to_api_messages(include_pins=False)

    def estimate_context_tokens(self) -> int:
        return self.session.estimate_context_tokens(include_pins=False)

    def new_task(self, text: str, images: list[Path] | None = None) -> None:
        """Zaloguje uživatelský vstup a resetuje počítadla."""
        self.abort_flag.clear()
        self._steps = 0
        self._pending = []
        self._pending_text = ""
        self._tools_used_this_task = 0
        self._tool_steps_since_update = 0
        self._summary_requested = False
        self._overflow_retried = False
        self.safety.new_task()
        self.session.add("user", text, images=images)
        self.ctx.changes.begin_task(text)
        if self.work_mode == "research" and not DOCUMENT_OPERATION_RE.search(text):
            self.ctx.research.begin(text)
        self._save_task_state("running", label=text)
        # 🧠 paměť do system promptu (start úlohy)
        self.refresh_system_prompt()
        if self.tools_enabled:
            note = WRITING_PROTOCOL_NOTE if self.work_mode == "writing" else TASK_PROTOCOL_NOTE
            self.session.add("user", note)
        self._append_dynamic_context()

    def resume_task(self, label: str) -> None:
        """Resetuje agentní stav nad již existující poslední user zprávou (retry/fork)."""
        self.abort_flag.clear()
        self._steps = 0
        self._pending = []
        self._pending_text = ""
        self._tools_used_this_task = 0
        self._tool_steps_since_update = 0
        self._summary_requested = False
        self._overflow_retried = False
        self.safety.new_task()
        self.ctx.changes.begin_task(label)
        self._save_task_state("running", label=label)
        self.refresh_system_prompt()
        if self.tools_enabled:
            note = WRITING_PROTOCOL_NOTE if self.work_mode == "writing" else TASK_PROTOCOL_NOTE
            self.session.add("user", note)
        self._append_dynamic_context()

    def steer(self, text: str, images: list[Path] | None = None) -> None:
        """Continue the current task with a user clarification after stopping its stream."""
        self.abort_flag.clear()
        self._pending = []
        self._pending_text = ""
        self.session.add("user", text, images=images)
        self._save_task_state("running")
        self.refresh_system_prompt()
        if self.tools_enabled:
            note = WRITING_PROTOCOL_NOTE if self.work_mode == "writing" else TASK_PROTOCOL_NOTE
            self.session.add("user", note)
        self._append_dynamic_context()

    def _check_abort(self) -> StepResult | None:
        if self.abort_flag.is_set():
            self._save_task_state("aborted", result="Přerušeno uživatelem.")
            return StepResult(Status.ABORTED, text="Přerušeno uživatelem.")
        limit = self.safety.step_limit()
        if limit is not None and self._steps >= limit:
            self._save_task_state("aborted", result="Dosažen limit kroků agenta.")
            return StepResult(Status.ABORTED,
                              text=f"Dosažen limit {limit} kroků agenta. "
                                   f"Zvyš limit (/autonomy, config agent.max_steps) nebo zadej úkol znovu.")
        return None

    # ------------------------------------------------------------------
    def _execute_calls(self, calls: list[dict], assistant_text: str = "") -> list[tuple]:
        """Vykoná tool calls a přidá výsledky do session. Vrátí trace."""
        # asistentova zpráva s tool_calls (přesně jak ji vrátil model)
        self.session.add("assistant", assistant_text, tool_calls=calls)
        prepared = []
        for call in calls:
            name = call["function"]["name"]
            try:
                args = parse_tool_arguments(call["function"]["arguments"])
            except ValueError as e:
                args = None
                parse_error = f"ERROR: {e}"
            else:
                parse_error = None
            self.emit("tool_start", (name, args))
            prepared.append((call, name, args, parse_error))

        def execute_one(item):
            _, name, args, parse_error = item
            return parse_error if parse_error is not None else self.registry.execute(name, args, self.ctx)

        can_parallel = len(prepared) > 1 and all(
            args is not None and self.registry.get(name) is not None
            and bool(self.registry.get(name).parallel_safe)
            for _, name, args, _ in prepared
        )
        if can_parallel:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(4, len(prepared)),
                                    thread_name_prefix="agent-read") as pool:
                results = list(pool.map(execute_one, prepared))
        else:
            results = [execute_one(item) for item in prepared]

        trace = []
        for (call, name, args, _), result in zip(prepared, results):
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
            self._save_task_state("error", result=f"{type(e).__name__}: {e}")
            return StepResult(Status.ERROR,
                              text=f"{type(e).__name__}: {e}",
                              reasoning=tail_s)

    # ------------------------------------------------------------------
    def _ctx_limit(self) -> int:
        try:
            return self.cfg.context_size()
        except (KeyError, ValueError):
            return 32768

    def _maybe_compress(self, force: bool = False) -> None:
        """Auto-komprese kontextu při COMPRESS_AT % limitu (nebo vynuceně po přetečení).

        Ne-destruktivní: historie zůstává pro UI, model vidí souhrn + poslední zprávy.
        Fallback při selhání sumarizace: posun cutu (hard trim) na 50 % limitu.
        """
        limit = self._ctx_limit()
        est = self.estimate_context_tokens()
        if not force and est < int(limit * COMPRESS_AT):
            return
        self.emit("info", f"📦 Kontext ~{est} tok (>85 % z {limit}) - vytvářím souhrn starší konverzace ...")
        try:
            from harness.context import summarize_messages
            keep_tokens = int(limit * 0.35)
            cut = self.session.compression_cut(keep_tokens=keep_tokens)
            if cut is None:
                self.session.trim_to_budget(int(limit * 0.5))
                new_est = self.session.estimate_context_tokens()
                self.refresh_system_prompt()
                self.emit("info", f"📦 Kontext oříznut: ~{est} → ~{new_est} tokenů")
                return
            start = self.session.compression["cut"] if self.session.compression else (
                1 if self.session.messages and self.session.messages[0].get("role") == "system" else 0
            )
            # Sumarizuj přesně rozsah, který po posunu cutu zmizí z modelova pohledu.
            if self.session.compression:
                to_summarize = ([{"role": "user",
                                  "content": "Previous compression summary:\n" + self.session.compression["summary"]}]
                                + self.session.messages[start:cut])
            else:
                to_summarize = self.session.messages[start:cut]
            summary = summarize_messages(
                self.llm, to_summarize, should_stop=self.abort_flag.is_set)
            ok = self.session.compress_to_summary(summary, keep_tokens=keep_tokens, cut=cut)
            if not ok:
                self.session.trim_to_budget(int(limit * 0.5))
            new_est = self.session.estimate_context_tokens()
            # 🧠 po kompresi si model znovu "přečte" aktuální paměť
            self.refresh_system_prompt()
            self.emit("info", f"📦 Kontext komprimován: ~{est} → ~{new_est} tokenů (historie v UI zůstává)")
        except Exception as e:
            if self.abort_flag.is_set():
                return
            self.session.trim_to_budget(int(limit * 0.5))
            self.emit("info", f"📦 Sumarizace selhala ({type(e).__name__}: {e}) - aplikován tvrdý trim")

    def _step(self, approve: bool | None = None) -> StepResult:
        # 1) čekající potvrzení
        if self._pending:
            if approve is None:
                self._save_task_state("waiting_confirmation", pending_calls=self._pending)
                return StepResult(Status.NEEDS_CONFIRMATION,
                                  pending_calls=self._pending,
                                  pending_summary=self._summarize_calls(self._pending),
                                  text=self._pending_text)
            if not approve:
                calls = self._pending
                self._pending = []
                pending_text = self._pending_text
                self._pending_text = ""
                self.session.add("assistant", pending_text, tool_calls=calls)
                for c in calls:
                    self.session.add("tool", "User DECLINED this action. Do not retry it; "
                                             "ask the user or propose an alternative.",
                                     tool_call_id=c["id"], name=c["function"]["name"])
                self._save_task_state("running")
                return StepResult(Status.CONTINUE, text="Akce zamítnuta uživatelem.")
            calls = self._pending
            self._pending = []
            pending_text = self._pending_text
            self._pending_text = ""
            self.safety.mark_confirmed()
            trace = self._execute_calls(calls, pending_text)
            self._save_task_state("running")
            return StepResult(Status.CONTINUE, tool_trace=trace)

        # 2) abort / limit kontrola
        stop = self._check_abort()
        if stop:
            return stop

        # 2b) auto-komprese kontextu (příliš dlouhá konverzace)
        self._maybe_compress()
        stop = self._check_abort()
        if stop:
            return stop

        if self.work_mode == "research":
            run = self.ctx.research.current()
            if run and not run.get("plan"):
                self.emit("info", "Připravuji plán výzkumu před hledáním...")
                try:
                    plan = plan_research(
                        self.llm, run.get("question", ""),
                        self.ctx.repo_index.document_catalog() if self.ctx.repo_index else "",
                        should_stop=self.abort_flag.is_set)
                    self.ctx.research.set_plan(plan)
                    self.session.add(
                        "user", "[RESEARCH PLAN - internal, follow systematically]\n"
                        + json.dumps(plan, ensure_ascii=False, indent=2))
                except GenerationStopped:
                    self._save_task_state("aborted", result="Zastaveno uživatelem.")
                    return StepResult(Status.ABORTED, text="Zastaveno uživatelem.")
                except Exception as exc:
                    self._save_task_state("error", result=f"Research planning failed: {exc}")
                    return StepResult(Status.ERROR, text=f"Plán výzkumu selhal: {exc}")

        # 3) LLM volání (memory nástroje má i chat režim)
        tools = self.registry.schemas() if self.registry.names() else None
        try:
            res = self.llm.stream(
                self._api_messages(),
                tools=tools,
                on_text=lambda t: self.emit("text", t),
                on_reasoning=lambda t: self.emit("reasoning", t),
                on_tool_delta=lambda name, args: self.emit("tool_delta", (name, args)),
                should_stop=self.abort_flag.is_set,
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
            self._save_task_state("error", result=f"LLM chyba: {type(e).__name__}: {e}")
            return StepResult(Status.ERROR, text=f"LLM chyba: {type(e).__name__}: {e}")

        self._steps += 1

        if res.stopped:
            if (res.content or "").strip():
                self.session.add("assistant", res.content)
            self._save_task_state("aborted", result="Zastaveno uživatelem.")
            return StepResult(Status.ABORTED, text="Zastaveno uživatelem.",
                              reasoning=res.reasoning)

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
                self._pending_text = res.content or ""
                self._save_task_state("waiting_confirmation", pending_calls=self._pending)
                return StepResult(Status.NEEDS_CONFIRMATION,
                                  pending_calls=res.tool_calls,
                                  pending_summary=self._summarize_calls(res.tool_calls),
                                  text=res.content,
                                  reasoning=res.reasoning)
            trace = self._execute_calls(res.tool_calls, res.content or "")
            # 📢 progress nudge: dlouhá série kroků bez slov k uživateli
            self._tools_used_this_task += len(trace)
            self._tool_steps_since_update += 1
            if self._tool_steps_since_update >= TOOL_STEPS_BEFORE_UPDATE:
                self._tool_steps_since_update = 0
                self.session.add("user", PROGRESS_NOTE)
            self._save_task_state("running")
            return StepResult(Status.CONTINUE, text=res.content, tool_trace=trace,
                              reasoning=res.reasoning)

        # 5) finální odpověď (+ 📋 vynucení strukturovaného souhrnu)
        if self.work_mode == "research":
            run = self.ctx.research.current()
            if run and run.get("status") == "collecting" and run.get("sources"):
                if (res.content or "").strip():
                    self.session.add("assistant", res.content)
                self.emit("info", "Sestavuji závěrečnou syntézu ze všech načtených zdrojů...")
                try:
                    res.content = synthesize_research(
                        self.llm, run, should_stop=self.abort_flag.is_set,
                        on_text=lambda text: self.emit("text", text),
                        on_reasoning=lambda text: self.emit("reasoning", text))
                    self.ctx.research.complete(res.content)
                except GenerationStopped as exc:
                    if exc.text:
                        self.session.add("assistant", exc.text)
                    self._save_task_state("aborted", result="Zastaveno uživatelem.")
                    return StepResult(Status.ABORTED, text="Zastaveno uživatelem.")
                except Exception as exc:
                    self._save_task_state("error", result=str(exc))
                    return StepResult(
                        Status.ERROR,
                        text=f"Výzkumná syntéza selhala: {type(exc).__name__}: {exc}",
                    )
        if (self.tools_enabled
                and self._tools_used_this_task >= MIN_TOOLS_FOR_SUMMARY
                and not self._summary_requested
                and not _looks_structured(res.content or "")):
            self._summary_requested = True
            self.session.add("assistant", res.content)
            note = WRITING_SUMMARY_NOTE if self.work_mode == "writing" else SUMMARY_NOTE
            self.session.add("user", note)
            self._save_task_state("running")
            return StepResult(Status.CONTINUE, text=res.content, reasoning=res.reasoning)
        self.session.add("assistant", res.content)
        self._save_task_state("complete", result=res.content)
        return StepResult(Status.FINAL, text=res.content, reasoning=res.reasoning)

    @property
    def has_resumable_task(self) -> bool:
        return self.session.load_task_state().get("status") in (
            "running", "waiting_confirmation")

    def _save_task_state(self, status: str, *, label: str | None = None,
                         pending_calls: list | None = None,
                         result: str | None = None) -> None:
        previous = self.session.load_task_state()
        self.session.save_task_state({
            "status": status,
            "label": previous.get("label", "") if label is None else label[:300],
            "work_mode": self.work_mode,
            "steps": self._steps,
            "pending_calls": list(self._pending if pending_calls is None else pending_calls),
            "pending_text": self._pending_text,
            "result": result,
        })

    # ------------------------------------------------------------------
    def run(self, text: str, images: list[Path] | None = None,
            confirm_cb: Callable[[list[str]], bool] | None = None,
            max_rounds: int | None = None) -> Generator[StepResult, None, None]:
        """TUI convenience: celá úloha, potvrzování přes confirm_cb."""
        self.new_task(text, images)
        rounds = 0
        while max_rounds is None or rounds < max_rounds:
            rounds += 1
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


def build_registry(mode: str, work_mode: str | None = None) -> ToolRegistry:
    """Postav registry podle jednotného pracovního režimu."""
    from harness.tools import computer, context, documents, fs, git, memory, shell, skills, vision, web
    selected = normalize_work_mode(work_mode, mode)
    reg = ToolRegistry()
    memory.register_memory_tools(reg)  # chat má alespoň paměť
    web.register_web_tools(reg)        # internet: web_search + web_fetch (všude)
    context.register_context_tools(reg)
    skills.register_skill_tools(reg)
    documents.register_document_tools(reg)
    if selected in ("discussion", "research"):
        return reg
    fs.register_fs_tools(reg)
    vision.register_vision_tools(reg)
    if selected == "writing":
        return reg
    context.register_coding_context_tools(reg)
    git.register_git_tools(reg)
    shell.register_shell_tools(reg)
    if selected == "computer":
        computer.register_computer_tools(reg)
    return reg
