"""Stažení GGUF modelů (Qwen3.8-27B Q4_K_M + Q5_K_M + mmproj) z Hugging Face.

Použití:
    python scripts/download_models.py              # vše (default_model + mmproj)
    python scripts/download_models.py --model all  # Q4 i Q5 + mmproj
    python scripts/download_models.py --model q5   # jen Q5 + mmproj
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.config import load_config  # noqa: E402


def hf_download(repo: str, filename: str, models_dir: Path) -> Path:
    from huggingface_hub import hf_hub_download
    print(f"  → {repo}/{filename}")
    return Path(hf_hub_download(
        repo_id=repo,
        filename=filename,
        local_dir=str(models_dir),
    ))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", choices=["default", "all", "q4", "q5"], default="default")
    args = ap.parse_args()

    cfg = load_config()
    models_dir = cfg.path("paths.models_dir")
    models_dir.mkdir(parents=True, exist_ok=True)

    which: list[str]
    if args.model == "all":
        which = ["q4", "q5"]
    elif args.model in ("q4", "q5"):
        which = [args.model]
    else:
        which = [cfg.data["default_model"]]

    mmproj_done = False
    for key in which:
        m = cfg.data["models"][key]
        target = models_dir / m["file"]
        if target.exists() and target.stat().st_size > 1 << 30:
            print(f"[OK] {key}: {m['file']} už stažen")
        else:
            print(f"[STAHUJI] {key}: {m['alias']}")
            hf_download(m["repo"], m["file"], models_dir)
            print(f"[HOTOVO] {key}: {m['file']}")
        if not mmproj_done:
            mm = models_dir / m["mmproj"]
            if mm.exists() and mm.stat().st_size > 10 << 20:
                print(f"[OK] mmproj už stažen: {m['mmproj']}")
            else:
                print(f"[STAHUJI] mmproj: {m['mmproj']}")
                hf_download(m["repo"], m["mmproj"], models_dir)
                print(f"[HOTOVO] mmproj: {m['mmproj']}")
            mmproj_done = True

    print("\nVše připraveno v:", models_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
