"""LLM klient - OpenAI-kompatibilní API llama-serveru (streaming, tool calling, reasoning)."""
from __future__ import annotations

import json
import re
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from openai import OpenAI

from harness.config import Config

@dataclass
class AssistantResult:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    stopped: bool = False
    usage: dict = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


REASONING_EFFORTS = ("xhigh", "high", "medium", "low")
SENTENCE_END_RE = re.compile(r"[.!?…](?:[\"'»”\)\]]*)\s*$")


def _template_kwargs(cfg: Config) -> dict:
    """Thinking on/off + hloubka uvažování (reasoning_effort) přes chat template."""
    if not cfg.data.get("thinking", True):
        return {"chat_template_kwargs": {"enable_thinking": False}}
    if not cfg.model().get("supports_reasoning_effort", True):
        return {"chat_template_kwargs": {"enable_thinking": True}}
    effort = cfg.data.get("reasoning_effort")
    if effort in REASONING_EFFORTS:
        return {"chat_template_kwargs": {"reasoning_effort": effort}}
    return {}


class ThinkStreamParser:
    """Stream parser separating reasoning from content across <think> tags."""

    def __init__(self, on_text: Callable[[str], None] | None = None,
                 on_reasoning: Callable[[str], None] | None = None) -> None:
        self.on_text = on_text
        self.on_reasoning = on_reasoning
        self.in_think = False
        self.buffer = ""

    def feed(self, chunk: str) -> tuple[list[str], list[str]]:
        """Processes a chunk of delta.content and returns (text_parts, reasoning_parts)."""
        text_out: list[str] = []
        reasoning_out: list[str] = []
        self.buffer += chunk

        while self.buffer:
            if not self.in_think:
                start_idx = self.buffer.find("<think>")
                if start_idx != -1:
                    before = self.buffer[:start_idx]
                    if before:
                        text_out.append(before)
                        if self.on_text:
                            self.on_text(before)
                    self.in_think = True
                    self.buffer = self.buffer[start_idx + len("<think>"):].lstrip("\r\n")
                    continue

                matched = False
                for i in range(1, min(len("<think>"), len(self.buffer) + 1)):
                    if "<think>".startswith(self.buffer[-i:]):
                        safe = self.buffer[:-i]
                        if safe:
                            text_out.append(safe)
                            if self.on_text:
                                self.on_text(safe)
                        self.buffer = self.buffer[-i:]
                        matched = True
                        break
                if matched:
                    break
                text_out.append(self.buffer)
                if self.on_text:
                    self.on_text(self.buffer)
                self.buffer = ""
            else:
                end_idx = self.buffer.find("</think>")
                if end_idx != -1:
                    thought = self.buffer[:end_idx]
                    if thought:
                        reasoning_out.append(thought)
                        if self.on_reasoning:
                            self.on_reasoning(thought)
                    self.in_think = False
                    self.buffer = self.buffer[end_idx + len("</think>"):].lstrip("\r\n")
                    continue

                matched = False
                for i in range(1, min(len("</think>"), len(self.buffer) + 1)):
                    if "</think>".startswith(self.buffer[-i:]):
                        safe = self.buffer[:-i]
                        if safe:
                            reasoning_out.append(safe)
                            if self.on_reasoning:
                                self.on_reasoning(safe)
                        self.buffer = self.buffer[-i:]
                        matched = True
                        break
                if matched:
                    break
                reasoning_out.append(self.buffer)
                if self.on_reasoning:
                    self.on_reasoning(self.buffer)
                self.buffer = ""
        return text_out, reasoning_out

    def flush(self) -> tuple[list[str], list[str]]:
        """Flush any remaining buffered text at end of stream."""
        text_out: list[str] = []
        reasoning_out: list[str] = []
        if self.buffer:
            if self.in_think:
                reasoning_out.append(self.buffer)
                if self.on_reasoning:
                    self.on_reasoning(self.buffer)
            else:
                text_out.append(self.buffer)
                if self.on_text:
                    self.on_text(self.buffer)
            self.buffer = ""
        return text_out, reasoning_out


class LLMClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        import httpx
        # read=300s: max mezera mezi bajty (prompt eval 96k ctx trvá ~80s bez výstupu);
        # místo výchozích 600s celkových - zaseknutý stream umře dřív
        self.client = OpenAI(
            base_url=cfg.base_url + "/v1", api_key="local",
            max_retries=0,
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=30.0),
        )
        self.model_name = "local-model"  # llama-server akceptuje cokoliv

    # ------------------------------------------------------------------
    def stream(self, messages: list[dict], tools: list[dict] | None = None,
               sampling: dict | None = None,
               thinking: bool | None = None,
               on_text: Callable[[str], None] | None = None,
               on_reasoning: Callable[[str], None] | None = None,
               on_tool_delta: Callable[[str, str], None] | None = None,
               should_stop: Callable[[], bool] | None = None) -> AssistantResult:
        """Streamující volání; vrací složený výsledek (text + tool_calls)."""
        s = dict(sampling or self.cfg.sampling())
        extra_body = _template_kwargs(self.cfg)
        if thinking is not None:
            extra_body["chat_template_kwargs"] = {"enable_thinking": bool(thinking)}
        if "top_k" in s:
            extra_body["top_k"] = s.pop("top_k")
        if "min_p" in s:  # OpenAI SDK min_p nezna - llama-server pres extra_body (Nemotron)
            extra_body["min_p"] = s.pop("min_p")
        params: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            **s,
        }
        if tools:
            params["tools"] = tools
        if extra_body:
            params["extra_body"] = extra_body

        res = AssistantResult()
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tc_acc: dict[int, dict] = {}
        stop_started = None
        parser = ThinkStreamParser(on_text=on_text, on_reasoning=on_reasoning)
        if should_stop and should_stop():
            res.stopped = True
            return res
        inbox = queue.Queue(maxsize=32)
        cancelled = threading.Event()
        stream_box = {}

        def publish(kind, value):
            while not cancelled.is_set():
                try:
                    inbox.put((kind, value), timeout=0.1)
                    return
                except queue.Full:
                    pass

        def receive():
            try:
                stream = self.client.chat.completions.create(**params)
                stream_box["stream"] = stream
                if not cancelled.is_set():
                    for chunk in stream:
                        if cancelled.is_set():
                            break
                        publish("chunk", chunk)
                publish("done", None)
            except Exception as exc:
                publish("error", exc)
            finally:
                close = getattr(stream_box.get("stream"), "close", None)
                if callable(close):
                    close()

        worker = threading.Thread(target=receive, name="llm-transport", daemon=True)
        worker.start()
        last_chunk_at = time.monotonic()
        last_probe_at = 0.0
        idle_probes = 0
        try:
            while True:
                if should_stop and should_stop():
                    stop_started = stop_started or time.monotonic()
                    visible = "".join(text_parts)
                    if (not visible or parser.in_think or tc_acc
                            or SENTENCE_END_RE.search(visible)
                            or time.monotonic() - stop_started >= 0.75):
                        res.stopped = True
                        break
                now = time.monotonic()
                if now - last_chunk_at > 90 and now - last_probe_at > 10:
                    from harness.servermgmt import slots_processing
                    last_probe_at = now
                    idle_probes = idle_probes + 1 if slots_processing(self.cfg) is False else 0
                    if idle_probes >= 2:
                        raise RuntimeError("Model connection stalled: the server is idle and no output arrived")
                try:
                    kind, chunk = inbox.get(timeout=0.05)
                except queue.Empty:
                    continue
                if kind == "done":
                    res.stopped = stop_started is not None
                    break
                if kind == "error":
                    raise chunk
                last_chunk_at = time.monotonic()
                idle_probes = 0
                usage = getattr(chunk, "usage", None)
                if usage:
                    res.usage = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue
                # reasoning (llama.cpp posílá reasoning_content, případně reasoning)
                r = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                if r:
                    reasoning_parts.append(r)
                    if on_reasoning:
                        on_reasoning(r)
                if delta.content:
                    tp, rp = parser.feed(delta.content)
                    if tp:
                        text_parts.extend(tp)
                    if rp:
                        reasoning_parts.extend(rp)
                for tc in delta.tool_calls or []:
                    if tc.index is None:
                        tc.index = 0
                    slot = tc_acc.setdefault(
                        tc.index, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            slot["name"] += tc.function.name
                        if tc.function.arguments:
                            slot["arguments"] += tc.function.arguments
                        if on_tool_delta and (tc.function.name or tc.function.arguments):
                            on_tool_delta(tc.function.name or "", tc.function.arguments or "")

            # Flush parsing buffer na konci streamu
            ftp, frp = parser.flush()
            if ftp:
                text_parts.extend(ftp)
            if frp:
                reasoning_parts.extend(frp)
        finally:
            cancelled.set()
            close = getattr(stream_box.get("stream"), "close", None)
            if callable(close):
                closer = threading.Thread(target=close, name="llm-close", daemon=True)
                closer.start()
                closer.join(timeout=0.2)
            worker.join(timeout=0.2)

        res.content = "".join(text_parts)
        res.reasoning = "".join(reasoning_parts)
        res.tool_calls = [] if res.stopped else [
            {
                "id": slot["id"] or f"call_{i}",
                "type": "function",
                "function": {"name": slot["name"], "arguments": slot["arguments"]},
            }
            for i, slot in sorted(tc_acc.items())
        ]
        self.last_usage = res.usage
        return res

    # ------------------------------------------------------------------
    def ask(self, messages: list[dict], tools: list[dict] | None = None,
            sampling: dict | None = None,
            thinking: bool | None = None) -> AssistantResult:
        """Ne-streamující volání (jednodušší, pro krátké požadavky).

        thinking=None → podle cfg; False → vynuceně vypnutý thinking (sumarizace).
        """
        s = dict(sampling or self.cfg.sampling())
        extra_body: dict[str, Any] = {}
        if "top_k" in s:
            extra_body["top_k"] = s.pop("top_k")
        if "min_p" in s:  # OpenAI SDK min_p nezna - llama-server pres extra_body (Nemotron)
            extra_body["min_p"] = s.pop("min_p")
        if thinking is not None:
            extra_body["chat_template_kwargs"] = {"enable_thinking": bool(thinking)}
        params: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            **s,
        }
        if thinking is None:
            extra_body.update(_template_kwargs(self.cfg))
        if tools:
            params["tools"] = tools
        if extra_body:
            params["extra_body"] = extra_body
        resp = self.client.chat.completions.create(**params)
        msg = resp.choices[0].message
        raw_content = msg.content or ""
        r = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None) or ""
        think_match = re.search(r"<think>(.*?)</think>", raw_content, re.DOTALL)
        if think_match:
            extracted_thought = think_match.group(1).strip()
            if not r:
                r = extracted_thought
            raw_content = re.sub(r"<think>.*?</think>", "", raw_content, flags=re.DOTALL).lstrip("\r\n")
        res = AssistantResult(content=raw_content, reasoning=r)
        for tc in msg.tool_calls or []:
            res.tool_calls.append({
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
            })
        return res

    # ------------------------------------------------------------------
    def health(self) -> bool:
        try:
            self.client.models.list()
            return True
        except Exception:
            return False


def parse_tool_arguments(raw: str) -> dict:
    """Bezpečné parsování argumentů tool callu (model občas pošle nevalidní JSON)."""
    if not raw or not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # fallback: zkusit najít první {...} blok
        start, end = raw.find("{"), raw.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                pass
        raise ValueError(f"Nepodařilo se parsovat argumenty tool callu: {raw[:200]!r}")
