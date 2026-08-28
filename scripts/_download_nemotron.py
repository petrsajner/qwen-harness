"""Dočasné stažení Nemotron GGUF pro pred-implementacni testy (ne do configu)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from huggingface_hub import hf_hub_download

REPO = "unsloth/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-GGUF"
MODELS_DIR = ROOT / "runtime" / "models"

which = sys.argv[1] if len(sys.argv) > 1 else "q4"
FILES = {
    "q4": "NVIDIA-Nemotron-3.5-Lightning-30B-A3B-UD-Q4_K_XL.gguf",
    "q5": "NVIDIA-Nemotron-3.5-Lightning-30B-A3B-UD-Q5_K_XL.gguf",
}
path = Path(hf_hub_download(
    repo_id=REPO,
    filename=FILES[which],
    local_dir=str(MODELS_DIR),
))
print(f"[DONE] {path} ({path.stat().st_size / 1e9:.1f} GB)")
