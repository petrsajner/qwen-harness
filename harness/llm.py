"""LLM klient - OpenAI-kompatibilní API llama-serveru (streaming, tool calling, reasoning)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from openai import OpenAI

from harness.config import Config

DEFAULT_MAX_TOKENS = 16384


@dataclass
class AssistantResult:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[dict] = field(default_factory=list)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


def _template_kwargs(cfg: Config) -> dict:
    """Vypnutí thinking režimu přes chat template (Qwen3.8)."""
    if not cfg.data.get("thinking", True):
        return {"chat_template_kwargs": {"thinking": False}}
    return {}


class LLMClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        import httpx
        # read=300s: max mezera mezi bajty (prompt eval 96k ctx trvá ~80s bez výstupu);
        # místo výchozích 600s celkových - zaseknutý stream umře dřív
        self.client = OpenAI(
            base_url=cfg.base_url + "/v1", api_key="local",
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=30.0),
        )
        self.model_name = "local-model"  # llama-server akceptuje cokoliv

    # ------------------------------------------------------------------
    def stream(self, messages: list[dict], tools: list[dict] | None = None,
               max_tokens: int = DEFAULT_MAX_TOKENS, sampling: dict | None = None,
               on_text: Callable[[str], None] | None = None,
               on_reasoning: Callable[[str], None] | None = None) -> AssistantResult:
        """Streamující volání; vrací složený výsledek (text + tool_calls)."""
        s = dict(sampling or self.cfg.sampling())
        extra_body = {}
        if "top_k" in s:
            extra_body["top_k"] = s.pop("top_k")
        params: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens,
            **s,
        }
        params.setdefault("extra_body", {}).update(_template_kwargs(self.cfg))
        if tools:
            params["tools"] = tools
        if extra_body:
            params["extra_body"] = extra_body

        res = AssistantResult()
        tc_acc: dict[int, dict] = {}

        stream = self.client.chat.completions.create(**params)
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            # reasoning (llama.cpp posílá reasoning_content, případně reasoning)
            r = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            if r:
                res.reasoning += r
                if on_reasoning:
                    on_reasoning(r)
            if delta.content:
                res.content += delta.content
                if on_text:
                    on_text(delta.content)
            for tc in delta.tool_calls or []:
                if tc.index is None:
                    tc.index = 0
                slot = tc_acc.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        slot["name"] += tc.function.name
                    if tc.function.arguments:
                        slot["arguments"] += tc.function.arguments

        res.tool_calls = [
            {
                "id": slot["id"] or f"call_{i}",
                "type": "function",
                "function": {"name": slot["name"], "arguments": slot["arguments"]},
            }
            for i, slot in sorted(tc_acc.items())
        ]
        return res

    # ------------------------------------------------------------------
    def ask(self, messages: list[dict], tools: list[dict] | None = None,
            max_tokens: int = DEFAULT_MAX_TOKENS, sampling: dict | None = None,
            thinking: bool | None = None) -> AssistantResult:
        """Ne-streamující volání (jednodušší, pro krátké požadavky).

        thinking=None → podle cfg; False → vynuceně vypnutý thinking (sumarizace).
        """
        s = dict(sampling or self.cfg.sampling())
        extra_body: dict[str, Any] = {}
        if "top_k" in s:
            extra_body["top_k"] = s.pop("top_k")
        if thinking is not None:
            extra_body["chat_template_kwargs"] = {"thinking": bool(thinking)}
        params: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            **s,
        }
        if thinking is None:
            params.setdefault("extra_body", {}).update(_template_kwargs(self.cfg))
        if tools:
            params["tools"] = tools
        if extra_body:
            params.setdefault("extra_body", {}).update(extra_body)
        resp = self.client.chat.completions.create(**params)
        msg = resp.choices[0].message
        res = AssistantResult(content=msg.content or "")
        r = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
        if r:
            res.reasoning = r
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
