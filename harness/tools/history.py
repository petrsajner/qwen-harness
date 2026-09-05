"""Targeted retrieval of original conversation details after compression."""
import json

from harness.history_index import HistoryIndex
from harness.session import Session
from harness.tools.base import Tool
from harness.safety import Risk


class SearchChatHistoryTool(Tool):
    name = "search_chat_history"
    description = "Find earlier conversations and their IDs by a word or phrase. Read originals with read_chat_history."
    parallel_safe = True
    parameters = {"query": {"type": "string"}}
    required = ["query"]

    def run(self, ctx, query):
        return json.dumps(HistoryIndex(ctx.cfg.path("paths.sessions_dir")).search(query), ensure_ascii=False)


class ReadChatHistoryTool(Tool):
    name = "read_chat_history"
    description = "Read original stored messages, including details omitted from model context. Start is zero-based; use next_start for more."
    parallel_safe = True
    parameters = {"session_id": {"type": "string"}, "start": {"type": "integer"},
                  "count": {"type": "integer"}, "query": {"type": "string"}}

    def run(self, ctx, session_id="", start=0, count=20, query=""):
        session = ctx.session if not session_id or session_id == ctx.session.id else Session.load(ctx.cfg, session_id)
        messages = [m for m in session.messages if m.get("role") != "system"]
        if query:
            messages = [m for m in messages if query.casefold() in str(m.get("content", "")).casefold()]
        start = max(0, int(start))
        count = max(1, int(count))
        selected = messages[start:start + count]
        return json.dumps({"session_id": session.id, "messages": selected,
                           "next_start": start + count if start + count < len(messages) else None,
                           "total": len(messages)}, ensure_ascii=False)


def register_history_tools(registry):
    registry.register(SearchChatHistoryTool())
    registry.register(ReadChatHistoryTool())
    registry.register(ProjectDecisionTool())


class ProjectDecisionTool(Tool):
    name = "project_decisions"
    risk = Risk.WRITE
    description = "List project decisions or propose a new decision with its source chat. Only explicitly accepted user decisions should be marked accepted."
    parameters = {"text": {"type": "string"}, "status": {"type": "string", "enum": ["proposed", "accepted", "retired"]},
                  "decision_id": {"type": "string"}}

    def run(self, ctx, text="", status="proposed", decision_id=None):
        from harness.decisions import DecisionStore
        store = DecisionStore(ctx.project_workspace)
        result = store.save(text, ctx.session.id, status, decision_id) if text else store.list()
        return json.dumps(result, ensure_ascii=False)
