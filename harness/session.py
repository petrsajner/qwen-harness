"""Perzistence konverzace - JSONL záznamy + obrázky v session adresáři.

Zprávy ukládáme ve zjednodušené podobě (obrázky jako reference na soubory),
pro API volání se renderují do OpenAI formátu včetně base64 data URL.
"""
from __future__ import annotations

import base64
import copy
import json
import mimetypes
import shutil
import sqlite3
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from harness.config import Config
from harness.i18n import t

IMG_MIMES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}


class Session:
    def __init__(self, cfg: Config, session_id: str | None = None, system_prompt: str | None = None,
                 workspace: str | None = None, transient: bool = False,
                 work_mode: str | None = None):
        self.cfg = cfg
        self.id = session_id or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.dir = cfg.path("paths.sessions_dir") / self.id
        self.img_dir = self.dir / "images"
        self.messages: list[dict[str, Any]] = []
        # ne-destruktivní komprese: model vidí [system + souhrn + messages[cut:]],
        # uživatel kompletní messages (UI + JSONL zůstávají nedotčené)
        self.compression: dict[str, Any] | None = None  # {"cut": int, "summary": str}
        self.compression_rev = 0  # inkrement při každé změně (pro UI marker)
        self.meta: dict[str, Any] = {"workspace": workspace, "title": None,
                                     "created": time.time(), "updated": time.time(),
                                     "pinned_files": [], "work_mode": work_mode}
        # transient = nový neuložený chat: žije jen v paměti, na disk se zapíše
        # až s první uživatelskou zprávou (persist()) - neplní se prázdné chatty
        self.transient = transient
        if system_prompt:
            self.add("system", system_prompt)

    # -- přidávání zpráv ---------------------------------------------------
    def add(self, role: str, content: Any, *, images: list[Path] | None = None,
            tool_calls: list[dict] | None = None, tool_call_id: str | None = None,
            name: str | None = None, reasoning: str | None = None) -> dict:
        msg: dict[str, Any] = {"role": role, "content": content}
        if reasoning:
            msg["reasoning"] = str(reasoning)
        if tool_calls:
            msg["tool_calls"] = tool_calls
        if tool_call_id:
            msg["tool_call_id"] = tool_call_id
        if name:
            msg["name"] = name
        if self.transient and role == "user":
            self.persist()  # první skutečná zpráva → chat se zapisuje na disk
        if images:
            msg["images"] = [str(self._store_image(p)) for p in images]
        self.messages.append(msg)
        self._append_jsonl(msg)
        # 🧾 meta: titulek z prvního uživatelského dotazu + čas aktualizace
        if role == "user" and not self.meta.get("title") and isinstance(content, str) \
                and content.strip() and not content.startswith("["):
            self.meta["title"] = content.strip().replace("\n", " ")[:70]
        self.meta["updated"] = time.time()
        self.meta["message_count"] = len(self.messages)
        self._save_meta()
        return msg

    def _store_image(self, path: Path) -> Path:
        """Zkopíruje obrázek do session adresáře a vrátí novou cestu."""
        if self.transient:
            self.persist()
        self.img_dir.mkdir(parents=True, exist_ok=True)
        if self.img_dir in path.resolve().parents or path.parent == self.img_dir:
            return path  # už je v session (screenshoty apod.)
        dest = self.img_dir / f"{uuid.uuid4().hex[:8]}-{path.name}"
        shutil.copy2(path, dest)
        return dest

    # -- render pro API ----------------------------------------------------
    SUMMARY_PREFIX = ("[SESSION HISTORY SUMMARY - older conversation was auto-compressed. "
                      "Use it as context, do not re-ask the user about these facts:]\n\n")
    INTERNAL_USER_PREFIXES = (
        "[TASK PROTOCOL", "[PROGRESS UPDATE", "[FINAL SUMMARY",
        "[The following image", "[Interrupted by user]", "[RESEARCH PLAN",
        "[DYNAMIC TASK CONTEXT",
    )

    def _view_messages(self) -> list[dict]:
        """Zprávy, které VIDÍ MODEL (po aplikaci komprese)."""
        if not self.compression:
            return self.messages
        cut = min(self.compression["cut"], len(self.messages))
        head = self.messages[:1] if self.messages and self.messages[0]["role"] == "system" else []
        summary_msg = {"role": "user", "content": self.SUMMARY_PREFIX + self.compression["summary"]}
        return head + [summary_msg] + self.messages[cut:]

    def to_api_messages(self, max_images: int = 8, include_pins: bool = True) -> list[dict]:
        """Převeď na OpenAI formát; posledních max_images obrázků jako data URL."""
        view = self._view_messages()
        image_paths = [p for m in view for p in m.get("images", [])]
        recent = set(image_paths[-max_images:])
        out: list[dict] = []
        for m in view:
            m2 = {k: v for k, v in m.items() if k != "images"}
            if "reasoning" in m2:
                reasoning_val = m2.pop("reasoning", None)
                if reasoning_val and "reasoning_content" not in m2:
                    m2["reasoning_content"] = reasoning_val
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
        pinned = self.pinned_context_block() if include_pins else ""
        if pinned:
            out.append({"role": "user", "content": pinned})
        return out

    @staticmethod
    def _data_url(path: str | Path) -> str:
        path = Path(path)
        stat = path.stat()
        return Session._cached_data_url(str(path), stat.st_mtime_ns, stat.st_size)

    @staticmethod
    @lru_cache(maxsize=16)
    def _cached_data_url(path_str: str, _mtime_ns: int, _size: int) -> str:
        path = Path(path_str)
        mime = IMG_MIMES.get(path.suffix.lower(), "image/png")
        b64 = base64.b64encode(path.read_bytes()).decode()
        return f"data:{mime};base64,{b64}"

    # -- odhad kontextu / komprese -----------------------------------------
    IMAGE_TOKENS = 1400  # orientační počet tokenů na obrázek (po downscale)

    def estimate_context_tokens(self, include_pins: bool = True) -> int:
        """Odhad tokenů skutečně odesílaných do API (po omezení obrázků)."""
        import json as _json
        view = self._view_messages()
        image_paths = [p for m in view for p in m.get("images", [])]
        recent = set(image_paths[-8:])
        total = 0
        for m in view:
            c = m.get("content") or ""
            if isinstance(c, str):
                total += len(c)
            elif isinstance(c, list):
                for part in c:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            total += len(str(part.get("text", "")))
                        elif part.get("type") == "image_url":
                            total += self.IMAGE_TOKENS * 4  # v chars, přepočet níže
            total += sum(1 for p in m.get("images", []) if p in recent) * self.IMAGE_TOKENS * 4
            if m.get("tool_calls"):
                total += len(_json.dumps(m["tool_calls"], ensure_ascii=False))
        if include_pins:
            total += len(self.pinned_context_block())
        # ~3.6 znaku na token (mix češtiny, kódu, JSON)
        return total * 10 // 36

    def pin_context_file(self, path: Path) -> bool:
        resolved = str(path.resolve())
        pins = list(self.meta.get("pinned_files") or [])
        if resolved in pins:
            return False
        pins.append(resolved)
        self.meta["pinned_files"] = pins[-10:]
        self._save_meta()
        return True

    def unpin_context_file(self, path: Path | str) -> bool:
        resolved = str(Path(path).resolve())
        target_name = Path(path).name
        pins = list(self.meta.get("pinned_files") or [])
        match = None
        for p in pins:
            if p == resolved or p == str(path) or Path(p).name == target_name:
                match = p
                break
        if not match:
            return False
        pins.remove(match)
        self.meta["pinned_files"] = pins
        self._save_meta()
        return True

    def pinned_context_block(self, max_chars: int = 40_000) -> str:
        parts: list[str] = []
        used = 0
        for raw in self.meta.get("pinned_files") or []:
            path = Path(raw)
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            room = max_chars - used
            if room <= 0:
                break
            text = text[:room]
            parts.append(f"### {path}\n{text}")
            used += len(text)
        if not parts:
            return ""
        return "[PINNED PROJECT FILES - user-selected persistent context]\n\n" + "\n\n".join(parts)

    def context_breakdown(self) -> dict:
        import collections as _collections
        view = self._view_messages()
        counts = _collections.Counter(m.get("role", "other") for m in view)
        images = sum(len(m.get("images", [])) for m in view)
        pins = [raw for raw in self.meta.get("pinned_files") or [] if Path(raw).is_file()]
        return {
            "estimated_tokens": self.estimate_context_tokens(),
            "visible_messages": len(view),
            "total_messages": len(self.messages),
            "roles": dict(counts),
            "images": images,
            "pinned_files": pins,
            "compressed": bool(self.compression),
        }

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

    @classmethod
    def _is_user_boundary(cls, message: dict) -> bool:
        content = message.get("content")
        return (message.get("role") == "user" and isinstance(content, str)
                and not content.startswith(cls.INTERNAL_USER_PREFIXES))

    def compression_cut(self, min_keep: int = 6,
                        keep_tokens: int | None = None) -> int | None:
        """Najde user hranici, od které se má zachovat živý ocas konverzace."""
        msgs = self.messages
        head_len = 1 if msgs and msgs[0]["role"] == "system" else 0
        if len(msgs) - head_len <= min_keep:
            return None
        cut = None
        if keep_tokens is not None:
            acc = 0
            for i in range(len(msgs) - 1, head_len - 1, -1):
                acc += self._msg_tokens(msgs[i])
                if acc > keep_tokens:
                    break
                if self._is_user_boundary(msgs[i]) and (len(msgs) - i) >= min_keep:
                    cut = i
        else:
            rest = msgs[head_len:]
            for i in range(len(rest) - min_keep, 0, -1):
                if self._is_user_boundary(rest[i]):
                    cut = head_len + i
                    break
        if cut is None or cut <= head_len:
            return None
        if self.compression and cut <= self.compression["cut"]:
            return None
        return cut

    def compress_to_summary(self, summary: str, min_keep: int = 6,
                            keep_tokens: int | None = None,
                            cut: int | None = None) -> bool:
        """Zaregistruj kompresi kontextu (NE-destruktivně).

        Zprávy zůstávají v self.messages (UI + JSONL nedotčené); model od teď
        vidí [system + souhrn + messages[cut:]]. Cut vždy na hranici 'user'
        zprávy, aby se nerozbily dvojice assistant(tool_calls) → tool.

        keep_tokens: cílový rozpočet ponechané části (největší výhodnější cut,
        který se do něj vejde). Bez něj platí jen min_keep zpráv.
        """
        cut = cut if cut is not None else self.compression_cut(min_keep, keep_tokens)
        if cut is None:
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
                        if self._is_user_boundary(self.messages[i])), None)
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

    def persist(self) -> None:
        """Zapiš transient session na disk (celou) a opusť transient režim."""
        if not self.transient:
            return
        self.transient = False
        self.dir.mkdir(parents=True, exist_ok=True)
        self._save_meta()
        with open(self._jsonl, "w", encoding="utf-8") as f:
            for m in self.messages:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")

    def _append_jsonl(self, msg: dict) -> None:
        if self.transient:
            return
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
        self._update_history_index()

    def last_user_index(self) -> int | None:
        for index in range(len(self.messages) - 1, -1, -1):
            message = self.messages[index]
            content = message.get("content")
            if message.get("role") == "user" and isinstance(content, str) \
                    and not content.startswith(self.INTERNAL_USER_PREFIXES):
                return index
        return None

    def rewind_last_turn(self, keep_user: bool) -> str | None:
        index = self.last_user_index()
        if index is None:
            return None
        content = str(self.messages[index].get("content") or "")
        self.messages = self.messages[:index + 1 if keep_user else index]
        self._clear_compression()
        self.meta["message_count"] = len(self.messages)
        self.meta["updated"] = time.time()
        self._rewrite_jsonl()
        self._save_meta()
        return content

    def fork_at_last_user(self, system_prompt: str) -> "Session" | None:
        index = self.last_user_index()
        if index is None:
            return None
        fork = Session(self.cfg, system_prompt=system_prompt,
                       workspace=self.meta.get("workspace"), transient=False,
                       work_mode=self.meta.get("work_mode"))
        copied: list[dict] = [fork.messages[0]] if fork.messages else []
        for original in self.messages[1:index + 1]:
            message = copy.deepcopy(original)
            if message.get("images"):
                new_images: list[str] = []
                for raw in message["images"]:
                    try:
                        new_images.append(str(fork._store_image(Path(raw))))
                    except OSError:
                        continue
                message["images"] = new_images
            copied.append(message)
        fork.messages = copied
        title = self.meta.get("title") or t("New branch")
        fork.meta.update({
            "title": f"{title} {t('(fork)')}"[:100],
            "message_count": len(copied),
            "pinned_files": list(self.meta.get("pinned_files") or []),
            "updated": time.time(),
        })
        fork._rewrite_jsonl()
        fork._save_meta()
        return fork

    def export_markdown(self) -> Path:
        export_dir = self.dir / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        target = export_dir / f"{self.id}.md"
        lines = [f"# {self.meta.get('title') or 'Qwen chat'}", ""]
        role_names = {"user": "Uživatel", "assistant": "Asistent", "tool": "Nástroj"}
        for message in self.messages:
            role = message.get("role")
            content = message.get("content")
            if role == "system" or (role == "user" and isinstance(content, str)
                                    and content.startswith(self.INTERNAL_USER_PREFIXES)):
                continue
            if not content and message.get("tool_calls"):
                names = ", ".join(call.get("function", {}).get("name", "tool")
                                  for call in message["tool_calls"])
                content = f"Volání nástrojů: {names}"
            lines.extend([f"## {role_names.get(role, str(role))}", "", str(content or ""), ""])
            for image in message.get("images", []):
                lines.append(f"Příloha: `{Path(image).name}`\n")
        atomic = "\n".join(lines)
        target.write_text(atomic, encoding="utf-8", newline="\n")
        return target

    def export_jsonl(self) -> Path:
        export_dir = self.dir / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        target = export_dir / f"{self.id}.jsonl"
        with open(target, "w", encoding="utf-8") as handle:
            for message in self.messages:
                handle.write(json.dumps(message, ensure_ascii=False) + "\n")
        return target

    def _clear_compression(self) -> None:
        if self.compression is not None:
            self.compression = None
            self.compression_rev += 1
        self._compression_file.unlink(missing_ok=True)

    @property
    def _task_state_file(self) -> Path:
        return self.dir / "task-state.json"

    def save_task_state(self, state: dict) -> None:
        if self.transient:
            return
        state = {**state, "updated": time.time()}
        temporary = self._task_state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self._task_state_file)

    def load_task_state(self) -> dict:
        try:
            state = json.loads(self._task_state_file.read_text(encoding="utf-8"))
            return state if isinstance(state, dict) else {}
        except (OSError, ValueError):
            return {}

    @property
    def _compression_file(self) -> Path:
        return self.dir / "compression.json"

    @property
    def _meta_file(self) -> Path:
        return self.dir / "meta.json"

    def _save_meta(self) -> None:
        if self.transient:
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._meta_file.write_text(
                json.dumps(self.meta, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        self._update_history_index()

    def _update_history_index(self) -> None:
        if self.transient or not self._jsonl.exists():
            return
        try:
            from harness.history_index import HistoryIndex
            HistoryIndex(self.cfg.path("paths.sessions_dir")).reindex(
                self.id, self.meta, self.messages, source_mtime=self._jsonl.stat().st_mtime)
        except (OSError, ValueError, sqlite3.Error):
            pass

    def _load_meta(self) -> None:
        try:
            self.meta = json.loads(self._meta_file.read_text(encoding="utf-8"))
            self.meta.setdefault("workspace", None)
            self.meta.setdefault("title", None)
            self.meta.setdefault("pinned_files", [])
            self.meta.setdefault("work_mode", None)
        except (OSError, ValueError):
            pass

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
        s._load_meta()
        # titulek pro starší sessions bez meta
        if not s.meta.get("title"):
            for m in s.messages:
                if m["role"] == "user" and isinstance(m.get("content"), str) \
                        and m["content"].strip() and not m["content"].startswith("["):
                    s.meta["title"] = m["content"].strip().replace("\n", " ")[:70]
                    break
        s._load_compression()
        if system_prompt:
            if s.messages and s.messages[0]["role"] == "system":
                s.messages[0]["content"] = system_prompt
            else:
                s.messages.insert(0, {"role": "system", "content": system_prompt})
        return s

    @classmethod
    def import_jsonl(cls, cfg: Config, source: Path, system_prompt: str,
                     workspace: str | None = None,
                     work_mode: str | None = None) -> "Session":
        messages: list[dict] = []
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"Neplatný JSONL na řádku {number}: {exc}") from exc
            if not isinstance(message, dict) or message.get("role") not in {
                    "system", "user", "assistant", "tool"}:
                raise ValueError(f"Neplatná zpráva na řádku {number}")
            if message.get("images"):
                message["images"] = [raw for raw in message["images"] if Path(raw).is_file()]
            messages.append(message)
        if not messages:
            raise ValueError("Importovaný chat neobsahuje žádné zprávy")
        if messages[0].get("role") == "system":
            messages[0]["content"] = system_prompt
        else:
            messages.insert(0, {"role": "system", "content": system_prompt})
        session = cls(cfg, system_prompt=system_prompt, workspace=workspace,
                      work_mode=work_mode)
        session.messages = messages
        session.meta["message_count"] = len(messages)
        session.meta["workspace"] = workspace
        session.meta["work_mode"] = work_mode
        session.meta["title"] = next(
            (str(message.get("content", ""))[:70] for message in messages
             if message.get("role") == "user"
             and not str(message.get("content", "")).startswith(cls.INTERNAL_USER_PREFIXES)),
            "Importovaný chat",
        )
        session.meta["updated"] = time.time()
        session._rewrite_jsonl()
        session._save_meta()
        return session

    @classmethod
    def delete(cls, cfg: Config, session_id: str) -> bool:
        """Smaž session (celou složku včetně obrázků). Vrací True při úspěchu."""
        import shutil
        d = cfg.path("paths.sessions_dir") / session_id
        if d.exists() and d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            try:
                from harness.history_index import HistoryIndex
                HistoryIndex(cfg.path("paths.sessions_dir")).remove(session_id)
            except Exception:
                pass
            return True
        return False

    def adopt_workspace(self, workspace: str | None) -> None:
        """Přiřaď session projekt (workspace), pokud ho ještě nemá.

        Používá se u starých sessions bez meta - např. po načtení pod
        aktuálním projektem si ho 'osvojí' a objeví se v jeho historii.
        """
        if workspace and not self.meta.get("workspace"):
            self.meta["workspace"] = workspace
            self._save_meta()

    @staticmethod
    def list_sessions(cfg: Config, limit: int = 60) -> list[dict]:
        """Sessions s metadaty (workspace, titulek, časy) - nové první."""
        base = cfg.path("paths.sessions_dir")
        if not base.exists():
            return []
        out: list[dict] = []
        for d in base.iterdir():
            if not (d.is_dir() and (d / "messages.jsonl").exists()):
                continue
            meta: dict = {}
            try:
                meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            try:
                n = int(meta["message_count"])
            except (KeyError, TypeError, ValueError):
                n = sum(1 for _ in open(d / "messages.jsonl", encoding="utf-8"))
            title = meta.get("title")
            if not title:
                try:
                    for line in open(d / "messages.jsonl", encoding="utf-8"):
                        m = json.loads(line)
                        if m.get("role") == "user" and isinstance(m.get("content"), str) \
                                and m["content"].strip() and not m["content"].startswith("["):
                            title = m["content"].strip().replace("\n", " ")[:70]
                            break
                except (OSError, ValueError):
                    title = None
            out.append({
                "id": d.name,
                "messages": n,
                "workspace": meta.get("workspace"),
                "title": title or t("(untitled)"),
                "updated": float(meta.get("updated") or 0) or 0,
            })
        out.sort(key=lambda s: s["updated"], reverse=True)
        return out[:limit]

    @staticmethod
    def search_sessions(cfg: Config, query: str, limit: int = 30) -> list[dict]:
        query = (query or "").strip().lower()
        if not query:
            return []
        from harness.history_index import HistoryIndex
        return HistoryIndex(cfg.path("paths.sessions_dir")).search(query, limit)
