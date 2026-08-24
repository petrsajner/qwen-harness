"""Stažení nakonfigurovaných GGUF modelů a jejich vision projektorů.

Použití:
    python scripts/download_models.py                  # default_model + mmproj
    python scripts/download_models.py --model auto     # modely pro detekovanou GPU
    python scripts/download_models.py --model all      # všechny modely + projektory
    python scripts/download_models.py --models q4,q5   # konkrétní modely (install wizard)
    python scripts/download_models.py --model ornith_q5 # jen Ornith + projektor
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
    cfg = load_config()
    model_keys = list(cfg.data["models"])
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", choices=["default", "auto", "all", *model_keys],
                    default="default")
    ap.add_argument("--models", default=None,
                    help="comma-separated model keys (overrides --model; "
                         "used by the installer selection)")
    args = ap.parse_args()

    models_dir = cfg.path("paths.models_dir")
    models_dir.mkdir(parents=True, exist_ok=True)

    which: list[str]
    if args.models:
        selected = [k.strip() for k in args.models.split(",") if k.strip()]
        unknown = [k for k in selected if k not in model_keys]
        if unknown:
            print(f"[ERROR] Unknown model keys: {', '.join(unknown)} "
                  f"(available: {', '.join(model_keys)})")
            return 1
        which = selected
    elif args.model == "all":
        which = model_keys
    elif args.model == "auto":
        from harness.gpu import download_keys, effective_vram_gb
        vram = effective_vram_gb(cfg)
        which = download_keys(cfg, vram)
        if vram is None:
            print("[AUTO] GPU VRAM unknown - downloading all models")
        elif set(which) == set(model_keys):
            print(f"[AUTO] {vram} GB VRAM - all models fit, downloading all")
        else:
            skipped = [k for k in model_keys if k not in which]
            print(f"[AUTO] {vram} GB VRAM - downloading only: {', '.join(which)}")
            print(f"[AUTO] skipping (would not fit): {', '.join(skipped)}")
    elif args.model in model_keys:
        which = [args.model]
    else:
        which = [cfg.data["default_model"]]

    mmproj_done: set[tuple[str, str]] = set()
    for key in which:
        m = cfg.data["models"][key]
        target = models_dir / m["file"]
        if target.exists() and target.stat().st_size > 1 << 30:
            print(f"[OK] {key}: {m['file']} already downloaded")
        else:
            print(f"[DOWNLOAD] {key}: {m['alias']}")
            hf_download(m["repo"], m["file"], models_dir)
            print(f"[DONE] {key}: {m['file']}")
        mmproj_repo = cfg.mmproj_repo(key)
        mmproj_id = (mmproj_repo, m["mmproj"])
        if mmproj_id not in mmproj_done:
            mm = models_dir / m["mmproj"]
            if mm.exists() and mm.stat().st_size > 10 << 20:
                print(f"[OK] mmproj already downloaded: {m['mmproj']}")
            else:
                print(f"[DOWNLOAD] mmproj: {m['mmproj']}")
                hf_download(mmproj_repo, m["mmproj"], models_dir)
                print(f"[DONE] mmproj: {m['mmproj']}")
            mmproj_done.add(mmproj_id)

    print("\nEverything ready in:", models_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
