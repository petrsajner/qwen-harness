"""Stažení oficiálních llama.cpp CUDA binárek pro Windows (Blackwell-ready).

Stahuje nejnovější release z github.com/ggml-org/llama.cpp a rozbalí ho do
runtime/llama/. Pro RTX 5090 (sm_120) je vyžadován CUDA 12.8+ build -
přednostně bereme cuda-13.x, fallback cuda-12.4 (ty mohou pro Blackwell
spoléhat na PTX JIT). Stahuje se i matching cudart balík (runtime DLL).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

GITHUB_RELEASES = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=30"
ASSET_RE = re.compile(r"^llama-b?\d+-bin-win-cuda(?:-[\d.]+)?-x64\.zip$")
CUDART_RE = re.compile(r"^cudart-llama-bin-win-cuda-[\d.]+-x64\.zip$")
# Preference CUDA verze pro RTX 5090 (Blackwell sm_120 vyžaduje 12.8+)
CUDA_PREFERENCE = ["13.3", "13.0", "12.8", "12.4"]


def _fetch_json(url: str) -> list:
    req = urllib.request.Request(url, headers={"User-Agent": "qwen-harness-setup"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def _cuda_version(name: str) -> str | None:
    m = re.search(r"cuda-([\d.]+)-", name)
    return m.group(1) if m else None


def pick_assets(assets: list[dict]) -> tuple[str, dict, dict | None] | None:
    """Vyber (tag, llama_asset, cudart_asset) podle preferencí."""
    llama_assets = [a for a in assets if ASSET_RE.match(a["name"])]
    cudart_assets = {a["name"]: a for a in assets if CUDART_RE.match(a["name"])}
    if not llama_assets:
        return None
    for pref in CUDA_PREFERENCE:
        for a in llama_assets:
            if _cuda_version(a["name"]) == pref:
                cudart = cudart_assets.get(f"cudart-llama-bin-win-cuda-{pref}-x64.zip")
                return a["name"], a, cudart
    # fallback: první CUDA build
    a = llama_assets[0]
    return a["name"], a, None


def latest_release_with_cuda() -> tuple[str, dict, dict | None] | None:
    for rel in _fetch_json(GITHUB_RELEASES):
        picked = pick_assets(rel.get("assets", []))
        if picked:
            return rel["tag_name"], picked[1], picked[2]
    return None


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as r, open(dest, "wb") as f:
        total_hdr = r.headers.get("content-length")
        total = int(total_hdr) if total_hdr else 0
        done = 0
        while True:
            chunk = r.read(1 << 20)  # 1 MiB
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                pct = done * 100 // total
                sys.stdout.write(f"\r  {done / 1e6:8.1f} / {total / 1e6:.1f} MB  ({pct:3d} %)")
                sys.stdout.flush()
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="stáhnout znovu i když binárky existují")
    args = ap.parse_args()

    llama_dir = ROOT / "runtime" / "llama"
    if not args.force and any(llama_dir.rglob("llama-server.exe")):
        print(f"[OK] llama.cpp už je přítomen v {llama_dir} (llama-server.exe nalezen)")
        return 0

    print("[1/3] Zjišťuji nejnovější release llama.cpp ...")
    found = latest_release_with_cuda()
    if found is None:
        print("[CHYBA] V posledních releases nebyl nalezen Windows CUDA build.")
        print("        Zkontroluj https://github.com/ggml-org/llama.cpp/releases")
        return 1
    tag, asset, cudart = found
    print(f"      Release: {tag}  →  {asset['name']} ({asset['size'] / 1e6:.0f} MB)"
          + (f" + cudart ({cudart['size'] / 1e6:.0f} MB)" if cudart else ""))

    runtime = ROOT / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    print("[2/3] Stahuji ...")
    zip_path = runtime / "llama.zip"
    download(asset["browser_download_url"], zip_path)
    cudart_path = None
    if cudart:
        cudart_path = runtime / "cudart.zip"
        download(cudart["browser_download_url"], cudart_path)

    print("[3/3] Rozbaluji ...")
    if llama_dir.exists():
        shutil.rmtree(llama_dir)
    llama_dir.mkdir(parents=True)
    for zp in (zip_path, cudart_path):
        if zp is None:
            continue
        with zipfile.ZipFile(zp) as zf:
            zf.extractall(llama_dir)
        zp.unlink()

    server = next(iter(llama_dir.rglob("llama-server.exe")), None)
    if server is None:
        print("[CHYBA] Po rozbalení chybí llama-server.exe!")
        return 1
    print(f"[HOTOVO] llama-server: {server}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
