"""Bezpečnostní vrstva - úrovně autonomie a potvrzování akcí.

Režimy autonomie:
  supervised - každá WRITE akce (zápis souboru, shell, GUI) vyžaduje potvrzení
  semi       - potvrzení jen první WRITE akce v rámci úlohy
  auto       - bez potvrzení (+ vždy pyautogui FAILSAFE)
"""
from __future__ import annotations

from enum import Enum


class Risk(str, Enum):
    SAFE = "safe"      # čtení, listing, screenshot - nic nemění
    WRITE = "write"    # mění soubory / systém / GUI


class SafetyPolicy:
    def __init__(self, autonomy: str = "supervised", max_steps: int = 0, semi_max_steps: int = 0):
        if autonomy not in ("supervised", "semi", "auto"):
            raise ValueError(f"Neznámý režim autonomie: {autonomy}")
        self.autonomy = autonomy
        self.max_steps = max_steps
        self.semi_max_steps = semi_max_steps
        self._confirmed_this_task = False  # pro semi režim

    # ------------------------------------------------------------------
    def new_task(self) -> None:
        """Volat na začátku každé uživatelské úlohy."""
        self._confirmed_this_task = False

    def needs_confirmation(self, risk: Risk) -> bool:
        if risk == Risk.SAFE:
            return False
        if self.autonomy == "supervised":
            return True
        if self.autonomy == "semi":
            return not self._confirmed_this_task
        return False  # auto

    def mark_confirmed(self) -> None:
        self._confirmed_this_task = True

    def step_limit(self) -> int | None:
        value = self.semi_max_steps if self.autonomy == "semi" else self.max_steps
        return value if value > 0 else None

    def __repr__(self) -> str:  # pragma: no cover
        limit = self.step_limit()
        return f"SafetyPolicy(autonomy={self.autonomy!r}, max={limit or 'unlimited'})"
