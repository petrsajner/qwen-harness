"""Správa llama-serveru (inference backend) - start/stop/switch/status.

Používá se z CLI (scripts/server.py), TUI i web UI.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import requests

from harness.config import Config

HEALTH_TIMEOUT = 900  # s - první načtení ~17-20GB modelu z disku chvíli trvá


def pid_file(cfg: Config) -> Path:
    return cfg.path("paths.runtime_dir") / "llama-server.pid"


def health(cfg: Config, timeout: float = 3.0) -> bool:
    try:
        r = requests.get(f"{cfg.base_url}/health", timeout=timeout)
        return r.status_code == 200
    except requests.RequestException:
        return False


def wait_health(cfg: Config, timeout: float = HEALTH_TIMEOUT) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if health(cfg):
            return True
        time.sleep(2)
    return False


def vram_str() -> str:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip().splitlines()[0]
        used, total = [x.strip() for x in out.split(",")]
        return f"GPU VRAM: {int(used) / 1024:.1f} / {int(total) / 1024:.1f} GB"
    except Exception:
        return "GPU VRAM: (nvidia-smi nedostupné)"


def running_model(cfg: Config) -> str | None:
    try:
        return pid_file(cfg).read_text(encoding="utf-8").strip().split(":")[0] or None
    except (OSError, ValueError, IndexError):
        return None


def stop(cfg: Config, quiet: bool = False) -> bool:
    import psutil
    pf = pid_file(cfg)
    pid = None
    try:
        pid = int(pf.read_text(encoding="utf-8").strip().split(":")[1])
    except (OSError, ValueError, IndexError):
        pass
    killed = False
    if pid:
        try:
            proc = psutil.Process(pid)
            children = proc.children(recursive=True)
            for c in children:
                c.kill()
            proc.kill()
            psutil.wait_procs(children + [proc], timeout=5)
            killed = True
        except psutil.NoSuchProcess:
            pass
        pf.unlink(missing_ok=True)
        for _ in range(15):
            if not health(cfg, timeout=1.0):
                break
            time.sleep(1)
    if not killed:
        # fallback: najdi proces podle jména
        for p in psutil.process_iter(["name"]):
            if p.info["name"] and p.info["name"].lower() == "llama-server.exe":
                try:
                    p.kill()
                    killed = True
                except psutil.NoSuchProcess:
                    pass
        pf.unlink(missing_ok=True)
    if not quiet:
        print("[OK] llama-server zastaven." if killed else "[INFO] llama-server neběžel.")
    return True


def start(cfg: Config, model_key: str | None = None, ctx_size: int | None = None) -> int:
    model_key = model_key or cfg.model_key()
    if model_key not in cfg.data["models"]:
        print(f"[CHYBA] Neznámý model '{model_key}'. Dostupné: {', '.join(cfg.data['models'])}")
        return 1

    if health(cfg):
        current = running_model(cfg)
        if current == model_key:
            print(f"[OK] llama-server už běží s modelem '{model_key}' ({cfg.base_url})")
            print("   ", vram_str())
            return 0
        print(f"[INFO] Běží model '{current}', přepínám na '{model_key}' ...")
        stop(cfg, quiet=True)

    exe = cfg.llama_server_exe()
    if exe is None:
        print("[CHYBA] llama-server.exe nenalezen. Spusť nejdřív: python scripts/download_llama.py")
        return 1
    mfile = cfg.model_file(model_key)
    mmproj = cfg.mmproj_file(model_key)
    if not mfile.exists():
        print(f"[CHYBA] Model nenalezen: {mfile}")
        print("        Stáhni ho: python scripts/download_models.py --model", model_key)
        return 1

    srv = cfg.data["server"]
    ctx = ctx_size or int(cfg.model(model_key).get("ctx_size", 32768))
    argv = [
        str(exe),
        "-m", str(mfile),
        "-ngl", str(srv.get("n_gpu_layers", 999)),
        "-c", str(ctx),
        "--host", srv["host"],
        "--port", str(srv["port"]),
        "--jinja",               # plná chat template + tool calling
        "--alias", model_key,
    ]
    if mmproj.exists():
        argv += ["--mmproj", str(mmproj)]
    else:
        print(f"[VAROVÁNÍ] mmproj nenalezen ({mmproj}) - vision (obrázky) nebude fungovat!")
    argv += [str(x) for x in srv.get("extra_args", [])]

    log_path = cfg.path("paths.runtime_dir") / "llama-server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logf = open(log_path, "ab", buffering=0)
    logf.write(f"\n===== START {model_key} {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n".encode())
    proc = subprocess.Popen(
        argv, stdout=logf, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
        cwd=str(exe.parent),
    )
    pid_file(cfg).write_text(f"{model_key}:{proc.pid}", encoding="utf-8")
    print(f"[START] model={model_key}  ctx={ctx}  pid={proc.pid}  → {cfg.base_url}")
    print(f"        log: {log_path}")
    print("[ČEKÁM] načítám model do VRAM ...", end="", flush=True)
    t0 = time.time()
    if not wait_health(cfg):
        print(f"\n[CHYBA] Server se nedostavil do {HEALTH_TIMEOUT}s. Poslední řádky logu:")
        print(log_path.read_bytes()[-2000:].decode(errors="replace"))
        return 1
    print(f" OK ({time.time() - t0:.0f}s)")
    print("   ", vram_str())
    return 0


def status(cfg: Config) -> int:
    if health(cfg):
        info = ""
        try:
            r = requests.get(f"{cfg.base_url}/props", timeout=3).json()
            info = str(r.get("model_path", ""))
        except Exception:
            pass
        print(f"[BĚŽÍ] {cfg.base_url}  (model z pidfile: {running_model(cfg)})")
        if info:
            print(f"        {info}")
        print("   ", vram_str())
        return 0
    print(f"[STOJÍ] {cfg.base_url} nereaguje. Start: python scripts/server.py start")
    return 1


def ensure(cfg: Config, model_key: str | None = None) -> bool:
    """Zajišť běžící server se zadaným modelem (případně start/switch)."""
    if health(cfg) and (model_key is None or running_model(cfg) == model_key):
        return True
    return start(cfg, model_key) == 0
