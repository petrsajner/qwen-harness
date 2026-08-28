"""Asynchronní orchestrace startu a přepínání lokálního modelu.

Principy (UI nikdy nezamykáme):
- request() VŽDY uspěje - pouze přepíše požadovaný cíl; starší rozpracovaný
  cíl se zahodí. Přepínač modelu i KV je trvale interaktivní.
- Přepnutí uprostřed nahrávání okamžitě zabije loading (stop serveru,
  VRAM se uvolní) a worker nasadí nový cíl.
- Volba KV se aplikuje při startu serveru (kv_profile v requestu);
  žádné "KV nejde přepnout" hlášky neexistují.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from harness import servermgmt
from harness.config import Config
from harness.i18n import t


@dataclass(frozen=True)
class ModelSwitchSnapshot:
    status: str = "idle"  # idle | starting | ready | failed
    target: str | None = None
    error: str = ""

    @property
    def busy(self) -> bool:
        return self.status == "starting"


def _running_model_ok(cfg: Config, model_key: str) -> bool:
    return servermgmt.health(cfg) and servermgmt.running_model(cfg) == model_key


class ModelSwitchController:
    """Background smyčka pro start/přepnutí modelu; nejvýše jeden worker.

    Worker v každém kole: vezme nejnovější přání → (potřebu-li) zastaví
    server → nastartuje cílový model (včetně čekající volby KV) → publikuje
    stav. Přání, které přijde během práce, server zabije (ensure díky mrtvému
    procesu rychle skončí) a smyčka pokračuje novým cílem.
    """

    def __init__(self, cfg: Config, *,
                 ensure_fn: Callable[[Config, str], bool] = servermgmt.ensure,
                 stop_fn: Callable[..., bool] = servermgmt.stop,
                 running_fn: Callable[[Config, str], bool] = _running_model_ok):
        self.cfg = cfg
        self._ensure = ensure_fn
        self._stop = stop_fn
        self._running = running_fn
        self._lock = threading.Lock()
        self._state = ModelSwitchSnapshot()
        self._thread: threading.Thread | None = None
        # (model_key, kv_profile|None, restart, on_success) | None
        self._desired: tuple | None = None
        self._gen = 0  # zvýší se při každém requestu/cancelu (zastarávání publikací)

    def snapshot(self) -> ModelSwitchSnapshot:
        with self._lock:
            return self._state

    def request(self, model_key: str, *, restart: bool = False,
                kv_profile: str | None = None,
                on_success: Callable[[str], None] | None = None) -> bool:
        """Přijme vždy; právě probíhající loading přeruší (uvolní VRAM)."""
        with self._lock:
            interrupted = self._state.busy
            self._desired = (model_key, kv_profile, restart, on_success)
            self._gen += 1
            self._state = ModelSwitchSnapshot("starting", model_key)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, daemon=True, name="model-switch")
                self._thread.start()
        if interrupted or restart:
            # zabij bezici/loading server mimo lock - workeruv ensure rychle
            # skončí chybou a smyčka si vezme nový cíl
            try:
                self._stop(self.cfg, quiet=True)
            except Exception:
                pass
        return True

    def cancel(self) -> None:
        """Stop tlačítko: zahodit přání, zastavit server, stav na idle."""
        with self._lock:
            self._desired = None
            self._gen += 1
            self._state = ModelSwitchSnapshot()
        try:
            self._stop(self.cfg, quiet=True)
        except Exception:
            pass

    def reset(self) -> None:
        """Zahodit čekající přání bez zásahu do serveru."""
        with self._lock:
            self._desired = None

    def wait(self, timeout: float | None = None) -> bool:
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    # ------------------------------------------------------------------
    def _run(self) -> None:
        while True:
            with self._lock:
                if self._desired is None:
                    self._thread = None
                    return
                target, kv_profile, restart, on_success = self._desired
                self._desired = None
                gen = self._gen
            try:
                if not restart and self._running(self.cfg, target):
                    # model už běží (start tlačítko na běžícím modelu) - netřeba restart
                    self._publish(gen, ModelSwitchSnapshot("ready", target))
                    continue
                self._stop(self.cfg, quiet=True)
                if kv_profile:
                    self.cfg.set_kv_cache_mode(target, kv_profile)
                self._publish(gen, ModelSwitchSnapshot("starting", target))
                if not self._ensure(self.cfg, target):
                    raise RuntimeError("llama-server could not be prepared")
                callback_error = ""
                if on_success is not None:
                    try:
                        on_success(target)
                    except Exception as exc:
                        callback_error = t("Failed to save UI state: {error}", error=exc)
                self._publish(gen, ModelSwitchSnapshot("ready", target, callback_error))
            except Exception as exc:
                self._publish(gen, ModelSwitchSnapshot(
                    "failed", target, f"{type(exc).__name__}: {exc}"))

    def _publish(self, gen: int, state: ModelSwitchSnapshot) -> None:
        """Publikuj stav jen pokud ho nikdo nepřepsal novějším přáním."""
        with self._lock:
            if self._gen == gen:
                self._state = state
