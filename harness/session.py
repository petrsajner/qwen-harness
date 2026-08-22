"""Perzistence konverzace - JSONL záznamy + obrázky v session adresáři.

Zprávy ukládáme ve zjednodušené podobě (obrázky jako reference na soubory),
pro API volání se renderují do OpenAI formátu včetně base64 data URL.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from harness.config import Config

IMG_MIMES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}


class Session:
    def __init__(self, cfg: Config, session_id: str | None = None, system_prompt: str | None = None):
        self.cfg = cfg
        self.id = session_id or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.dir = cfg.path("paths.sessions_dir") / self.id
        self.img_dir = self.dir / "images"
        self.messages: list[dict[str, Any]] = []
        if system_prompt:
            self.add("system", system_prompt)

    # -- přidávání zpráv ---------------------------------------------------
    def add(self, role: str, content: Any, *, images: list[Path] | None = None,
            tool_calls: list[dict] | None = None, tool_call_id: str | None = None,
            name: str | None = None) -> dict:
        msg: dict[str, Any] = {"role": role, "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        if tool_call_id:
            msg["tool_call_id"] = tool_call_id
        if name:
            msg["name"] = name
        if images:
            msg["images"] = [str(self._store_image(p)) for p in images]
        self.messages.append(msg)
        self._append_jsonl(msg)
        return msg

    def _store_image(self, path: Path) -> Path:
        """Zkopíruje obrázek do session adresáře a vrátí novou cestu."""
        self.img_dir.mkdir(parents=True, exist_ok=True)
        if self.img_dir in path.resolve().parents or path.parent == self.img_dir:
            return path  # už je v session (screenshoty apod.)
        dest = self.img_dir / f"{uuid.uuid4().hex[:8]}-{path.name}"
        shutil.copy2(path, dest)
        return dest

    # -- render pro API ----------------------------------------------------
    def to_api_messages(self, max_images: int = 8) -> list[dict]:
        """Převeď na OpenAI formát; posledních max_images obrázků jako data URL."""
        image_paths = [p for m in self.messages for p in m.get("images", [])]
        recent = set(image_paths[-max_images:])
        out: list[dict] = []
        for m in self.messages:
            m2 = {k: v for k, v in m.items() if k != "images"}
            imgs = [p for p in m.get("images", []) if p in recent]
            if not imgs:
                if not m2.get("content") and not m2.get("tool_calls"):
                    continue
                out.append(m2)
                continue
            parts: list[dict] = []
            if m2.get("content"):
                parts.append({"type": "text", "text": str(m2["content"])})
            else:
                parts.append({"type": "text", "text": "[image]"})
            for p in imgs:
                parts.append({"type": "image_url", "image_url": {"url": self._data_url(p)}})
            m2["content"] = parts
            out.append(m2)
        return out

    @staticmethod
    def _data_url(path: str | Path) -> str:
        path = Path(path)
        mime = IMG_MIMES.get(path.suffix.lower(), "image/png")
        b64 = base64.b64encode(path.read_bytes()).decode()
        return f"data:{mime};base64,{b64}"

    # -- perzistence ---------------------------------------------------------
    @property
    def _jsonl(self) -> Path:
        return self.dir / "messages.jsonl"

    def _append_jsonl(self, msg: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self._jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    @classmethod
    def load(cls, cfg: Config, session_id: str, system_prompt: str | None = None) -> "Session":
        s = cls(cfg, session_id=session_id)
        f = s._jsonl
        if not f.exists():
            raise FileNotFoundError(f"Session {session_id} nenalezena ({f})")
        s.messages = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
        if system_prompt:
            if s.messages and s.messages[0]["role"] == "system":
                s.messages[0]["content"] = system_prompt
            else:
                s.messages.insert(0, {"role": "system", "content": system_prompt})
        return s

    @staticmethod
    def list_sessions(cfg: Config) -> list[dict]:
        base = cfg.path("paths.sessions_dir")
        if not base.exists():
            return []
        out = []
        for d in sorted(base.iterdir(), reverse=True):
            if d.is_dir() and (d / "messages.jsonl").exists():
                n = sum(1 for _ in open(d / "messages.jsonl", encoding="utf-8"))
                out.append({"id": d.name, "messages": n})
        return out
