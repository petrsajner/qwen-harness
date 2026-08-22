"""One-click instalace prostředí: pip deps + llama.cpp binárky + GGUF modely.

Spouštět VENV pythonem:
    .venv/Scripts/python scripts/setup_env.py
Volby:
    --skip-models   přeskočit download modelů (už jsou stažené)
    --model all     stáhnout Q4 i Q5 (default: jen default_model z configu)
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def pip_install() -> int:
    print("=" * 60)
    print("[1/3] Instaluji Python závislosti (requirements.txt) ...")
    print("=" * 60)
    return subprocess.call([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-models", action="store_true")
    ap.add_argument("--model", choices=["default", "all", "q4", "q5"], default="default")
    args = ap.parse_args()

    rc = pip_install()
    if rc:
        return rc

    # Importy až po pip install (potřebují yaml, huggingface_hub apod.)
    import download_llama
    import download_models

    print()
    print("=" * 60)
    print("[2/3] llama.cpp CUDA binárky ...")
    print("=" * 60)
    sys.argv = [sys.argv[0]]  # izolace argv pro argparse v podskriptech
    rc = download_llama.main()
    if rc:
        return rc

    if args.skip_models:
        print("\n[3/3] Modely přeskočeny (--skip-models).")
        return 0

    print()
    print("=" * 60)
    print("[3/3] GGUF modely z Hugging Face (unsloth/Qwen3.8-27B-GGUF) ...")
    print("=" * 60)
    sys.argv = [sys.argv[0], "--model", args.model]
    rc = download_models.main()
    if rc:
        return rc

    print()
    print("=" * 60)
    print("HOTOVO! Další kroky:")
    print("  1) spust server:  .venv/Scripts/python scripts/server.py start")
    print("  2) TUI:           .venv/Scripts/python tui.py")
    print("     Web UI:        .venv/Scripts/python webapp.py  → http://127.0.0.1:7860")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
