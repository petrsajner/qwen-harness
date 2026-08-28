"""Načítání a zpřístupnění konfigurace (config.yaml + defaulty)."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent

BUILTIN_MODELS: dict[str, dict[str, Any]] = {
    "q4": {
        "alias": "Qwen3.8-27B Q4_K_M (16.5 GB, fast)",
        "status_label": "Qwen 3.8 27B · Q4",
        "repo": "unsloth/Qwen3.8-27B-GGUF",
        "file": "Qwen3.8-27B-UD-Q4_K_M.gguf",
        "mmproj": "mmproj-F16.gguf",
        "ctx_size": 131072,
        "kv_cache": "f16",
        "kv_cache_profiles": {
            "f16": {"label": "16-bit - more precise, context 128k",
                    "label_cs": "16 bit - přesnější, kontext 128k",
                    "ctx_size": 131072, "min_vram_gb": 30},
            "q8_0": {"label": "8-bit - larger context 256k",
                     "label_cs": "8 bit - větší kontext 256k",
                     "ctx_size": 262144, "min_vram_gb": 30},
            "q8_0_compact": {"cache_type": "q8_0",
                             "label": "8-bit - compact for 24 GB, context 96k",
                             "label_cs": "8 bit - kompaktní pro 24 GB, kontext 96k",
                             "ctx_size": 98304, "min_vram_gb": 23},
            "f16_compact": {"cache_type": "f16",
                            "label": "16-bit - compact for 24 GB, context 64k",
                            "label_cs": "16 bit - kompaktní pro 24 GB, kontext 64k",
                            "ctx_size": 65536, "min_vram_gb": 24},
        },
        "server_args": ["-fa", "on"],
    },
    "q3": {
        "alias": "Qwen3.8-27B IQ3_S (12.0 GB, borderline quality - for 16 GB GPUs)",
        "status_label": "Qwen 3.8 27B · IQ3_S",
        "repo": "unsloth/Qwen3.8-27B-GGUF",
        "file": "Qwen3.8-27B-UD-IQ3_S.gguf",
        "mmproj": "mmproj-F16.gguf",
        "ctx_size": 49152,
        "kv_cache": "q8_0",
        "kv_cache_profiles": {
            "q8_0": {"label": "8-bit - context 48k (16 GB borderline)",
                     "label_cs": "8 bit - kontext 48k (16 GB hraniční)",
                     "ctx_size": 49152, "min_vram_gb": 15},
            "q8_0_32k": {"cache_type": "q8_0",
                         "label": "8-bit - context 32k (16 GB safe)",
                         "label_cs": "8 bit - kontext 32k (16 GB bezpečné)",
                         "ctx_size": 32768, "min_vram_gb": 14},
            "q8_0_128k": {"cache_type": "q8_0",
                          "label": "8-bit - context 128k (24 GB)",
                          "label_cs": "8 bit - kontext 128k (24 GB)",
                          "ctx_size": 131072, "min_vram_gb": 20},
            "f16_96k": {"cache_type": "f16",
                        "label": "16-bit - context 96k (24 GB, max precision)",
                        "label_cs": "16 bit - kontext 96k (24 GB, max přesnost)",
                        "ctx_size": 98304, "min_vram_gb": 23},
            "q8_0_256k": {"cache_type": "q8_0",
                          "label": "8-bit - context 256k (32 GB)",
                          "label_cs": "8 bit - kontext 256k (32 GB)",
                          "ctx_size": 262144, "min_vram_gb": 26},
            "f16_192k": {"cache_type": "f16",
                         "label": "16-bit - context 192k (32 GB, borderline)",
                         "label_cs": "16 bit - kontext 192k (32 GB, hraniční)",
                         "ctx_size": 196608, "min_vram_gb": 31},
        },
        "server_args": ["-fa", "on"],
    },
    "q5": {
        "alias": "Qwen3.8-27B Q5_K_M (19.8 GB, high quality)",
        "status_label": "Qwen 3.8 27B · Q5",
        "repo": "unsloth/Qwen3.8-27B-GGUF",
        "file": "Qwen3.8-27B-UD-Q5_K_M.gguf",
        "mmproj": "mmproj-F16.gguf",
        "ctx_size": 98304,
        "kv_cache": "q8_0",
        "kv_cache_profiles": {
            "f16": {"label": "16-bit - more precise, context 96k",
                    "label_cs": "16 bit - přesnější, kontext 96k",
                    "ctx_size": 98304, "min_vram_gb": 24},
            "q8_0": {"label": "8-bit - larger context 192k",
                     "label_cs": "8 bit - větší kontext 192k",
                     "ctx_size": 196608, "min_vram_gb": 30},
        },
        "server_args": ["-fa", "on"],
    },
    "ornith_q5": {
        "alias": "Ornith 1.5 35B-A3B Abliterated Q5 (23.0 GB, reasoning, context 128k)",
        "status_label": "Ornith 1.5 35B-A3B · Abliterated Q5",
        "family": "ornith",
        "repo": "alztrk/Ornith-1.5-35B-A3B-Abliterated-GGUF",
        "file": "Ornith-1.5-35B-Abliterated-Dynamic-Q5_K_M.gguf",
        "mmproj_repo": "ornith-ai/Ornith-1.5-35B-A3B-GGUF",
        "mmproj": "mmproj-Ornith-1.5-35B-BF16.gguf",
        "ctx_size": 131072,
        "kv_cache": "q8_0",
        "kv_cache_profiles": {
            "q8_0": {"label": "8-bit - solid, context 128k",
                     "label_cs": "8 bit - pevné, kontext 128k",
                     "ctx_size": 131072, "min_vram_gb": 30},
        },
        "server_args": ["-fa", "on"],
        "sampling": {
            "thinking": {
                "temperature": 0.6,
                "top_p": 0.95,
                "top_k": 20,
                "presence_penalty": 0.0,
            },
            "non_thinking": {
                "temperature": 0.7,
                "top_p": 0.80,
                "top_k": 20,
                "presence_penalty": 1.5,
            },
        },
        "supports_reasoning_effort": False,
    },
    "nemotron_q4": {
            "alias": "Nemotron 3.5 Lightning 30B-A3B Q4_K_XL (25.5 GB, hybrid MoE, ~210 tok/s)",
            "status_label": "Nemotron 3.5 Lightning · Q4_XL",
            "family": "nemotron",
            "repo": "unsloth/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-GGUF",
            "file": "NVIDIA-Nemotron-3.5-Lightning-30B-A3B-UD-Q4_K_XL.gguf",
            "ctx_size": 524288,
            "kv_cache": "q8_0_512k",
            "kv_cache_profiles": {
                    "q8_0_128k": {
                            "cache_type": "q8_0",
                            "label": "8-bit - context 128k",
                            "label_cs": "8 bit - kontext 128k",
                            "ctx_size": 131072,
                            "min_vram_gb": 27
                    },
                    "q8_0_256k": {
                            "cache_type": "q8_0",
                            "label": "8-bit - context 256k",
                            "label_cs": "8 bit - kontext 256k",
                            "ctx_size": 262144,
                            "min_vram_gb": 28
                    },
                    "q8_0_512k": {
                            "cache_type": "q8_0",
                            "label": "8-bit - context 512k",
                            "label_cs": "8 bit - kontext 512k",
                            "ctx_size": 524288,
                            "min_vram_gb": 29.5
                    },
                    "q8_0_1m": {
                            "cache_type": "q8_0",
                            "label": "8-bit - context 1M (borderline)",
                            "label_cs": "8 bit - kontext 1M (hraniční)",
                            "ctx_size": 1048576,
                            "min_vram_gb": 31.5
                    },
                    "q8_0_256k_spill": {
                            "cache_type": "q8_0",
                            "label": "8-bit - context 256k, MoE overflow to RAM (24 GB)",
                            "label_cs": "8 bit - kontext 256k, MoE přeteče do RAM (24 GB)",
                            "ctx_size": 262144,
                            "min_vram_gb": 24,
                            "server_args": [
                                    "--n-cpu-moe",
                                    "14"
                            ]
                    },
                    "q8_0_512k_spill": {
                            "cache_type": "q8_0",
                            "label": "8-bit - context 512k, MoE overflow to RAM (24 GB)",
                            "label_cs": "8 bit - kontext 512k, MoE přeteče do RAM (24 GB)",
                            "ctx_size": 524288,
                            "min_vram_gb": 24,
                            "server_args": [
                                    "--n-cpu-moe",
                                    "18"
                            ]
                    }
            },
            "server_args": [
                    "-fa",
                    "on"
            ],
            "supports_reasoning_effort": False,
            "sampling": {
                    "thinking": {
                            "temperature": 0.6,
                            "top_p": 0.95,
                            "min_p": 0.01
                    },
                    "non_thinking": {
                            "temperature": 0.2
                    }
            }
    },
    "nemotron_q5": {
            "alias": "Nemotron 3.5 Lightning 30B-A3B Q5_K_XL (30.4 GB, hybrid MoE, top quality)",
            "status_label": "Nemotron 3.5 Lightning · Q5_KXL",
            "family": "nemotron",
            "repo": "unsloth/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-GGUF",
            "file": "NVIDIA-Nemotron-3.5-Lightning-30B-A3B-UD-Q5_K_XL.gguf",
            "ctx_size": 131072,
            "kv_cache": "q8_0_128k",
            "kv_cache_profiles": {
                    "q8_0_128k": {
                            "cache_type": "q8_0",
                            "label": "8-bit - context 128k (full GPU, borderline)",
                            "label_cs": "8 bit - kontext 128k (plně GPU, hraniční)",
                            "ctx_size": 131072,
                            "min_vram_gb": 31.5
                    },
                    "q8_0_256k": {
                            "cache_type": "q8_0",
                            "label": "8-bit - context 256k (full GPU, borderline)",
                            "label_cs": "8 bit - kontext 256k (plně GPU, hraniční)",
                            "ctx_size": 262144,
                            "min_vram_gb": 31.5
                    },
                    "q8_0_256k_spill": {
                            "cache_type": "q8_0",
                            "label": "8-bit - context 256k, MoE overflow to RAM (more headroom)",
                            "label_cs": "8 bit - kontext 256k, MoE přeteče do RAM (větší rezerva)",
                            "ctx_size": 262144,
                            "min_vram_gb": 29.5,
                            "server_args": [
                                    "--n-cpu-moe",
                                    "8"
                            ]
                    },
                    "q8_0_512k_spill": {
                            "cache_type": "q8_0",
                            "label": "8-bit - context 512k, MoE overflow to RAM",
                            "label_cs": "8 bit - kontext 512k, MoE přeteče do RAM",
                            "ctx_size": 524288,
                            "min_vram_gb": 26,
                            "server_args": [
                                    "--n-cpu-moe",
                                    "16"
                            ]
                    }
            },
            "server_args": [
                    "-fa",
                    "on"
            ],
            "supports_reasoning_effort": False,
            "sampling": {
                    "thinking": {
                            "temperature": 0.6,
                            "top_p": 0.95,
                            "min_p": 0.01
                    },
                    "non_thinking": {
                            "temperature": 0.2
                    }
            }
    },
}

DEFAULTS: dict[str, Any] = {
    "server": {
        "host": "127.0.0.1",
        "port": 8080,
        "n_gpu_layers": 999,
        "extra_args": [],
    },
    # Built-ins live in code too: an installer update preserves config.yaml, but
    # still needs to introduce newly supported models.
    "models": BUILTIN_MODELS,
    "default_model": "q5",
    "sampling": {
        "thinking": {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "presence_penalty": 0.0},
        "non_thinking": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 1.5},
    },
    "thinking": True,
    "reasoning_effort": "xhigh",   # xhigh | medium | low (Qwen reasoning effort)
    "agent": {
        "mode": "agent",
        "autonomy": "supervised",
        "max_steps": 0,
        "semi_max_steps": 0,
        "shell_timeout": 60,
        "workspace": None,
    },
    "computer": {
        "screenshot_max_edge": 1920,
        "screenshot_grayscale": False,
        "failsafe": True,
        "pause_between_actions": 0.15,
    },
    "memory": {
        "directory": "memory",
        "global_filename": "GLOBAL.md",
        "modes_directory": "modes",
        "development_filename": "MEMORY.md",
        "project_filename": "QWEN_MEMORY.md",
    },
    "web": {"host": "127.0.0.1", "port": 7860},
    "hardware": {"vram_gb": "auto"},
    "skills": {
        "directory": "skills",
        "user_directory": "user-skills",
        "project_directory": ".qwen-skills",
    },
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


def _migrate_builtin_models(user: dict[str, Any]) -> None:
    """Convert the short-lived fixed-Q8 presets to selectable KV profiles."""
    q8_args = ["--cache-type-k", "q8_0", "--cache-type-v", "q8_0", "-fa", "on"]
    q8_ctx = {"q4": 262144, "q5": 196608}
    f16_ctx = {"q4": 131072, "q5": 98304}
    models = user.get("models")
    if not isinstance(models, dict):
        return
    for key, large_ctx in q8_ctx.items():
        model = models.get(key)
        if (isinstance(model, dict) and model.get("ctx_size") == large_ctx
                and model.get("server_args") == q8_args):
            model["ctx_size"] = f16_ctx[key]
            model.pop("server_args", None)


# staré české labely z configů před verzí 1.3.0 (angličtina se stala základem)
_LEGACY_KV_LABELS = {
    "16 bit - přesnější, kontext 128k": "16-bit - more precise, context 128k",
    "8 bit - větší kontext 256k": "8-bit - larger context 256k",
    "16 bit - přesnější, kontext 96k": "16-bit - more precise, context 96k",
    "8 bit - větší kontext 192k": "8-bit - larger context 192k",
    "8 bit - pevné, kontext 128k": "8-bit - solid, context 128k",
}


def _migrate_kv_labels(user: dict[str, Any]) -> None:
    """Přelož legacy české KV labely na anglické `label` + českou `label_cs`.

    Instalátor při upgrade zachová starý config.yaml (onlyifdoesntexist) -
    bez téhle migrace by anglické UI zobrazovalo české volby KV cache.
    """
    models = user.get("models")
    if not isinstance(models, dict):
        return
    for model in models.values():
        if not isinstance(model, dict):
            continue
        profiles = model.get("kv_cache_profiles")
        if not isinstance(profiles, dict):
            continue
        for profile in profiles.values():
            if not isinstance(profile, dict):
                continue
            label = profile.get("label")
            if isinstance(label, str) and label in _LEGACY_KV_LABELS:
                profile["label_cs"] = label
                profile["label"] = _LEGACY_KV_LABELS[label]


def _remove_legacy_agent_limits(user: dict[str, Any]) -> None:
    agent = user.get("agent")
    if isinstance(agent, dict):
        agent["max_steps"] = 0
        agent["semi_max_steps"] = 0


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

    def mmproj_file(self, key: str | None = None) -> Path | None:
        """Cesta k vision projektoru; None pro text-only modely (bez mmproj v configu)."""
        mmproj = self.model(key).get("mmproj")
        if not mmproj:
            return None
        return self.path("paths.models_dir") / mmproj

    def mmproj_repo(self, key: str | None = None) -> str:
        model = self.model(key)
        return str(model.get("mmproj_repo") or model["repo"])

    def kv_cache_profiles(self, key: str | None = None) -> dict[str, dict[str, Any]]:
        return dict(self.model(key).get("kv_cache_profiles") or {})

    def kv_cache_mode(self, key: str | None = None) -> str:
        model = self.model(key)
        profiles = self.kv_cache_profiles(key)
        selected = str(model.get("kv_cache", "f16"))
        return selected if selected in profiles else next(iter(profiles), selected)

    def set_kv_cache_mode(self, key: str, mode: str) -> None:
        if mode not in self.kv_cache_profiles(key):
            raise ValueError(f"Model '{key}' nepodporuje KV cache '{mode}'")
        self.model(key)["kv_cache"] = mode

    def context_size(self, key: str | None = None) -> int:
        model = self.model(key)
        profile = self.kv_cache_profiles(key).get(self.kv_cache_mode(key), {})
        return int(profile.get("ctx_size", model.get("ctx_size", 32768)))

    def kv_cache_server_args(self, key: str | None = None) -> list[str]:
        mode = self.kv_cache_mode(key)
        # profil může mít vlastní cache_type (napr. kompaktni varianty q8_0_compact)
        cache_type = str(self.kv_cache_profiles(key).get(mode, {}).get("cache_type") or mode)
        return ["--cache-type-k", cache_type, "--cache-type-v", cache_type]

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
        sampling = self.data["sampling"][key]
        model_sampling = self.model().get("sampling", {}).get(key, {})
        return _deep_merge(sampling, model_sampling)

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
    _migrate_builtin_models(user)
    _migrate_kv_labels(user)
    _remove_legacy_agent_limits(user)
    return Config(_deep_merge(DEFAULTS, user))
