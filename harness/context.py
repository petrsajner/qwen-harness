"""Správa kontextu - odhad tokenů, sumarizace (auto-komprese, handoff)."""
from __future__ import annotations

from typing import Any

SUMMARIZE_PROMPT = """You are creating a technical handoff summary of a work session between a user and a coding agent.
Summarize the conversation below into a COMPACT markdown summary (max ~500 words) with these sections:
1. **Goal** - what the user is working on (project, task)
2. **Done** - completed steps, created/modified files (full paths), key commands and their outcomes
3. **Decisions & context** - important decisions, constraints, user preferences
4. **Pending** - what remains to be done, next intended steps
5. **Key facts** - versions, error messages, paths, anything needed to continue seamlessly

Be precise with file paths and technical details. Do not include pleasantries.

CONVERSATION TRANSCRIPT:
"""


def render_messages_text(messages: list[dict], max_chars: int = 60_000) -> str:
    """Zpravy jako compact text pro sumarizaci (obrázky jen jako poznámka)."""
    lines: list[str] = []
    total = 0
    for m in messages:
        role = m.get("role", "?").upper()
        content = m.get("content") or ""
        if not isinstance(content, str):
            content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
        if m.get("tool_calls"):
            calls = ", ".join(
                f"{c['function']['name']}({(c['function'].get('arguments') or '')[:120]})"
                for c in m["tool_calls"])
            content = (content + f"\n[tool calls: {calls}]").strip()
        if m.get("images"):
            content += f" [+{len(m['images'])} image(s) attached]"
        line = f"{role}: {content}"
        if total + len(line) > max_chars:  # ber od začátku, stoříz zbytek
            line = f"{role}: [...transcript truncated at {max_chars} chars...]"
            lines.append(line)
            break
        lines.append(line)
        total += len(line)
    return "\n\n".join(lines)


def summarize_messages(llm: Any, messages: list[dict]) -> str:
    """Nech model vytvořit souhrn konverzace (rychle, bez thinking, bez nástrojů)."""
    transcript = render_messages_text(messages)
    res = llm.ask(
        [
            {"role": "system", "content": "You are a precise technical summarizer. Output only the summary."},
            {"role": "user", "content": SUMMARIZE_PROMPT + transcript},
        ],
        max_tokens=1400,
        sampling=llm.cfg.sampling(False),
        thinking=False,
    )
    text = (res.content or "").strip()
    if not text:
        raise RuntimeError("model vrátil prázdný souhrn")
    return text
