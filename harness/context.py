"""Správa kontextu - odhad tokenů, sumarizace (auto-komprese, handoff)."""
from __future__ import annotations

import hashlib
from pathlib import Path
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
        lines.append(f"{role}: {content}")
    transcript = "\n\n".join(lines)
    if len(transcript) <= max_chars:
        return transcript
    marker = "\n\n[...older transcript middle truncated...]\n\n"
    head_chars = max_chars // 4
    tail_chars = max_chars - head_chars - len(marker)
    return transcript[:head_chars] + marker + transcript[-tail_chars:]


def summarize_messages(llm: Any, messages: list[dict], should_stop=None) -> str:
    """Summarize every part, retaining mode-specific facts and traceable message IDs."""
    mode = llm.cfg.data.get("work_mode", "discussion")
    focus = {
        "discussion": "goals, decisions, perspectives, unresolved questions and user preferences",
        "research": "claims, exact source IDs and URLs, contradictions, uncertainty and unanswered questions",
        "writing": "outline, voice, style, characters, chronology, continuity and approved wording",
        "development": "architecture, file paths, changes, test evidence, errors and next steps",
        "computer": "application state, completed actions, file paths and next steps",
    }.get(mode, "goals, decisions, facts and next steps")
    prompt = (f"Create a continuation summary for {mode}. Preserve {focus}. "
              "Keep explicit user requirements and accepted decisions, including negative constraints. "
              "Distinguish completed work from proposals. Preserve message references and source links "
              "so missing detail can be retrieved with read_chat_history. Be concise without losing "
              "relevant facts. Reply in the conversation language.\n\n")
    lines = []
    for i, message in enumerate(messages):
        reference = message.get("id", str(i))
        content = render_messages_text([message], max_chars=2**63 - 1)
        lines.append(f"[Message {reference}]\n{content}")
    transcript = "\n\n".join(lines)
    budget = max(8000, int(llm.cfg.context_size() * 0.45 * 3.6))
    cache = llm.cfg.path("paths.runtime_dir") / "summary-cache"
    cache.mkdir(parents=True, exist_ok=True)

    def summarize(text):
        if should_stop and should_stop():
            raise RuntimeError("sumarizace zastavena uživatelem")
        key = hashlib.sha256((mode + prompt + text).encode()).hexdigest()
        path = cache / (key + ".md")
        if path.is_file():
            return path.read_text(encoding="utf-8")
        res = llm.stream([
            {"role": "system", "content": "Summarize faithfully; preserve requirements and references."},
            {"role": "user", "content": prompt + text}],
            sampling=llm.cfg.sampling(False), thinking=False, should_stop=should_stop)
        if res.stopped:
            raise RuntimeError("sumarizace zastavena uživatelem")
        summary = (res.content or "").strip()
        if not summary:
            raise RuntimeError("model vrátil prázdný souhrn")
        from harness.changes import atomic_write_text
        atomic_write_text(path, summary)
        return summary

    while len(transcript) > budget:
        merged = "\n\n".join(summarize(transcript[i:i + budget])
                              for i in range(0, len(transcript), budget))
        if len(merged) >= len(transcript):
            raise RuntimeError("Souhrn nezmenšil kontext; původní podklady zůstaly uložené")
        transcript = merged
    return summarize(transcript)
