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
import os
import re
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
                       capture_output=True, creationflags=0x08000000)
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


def main() -> int:
    smoke = "--smoke" in sys.argv
    srv_port, web_port = _cfg_ports()
    base_srv = f"http://127.0.0.1:{srv_port}"
    base_web = f"http://127.0.0.1:{web_port}"

    # ---- 1) preflight: venv + llama.cpp + modely (na správném místě!) ---------
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
        _log("Chybí: " + "; ".join(problems))
        if _alert("Aplikace ještě není dokončená - chybí:\n\n  • " +
                  "\n  • ".join(problems) +
                  "\n\nSpustit instalaci nyní? (stáhne ~37 GB na správné místo)",
                  question=True):
            if not _run_setup_console():
                return 1
            # znovu zkontroluj
            models_ok, _ = _check_model_files()
            if not VENV_PY.exists() or not models_ok:
                _alert("Instalace se nepodařila - zkus znovu nebo spusť "
                       "'Instalace prostředí a modelů' ze Start Menu.")
                return 1
        else:
            return 1

    # ---- 2) llama-server ------------------------------------------------------
    attach_server = False
    if _http_ok(f"{base_srv}/health"):
        # na portu už něco běží - zeptat se, čí je (klidně vývojová instance)
        if smoke:
            attach_server = True
        else:
            attach_server = _alert(
                "llama-server už na tomto počítači běží (možná jiná instance aplikace).\n\n"
                "[Ano] = použít běžící server\n"
                "[Ne] = zastavit ho a spustit VLASTNÍ (modely z této instalace)",
                question=True)
        if not attach_server:
            subprocess.call([str(VENV_PY), "scripts/server.py", "stop"],
                            cwd=str(ROOT), creationflags=0x08000000)
            time.sleep(2)
    if not attach_server:
        _log(f"Startuji vlastní llama-server (modely: {ROOT / 'runtime' / 'models'}) ...")
        rc = subprocess.call(
            [str(VENV_PY), "scripts/server.py", "start"],
            cwd=str(ROOT), creationflags=0x08000000)  # CREATE_NO_WINDOW
        if rc != 0 or not _http_ok(f"{base_srv}/health", timeout=5):
            _alert("llama-server se nepodařilo spustit (viz runtime\\llama-server.log).")
            return 1
    else:
        _log("llama-server už běží - připojuji se.")

    # ---- 3) Web UI (subprocess, bez prohlížeče) -------------------------------
    webapp_proc = None
    env = {**os.environ, "QWEN_NO_BROWSER": "1"}
    if _http_ok(f"{base_web}/config"):
        if not attach_server:
            # vlastní server => cizí Web UI ukonči (viselo by na starém)
            pid = _port_pid(web_port)
            if pid:
                _log(f"Ukončuji cizí Web UI (pid {pid}).")
                _kill_tree(pid)
                time.sleep(2)
    if not _http_ok(f"{base_web}/config"):
        _log("Startuji Web UI ...")
        webapp_proc = subprocess.Popen(
            [str(VENV_PYW), "webapp.py"], cwd=str(ROOT), env=env,
            creationflags=0x08000000)
        for _ in range(120):
            if _http_ok(f"{base_web}/config"):
                break
            time.sleep(0.5)
    url = base_web

    # ---- 4) cleanup -----------------------------------------------------------
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
        _log(f"SMOKE: vše běží na {url} - za 3 s ukončím (test cleanupu).")
        time.sleep(3)
        cleanup()
        return 0

    # ---- 5) nativní okno --------------------------------------------------------
    try:
        import webview
        webview.create_window("Qwen3.8-27B Harness", url,
                               width=1440, height=920, min_size=(960, 640),
                               background_color="#0b0e14")
        _log(f"Okno otevřeno: {url}")
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
