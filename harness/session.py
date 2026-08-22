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
        # ne-destruktivní komprese: model vidí [system + souhrn + messages[cut:]],
        # uživatel kompletní messages (UI + JSONL zůstávají nedotčené)
        self.compression: dict[str, Any] | None = None  # {"cut": int, "summary": str}
        self.compression_rev = 0  # inkrement při každé změně (pro UI marker)
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
    SUMMARY_PREFIX = ("[SESSION HISTORY SUMMARY - older conversation was auto-compressed. "
                      "Use it as context, do not re-ask the user about these facts:]\n\n")

    def _view_messages(self) -> list[dict]:
        """Zprávy, které VIDÍ MODEL (po aplikaci komprese)."""
        if not self.compression:
            return self.messages
        cut = min(self.compression["cut"], len(self.messages))
        head = self.messages[:1] if self.messages and self.messages[0]["role"] == "system" else []
        summary_msg = {"role": "user", "content": self.SUMMARY_PREFIX + self.compression["summary"]}
        return head + [summary_msg] + self.messages[cut:]

    def to_api_messages(self, max_images: int = 8) -> list[dict]:
        """Převeď na OpenAI formát; posledních max_images obrázků jako data URL."""
        view = self._view_messages()
        image_paths = [p for m in view for p in m.get("images", [])]
        recent = set(image_paths[-max_images:])
        out: list[dict] = []
        for m in view:
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

    # -- odhad kontextu / komprese -----------------------------------------
    IMAGE_TOKENS = 1400  # orientační počet tokenů na obrázek (po downscale)

    def estimate_context_tokens(self) -> int:
        """Odhad tokenů skutečně odesílaných do API (po omezení obrázků)."""
        import json as _json
        total = 0
        for m in self.to_api_messages():
            c = m.get("content")
            if isinstance(c, str):
                total += len(c)
            elif isinstance(c, list):
                for part in c:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            total += len(str(part.get("text", "")))
                        elif part.get("type") == "image_url":
                            total += self.IMAGE_TOKENS * 4  # v chars, přepočet níže
            if m.get("tool_calls"):
                total += len(_json.dumps(m["tool_calls"], ensure_ascii=False))
        # ~3.6 znaku na token (mix češtiny, kódu, JSON)
        return total * 10 // 36

    def _msg_tokens(self, m: dict) -> int:
        """Orientační počet tokenů jedné zprávy (text + obrázky + tool_calls)."""
        import json as _json
        c = m.get("content") or ""
        if not isinstance(c, str):
            c = " ".join(str(p.get("text", "")) for p in c if isinstance(p, dict))
        n = len(str(c)) * 10 // 36
        if m.get("images"):
            n += len(m["images"]) * self.IMAGE_TOKENS
        if m.get("tool_calls"):
            n += len(_json.dumps(m["tool_calls"], ensure_ascii=False)) * 10 // 36
        return n

    def compress_to_summary(self, summary: str, min_keep: int = 6,
                            keep_tokens: int | None = None) -> bool:
        """Zaregistruj kompresi kontextu (NE-destruktivně).

        Zprávy zůstávají v self.messages (UI + JSONL nedotčené); model od teď
        vidí [system + souhrn + messages[cut:]]. Cut vždy na hranici 'user'
        zprávy, aby se nerozbily dvojice assistant(tool_calls) → tool.

        keep_tokens: cílový rozpočet ponechané části (největší výhodnější cut,
        který se do něj vejde). Bez něj platí jen min_keep zpráv.
        """
        msgs = self.messages
        head_len = 1 if msgs and msgs[0]["role"] == "system" else 0
        if len(msgs) - head_len <= min_keep:
            return False
        cut = None
        if keep_tokens is not None:
            acc = 0
            for i in range(len(msgs) - 1, head_len - 1, -1):
                acc += self._msg_tokens(msgs[i])
                if acc > keep_tokens:
                    break
                if msgs[i].get("role") == "user" and (len(msgs) - i) >= min_keep:
                    cut = i  # nejhlubší hranice, jejíž ocas se vejde do rozpočtu
        else:
            rest = msgs[head_len:]
            for i in range(len(rest) - min_keep, 0, -1):
                if rest[i]["role"] == "user":
                    cut = head_len + i
                    break
        if cut is None or cut <= head_len:
            return False
        if self.compression and cut <= self.compression["cut"]:
            return False  # nový cut musí být za starým
        self.compression = {"cut": cut, "summary": summary}
        self.compression_rev += 1
        self._save_compression()
        return True

    def trim_to_budget(self, budget_tokens: int, min_keep: int = 6) -> bool:
        """Tvrdý fallback: posuň kompresní cut dál (historie zůstává pro UI)."""
        changed = False
        while self.estimate_context_tokens() > budget_tokens and len(self.messages) > min_keep + 1:
            cur = self.compression["cut"] if self.compression else 1
            # nejbližší user hranice za aktuálním cutem
            nxt = next((i for i in range(cur + 1, len(self.messages) - min_keep + 1)
                        if self.messages[i]["role"] == "user"), None)
            if nxt is None:
                break
            self.compression = {
                "cut": nxt,
                "summary": (self.compression["summary"] if self.compression
                            else "(older context hard-trimmed without summary)"),
            }
            changed = True
        if changed:
            self.compression_rev += 1
            self._save_compression()
        return self.estimate_context_tokens() <= budget_tokens

    # -- perzistence ---------------------------------------------------------
    @property
    def _jsonl(self) -> Path:
        return self.dir / "messages.jsonl"

    def _append_jsonl(self, msg: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self._jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def _rewrite_jsonl(self) -> None:
        """Přepiš celý JSONL (po opravách historie)."""
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._jsonl.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for m in self.messages:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        tmp.replace(self._jsonl)

    @property
    def _compression_file(self) -> Path:
        return self.dir / "compression.json"

    def _save_compression(self) -> None:
        if self.compression:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._compression_file.write_text(
                json.dumps(self.compression, ensure_ascii=False), encoding="utf-8")

    def _load_compression(self) -> None:
        try:
            data = json.loads(self._compression_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "cut" in data and "summary" in data:
                self.compression = data
                self.compression_rev += 1
        except (OSError, ValueError):
            pass

    @classmethod
    def load(cls, cfg: Config, session_id: str, system_prompt: str | None = None) -> "Session":
        s = cls(cfg, session_id=session_id)
        f = s._jsonl
        if not f.exists():
            raise FileNotFoundError(f"Session {session_id} nenalezena ({f})")
        s.messages = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
        s._load_compression()
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
