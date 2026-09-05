"""Project decisions with explicit status and links to their originating conversation."""
import json
import time
import uuid
from pathlib import Path

from harness.changes import atomic_write_text


class DecisionStore:
    def __init__(self, workspace):
        self.path = Path(workspace) / ".qwen" / "decisions.json" if workspace else None

    def list(self):
        if not self.path:
            return []
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def save(self, text, session_id, status="proposed", decision_id=None):
        if not self.path:
            raise ValueError("Select a project to save a project decision")
        if status not in ("proposed", "accepted", "retired"):
            raise ValueError("Unknown decision status")
        items = self.list()
        item = next((i for i in items if i["id"] == decision_id), None)
        if item is None:
            item = {"id": uuid.uuid4().hex, "created": time.time(), "source_session": session_id}
            items.append(item)
        item.update(text=text, status=status, updated=time.time())
        atomic_write_text(self.path, json.dumps(items, ensure_ascii=False, indent=2))
        return item

    def context(self):
        accepted = [item for item in self.list() if item["status"] == "accepted"]
        if not accepted:
            return ""
        return "## ACCEPTED PROJECT DECISIONS\n" + "\n".join(
            f"- {item['text']} (source chat: {item['source_session']})" for item in accepted)
