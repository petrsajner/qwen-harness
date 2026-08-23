"""Asynchronní orchestrace startu a přepínání lokálního modelu."""
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


class ModelSwitchController:
    """Zajistí nejvýše jeden background start/switch a vystaví jeho stav UI."""

    def __init__(self, cfg: Config, *,
                 ensure_fn: Callable[[Config, str], bool] = servermgmt.ensure,
                 stop_fn: Callable[..., bool] = servermgmt.stop):
        self.cfg = cfg
        self._ensure = ensure_fn
        self._stop = stop_fn
        self._lock = threading.Lock()
        self._state = ModelSwitchSnapshot()
        self._thread: threading.Thread | None = None

    def snapshot(self) -> ModelSwitchSnapshot:
        with self._lock:
            return self._state

    def request(self, model_key: str, *, restart: bool = False,
                on_success: Callable[[str], None] | None = None) -> bool:
        with self._lock:
            if self._state.busy:
                return False
            self._state = ModelSwitchSnapshot("starting", model_key)
            self._thread = threading.Thread(
                target=self._run,
                args=(model_key, restart, on_success),
                daemon=True,
                name=f"model-switch-{model_key}",
            )
            self._thread.start()
            return True

    def reset(self) -> None:
        with self._lock:
            if not self._state.busy:
                self._state = ModelSwitchSnapshot()

    def wait(self, timeout: float | None = None) -> bool:
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    def _run(self, model_key: str, restart: bool,
             on_success: Callable[[str], None] | None) -> None:
        try:
            if restart:
                self._stop(self.cfg, quiet=True)
            if not self._ensure(self.cfg, model_key):
                raise RuntimeError("llama-server could not be prepared")
            callback_error = ""
            if on_success is not None:
                try:
                    on_success(model_key)
                except Exception as exc:
                    callback_error = t("Failed to save UI state: {error}", error=exc)
            with self._lock:
                self._state = ModelSwitchSnapshot("ready", model_key, callback_error)
        except Exception as exc:
            with self._lock:
                self._state = ModelSwitchSnapshot(
                    "failed", model_key, f"{type(exc).__name__}: {exc}"
                )
