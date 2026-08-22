"""Načítání a zpřístupnění konfigurace (config.yaml + defaulty)."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict[str, Any] = {
    "server": {
        "host": "127.0.0.1",
        "port": 8080,
        "n_gpu_layers": 999,
        "extra_args": [],
    },
    "models": {},
    "default_model": "q4",
    "sampling": {
        "thinking": {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "presence_penalty": 0.0},
        "non_thinking": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 1.5},
    },
    "thinking": True,
    "agent": {
        "mode": "agent",
        "autonomy": "supervised",
        "max_steps": 40,
        "semi_max_steps": 15,
        "shell_timeout": 60,
        "workspace": None,
    },
    "computer": {
        "screenshot_max_edge": 1920,
        "screenshot_grayscale": False,
        "failsafe": True,
        "pause_between_actions": 0.15,
    },
    "web": {"host": "127.0.0.1", "port": 7860},
    "paths": {
        "runtime_dir": "runtime",
        "llama_dir": "runtime/llama",
        "models_dir": "runtime/models",
        "sessions_dir": "sessions",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config:
    """Konfigurace s helpery pro cesty a modely."""

    def __init__(self, data: dict[str, Any], root: Path = ROOT):
        self.data = data
        self.root = root

    # -- cesty -------------------------------------------------------------
    def path(self, dotted: str) -> Path:
        """Vrátí absolutní cestu z paths.* (relativní řeší od kořenu projektu)."""
        node: Any = self.data
        for part in dotted.split("."):
            node = node[part]
        p = Path(str(node))
        return p if p.is_absolute() else (self.root / p)

    # -- modely ------------------------------------------------------------
    def model_key(self) -> str:
        key = self.data.get("default_model", "q4")
        return key if key in self.data.get("models", {}) else next(iter(self.data["models"]), "q4")

    def model(self, key: str | None = None) -> dict:
        key = key or self.model_key()
        return self.data["models"][key]

    def model_file(self, key: str | None = None) -> Path:
        return self.path("paths.models_dir") / self.model(key)["file"]

    def mmproj_file(self, key: str | None = None) -> Path:
        return self.path("paths.models_dir") / self.model(key)["mmproj"]

    # -- server ------------------------------------------------------------
    @property
    def base_url(self) -> str:
        s = self.data["server"]
        return f"http://{s['host']}:{s['port']}"

    def llama_server_exe(self) -> Path | None:
        """Najde llama-server.exe v runtime/llama (i vnořený, např. ve verzi CUDA)."""
        llama_dir = self.path("paths.llama_dir")
        if not llama_dir.exists():
            return None
        return next(iter(sorted(llama_dir.rglob("llama-server.exe"))), None)

    # -- sampling ----------------------------------------------------------
    def sampling(self, thinking: bool | None = None) -> dict:
        if thinking is None:
            thinking = self.data.get("thinking", True)
        key = "thinking" if thinking else "non_thinking"
        return dict(self.data["sampling"][key])

    # -- zkratky -----------------------------------------------------------
    @property
    def agent(self) -> dict:
        return self.data["agent"]

    @property
    def computer(self) -> dict:
        return self.data["computer"]

    @property
    def web(self) -> dict:
        return self.data["web"]


def load_config(path: Path | None = None) -> Config:
    path = path or (ROOT / "config.yaml")
    user: dict = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
    return Config(_deep_merge(DEFAULTS, user))
