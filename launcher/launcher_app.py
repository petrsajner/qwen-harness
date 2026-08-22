r"""Qwen3.8-27B Harness — spustitelný launcher (kompilováno PyInstallerem na QwenHarness.exe).

Životní cyklus:
  START → preflight (venv/modely; chybí-li → nabídne instalaci v konzoli)
        → start llama-server (pokud neběží)
        → start Web UI (bez autoprotein prohlížeče) + nativní okno (WebView2)
  KONEC → zavření okna: stop Web UI + llama-server, VRAM uvolněna.

Build:  installer\build_exe.bat  →  dist\QwenHarness\QwenHarness.exe
Test:   QwenHarness.exe --smoke  (životní cyklus bez okna)
"""
from __future__ import annotations

import atexit
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def _app_root() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller exe
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT = _app_root()
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
VENV_PYW = ROOT / ".venv" / "Scripts" / "pythonw.exe"

# log pro windowed exe (bez konzole)
if sys.stdout is None or sys.stderr is None:
    _logdir = ROOT / "runtime"
    _logdir.mkdir(parents=True, exist_ok=True)
    _lf = open(_logdir / "launcher.log", "a", buffering=1, encoding="utf-8")
    _lf.write(f"\n===== LAUNCHER {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    sys.stdout = _lf
    sys.stderr = _lf


def _log(msg: str) -> None:
    print(f"[APP] {msg}")


def _alert(msg: str, question: bool = False) -> bool:
    """MessageBox; question=True vrací True při Ano."""
    try:
        import ctypes
        flags = 0x24 if question else 0x10  # YESNO+QUESTION | ICON_ERROR
        return ctypes.windll.user32.MessageBoxW(None, msg, "Qwen3.8-27B Harness", flags) == 6
    except Exception:
        return False


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _is_our_webui(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/config", timeout=2.0) as r:
            payload = json.load(r)
        return r.status == 200 and isinstance(payload.get("components"), list)
    except Exception:
        return False


def _port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _free_web_port(preferred: int) -> int:
    for port in range(preferred, preferred + 20):
        if not _port_busy(port):
            return port
    raise RuntimeError(f"Žádný volný Web UI port v rozsahu {preferred}-{preferred + 19}")


def _cfg_ports() -> tuple[int, int]:
    """Porty (server, web) z config.yaml - jednoduchý parse s fallbackem."""
    srv, web = 8080, 7860
    try:
        text = (ROOT / "config.yaml").read_text(encoding="utf-8")
        m = re.search(r"^server:.*?^\s+port:\s*(\d+)", text, re.S | re.M)
        if m:
            srv = int(m.group(1))
        m = re.search(r"^web:.*?^\s+port:\s*(\d+)", text, re.S | re.M)
        if m:
            web = int(m.group(1))
    except Exception:
        pass
    return srv, web


def _check_model_files() -> tuple[bool, str]:
    """Existují modely na SPRÁVNÉM místě (runtime/models v instalačním adresáři)?

    Vrací (ok, popis s přesnými cestami pro zobrazení uživateli).
    """
    models_dir = ROOT / "runtime" / "models"
    ggufs = list(models_dir.glob("*.gguf")) if models_dir.exists() else []
    has_mmproj = any("mmproj" in g.name.lower() for g in ggufs)
    has_model = any("mmproj" not in g.name.lower() and "mtp" not in g.name.lower() for g in ggufs)
    detail = f"hledám v: {models_dir}"
    return (has_model and has_mmproj), detail


def _port_pid(port: int) -> int | None:
    """PID procesu, který poslouchá na portu (netstat, bez psutil)."""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True,
                             text=True, creationflags=0x08000000).stdout
        for line in out.splitlines():
            if "LISTENING" in line and f"127.0.0.1:{port} " in line + " ":
                parts = line.split()
                if len(parts) >= 5:
                    return int(parts[-1])
    except Exception:
        pass
    return None


def _kill_tree(pid: int) -> None:
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, creationflags=0x08000000)  # bez konzole
    except Exception:
        pass


def _run_setup_console() -> bool:
    """Instalace prostředí+modelů ve viditelné konzoli (venv, llama.cpp, 37 GB modelů)."""
    bat = ROOT / "run_setup.bat"
    if not bat.exists():
        _alert(f"Chybí {bat.name} - spusť instalaci ručně podle README.")
        return False
    rc = subprocess.call(["cmd", "/c", str(bat)], cwd=str(ROOT))
    return rc == 0


def _focus_window() -> None:
    """Přines okno do popředí - robustně (Windows zakazují ukrást fokus,
    proto TOPMOST-toggle trik; 3 pokusy, WebView2 okno se inicializuje pomalu)."""
    import ctypes
    import time as _t
    _t.sleep(2.0)
    try:
        from ctypes import wintypes
        u = ctypes.windll.user32
        SWP_NOSIZE, SWP_NOMOVE = 0x0001, 0x0002
        HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
        for _attempt in range(3):
            found: list[int] = []

            @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            def cb(h, l):
                buf = ctypes.create_unicode_buffer(128)
                u.GetWindowTextW(h, buf, 128)
                if "Qwen3.8-27B Harness" in buf.value:
                    found.append(h)
                return True

            u.EnumWindows(cb, 0)
            if found:
                for h in found:
                    u.ShowWindow(h, 9)  # SW_RESTORE
                    # TOPMOST → NOTOPMOST donutí okno vizuálně nahoru i bez focus práv
                    u.SetWindowPos(h, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
                    u.SetWindowPos(h, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
                    u.SetForegroundWindow(h)
                    u.BringWindowToTop(h)
                return
            _t.sleep(1.0)
    except Exception:
        pass


_splash_done: "threading.Event | None" = None


def _show_splash() -> None:
    """Malé okno 'startuji…' okamžitě po spuštění (zavře se s hlavním oknem).

    Tk splash = native, bez konzole; když tkinter chybí, tiše přeskoč.
    """
    global _splash_done
    try:
        import tkinter as tk

        def _run():
            global _splash_done
            try:
                root = tk.Tk()
                root.title("Qwen3.8-27B Harness")
                root.overrideredirect(True)
                root.attributes("-topmost", True)
                tk.Label(root, text="Qwen3.8-27B Harness\n\nstartuji …",
                         font=("Segoe UI", 13), padx=36, pady=22,
                         bg="#0b0e14", fg="#e8f0ff").pack()
                root.update_idletasks()
                w, h = 320, 120
                x = (root.winfo_screenwidth() - w) // 2
                y = (root.winfo_screenheight() - h) // 2
                root.geometry(f"{w}x{h}+{x}+{y}")
                while not _splash_done.is_set():
                    root.update()
                    time.sleep(0.05)
                root.destroy()
            except Exception:
                pass

        import threading
        _splash_done = threading.Event()
        threading.Thread(target=_run, daemon=True, name="splash").start()
    except Exception:
        pass


def _close_splash() -> None:
    if _splash_done is not None:
        _splash_done.set()


def _write_loading_page(web_port: int):
    """Interní 'načítám' stránka - okno se otevře okamžitě; stránka sama
    přeskočí na UI ve chvíli, kdy webapp naslouchá (fetch probe)."""
    import pathlib
    d = ROOT / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    p = d / ".loading.html"
    p.write_text(f"""<!doctype html><html><head><meta charset="utf-8">
<title>Qwen3.8-27B Harness</title>
<style>
body{{background:#0b0e14;color:#e6edf3;font-family:'Segoe UI',system-ui,sans-serif;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0;
flex-direction:column;gap:20px}}
.r{{width:56px;height:56px;border:4px solid rgba(45,212,191,.18);
border-top-color:#2dd4bf;border-radius:50%;animation:s 1s linear infinite}}
@keyframes s{{to{{transform:rotate(360deg)}}}}
h1{{font-size:21px;font-weight:600;margin:0}} h1 b{{color:#2dd4bf}}
small{{color:#8b949e}}
</style></head><body>
<div class="r"></div>
<h1><b>Qwen</b>3.8-27B Harness</h1>
<small id="s">startuji rozhraní… (první spuštění chvíli trvá)</small>
<script>
const APP='http://127.0.0.1:{web_port}/';
const t0=Date.now();
(async function probe(){{
  try{{ await fetch(APP+'config',{{mode:'no-cors'}}); location.replace(APP); return; }}catch(e){{}}
  if(Date.now()-t0>20000)
    document.getElementById('s').textContent='stále startuje… (detaily: runtime/launcher.log)';
  setTimeout(probe,400);
}})();
</script></body></html>""", encoding="utf-8")
    return p


def main() -> int:
    smoke = "--smoke" in sys.argv
    srv_port, web_port = _cfg_ports()
    base_srv = f"http://127.0.0.1:{srv_port}"
    base_web = f"http://127.0.0.1:{web_port}"

    if not smoke:
        _show_splash()

    # ---- 1) preflight: venv + llama.cpp + modely (rychlé kontroly souborů) ----
    problems = []
    if not VENV_PY.exists():
        problems.append("Python prostředí (.venv)")
    if not (ROOT / "runtime" / "llama").exists() or not any(
            (ROOT / "runtime" / "llama").rglob("llama-server.exe")):
        problems.append("llama.cpp (runtime\\llama)")
    models_ok, models_detail = _check_model_files()
    if not models_ok:
        problems.append(f"modely Qwen3.8-27B ({models_detail})")
    if problems:
        _close_splash()
        _log("Chybí: " + "; ".join(problems))
        if _alert("Aplikace ještě není dokončená - chybí:\n\n  • " +
                  "\n  • ".join(problems) +
                  "\n\nSpustit instalaci nyní? (stáhne ~37 GB na správné místo)",
                  question=True):
            if not _run_setup_console():
                return 1
            models_ok, _ = _check_model_files()
            if not VENV_PY.exists() or not models_ok:
                _alert("Instalace se nepodařila - zkus znovu nebo spusť "
                       "'Instalace prostředí a modelů' ze Start Menu.")
                return 1
        else:
            return 1

    # ---- 2) Web UI NEJDŘÍV (model se nahodí na pozadí přes autostart) ---------
    # UI-first: okno se otevře hned, status ukazuje ⏳ načítám model → 🟢
    webapp_proc = None
    webui_running = _is_our_webui(base_web)
    if not webui_running:
        web_port = _free_web_port(web_port)
        base_web = f"http://127.0.0.1:{web_port}"
    env = {**os.environ, "QWEN_NO_BROWSER": "1", "QWEN_AUTOSTART_SERVER": "1",
           "QWEN_WEB_PORT": str(web_port)}
    if not webui_running:
        _log("Startuji Web UI (model se nahodí na pozadí) ...")
        webapp_proc = subprocess.Popen(
            [str(VENV_PYW), "webapp.py"], cwd=str(ROOT), env=env,
            creationflags=0x08000000)
        if not smoke:
            # okno otevřeme HNED s loading stránkou (sama přeskočí na UI,
            # až bude server ready) - uživatel nekouká 10 s na splash
            loading = _write_loading_page(web_port)
            url = loading.as_uri()
            _log(f"Okno otevřeno hned (loading) → {base_web}")
        else:
            for _ in range(120):
                if _http_ok(f"{base_web}/config"):
                    break
                time.sleep(0.5)
            url = base_web
    else:
        url = base_web
    if not (webapp_proc is not None and not smoke):
        _log(f"Web UI připraveno: {url}")

    # ---- 3) cleanup (zavření okna = stop všeho + uvolnění VRAM) ---------------
    cleaned = {"done": False}

    def cleanup() -> None:
        if cleaned["done"]:
            return
        cleaned["done"] = True
        _log("Ukončuji: Web UI ...")
        if webapp_proc is not None:
            _kill_tree(webapp_proc.pid)
        _log("Zastavuji llama-server (uvolňuji VRAM) ...")
        subprocess.call([str(VENV_PY), "scripts/server.py", "stop"],
                        cwd=str(ROOT), creationflags=0x08000000)
        _log("Hotovo - VRAM uvolněna.")

    atexit.register(cleanup)

    if smoke:
        # počkej na model (autostart na pozadí), pak cleanup - test celého cyklu
        _log("SMOKE: čekám na model (autostart) ...")
        for _ in range(90):
            if _http_ok(f"{base_srv}/health"):
                break
            time.sleep(1)
        ok = _http_ok(f"{base_srv}/health")
        _log(f"SMOKE: model {'BĚŽÍ' if ok else 'NEBĚŽÍ (timeout)'} - ukončuji.")
        cleanup()
        return 0 if ok else 1

    # ---- 4) nativní okno --------------------------------------------------------
    _close_splash()
    try:
        import webview
        webview.create_window("Qwen3.8-27B Harness", url,
                               width=1440, height=920, min_size=(960, 640),
                               background_color="#0b0e14")
        _log(f"Okno otevřeno: {url} (model se případně dolaďuje na pozadí)")
        webview.start(_focus_window)
    except ImportError:
        import webbrowser
        _log(f"pywebview chybí - otevírám prohlížeč: {url}")
        webbrowser.open(url)
        try:
            input("[APP] Enter = konec (server se zastaví)...\n")
        except EOFError:
            pass
    finally:
        cleanup()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        _alert("Aplikace selhala – detaily v runtime\\launcher.log")
        raise SystemExit(1)
