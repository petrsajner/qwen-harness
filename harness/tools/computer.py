"""Computer-use nástroje - ovládání počítače přes screenshoty a GUI akce.

Souřadnicový systém: model pracuje v pixelech obrázku, který dostal
(screenshot může být downscaled). Nástroje přepočítávají na reálné
souřadnice obrazovky automaticky podle posledního screenshotu.

FAILSAFE: pyautogi fail-safe je VŽDY zapnutý - rychlý pohyb myši do
levého horního rohu obrazovky vyhodí výjimku a akce se přeruší.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from harness.safety import Risk
from harness.tools.base import AgentContext, Tool

# Stav posledního screenshotu pro přepočet souřadnic (module-level, sdílený mezi nástroji)
_last_shot: dict = {"screen_w": 0, "screen_h": 0, "img_w": 0, "img_h": 0, "origin_x": 0, "origin_y": 0}


def _to_screen(x: float, y: float) -> tuple[int, int]:
    """Převeď souřadnice z prostoru obrázku na reálnou obrazovku."""
    if _last_shot["img_w"] == 0:
        return round(x), round(y)  # žádný screenshot zatím - předpokládej 1:1
    sx = _last_shot["origin_x"] + x * (_last_shot["screen_w"] / _last_shot["img_w"])
    sy = _last_shot["origin_y"] + y * (_last_shot["screen_h"] / _last_shot["img_h"])
    return round(sx), round(sy)


def _pyautogui():
    import pyautogui
    pyautogui.FAILSAFE = True  # vždy - myš do rohu = přerušení
    pyautogui.FAILSAFE_POINTS = [(0, 0)]
    return pyautogui


class ScreenshotTool(Tool):
    name = "screenshot"
    description = ("Capture a screenshot of the primary monitor. The image is attached to the conversation "
                   "so you can see the screen. Returns real screen size and image size - use IMAGE pixel "
                   "coordinates for click/move/scroll tools. ALWAYS call this first, before any GUI action.")
    parameters = {}

    def run(self, ctx: AgentContext) -> str:
        import mss
        from PIL import Image

        ccfg = ctx.cfg.computer
        with mss.mss() as sct:
            mon = sct.monitors[1]  # primární monitor
            shot = sct.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.rgb)

        screen_w, screen_h = img.size
        max_edge = int(ccfg.get("screenshot_max_edge", 1920))
        if max(img.size) > max_edge:
            scale = max_edge / max(img.size)
            img = img.resize((round(img.size[0] * scale), round(img.size[1] * scale)), Image.LANCZOS)
        if ccfg.get("screenshot_grayscale"):
            img = img.convert("L")

        ctx.session.img_dir.mkdir(parents=True, exist_ok=True)
        path = ctx.session.img_dir / f"shot-{uuid.uuid4().hex[:8]}.png"
        img.save(path, "PNG", optimize=True)

        _last_shot.update(
            screen_w=screen_w, screen_h=screen_h,
            img_w=img.size[0], img_h=img.size[1],
            origin_x=0, origin_y=0,
        )
        ctx.pending_images.append(path)
        return (f"Screenshot captured: image {img.size[0]}x{img.size[1]} px (real screen {screen_w}x{screen_h}). "
                f"Coordinates for GUI tools are in IMAGE pixel space. The screenshot is attached to your next message.")


class ClickTool(Tool):
    name = "click"
    description = "Click the mouse at IMAGE coordinates (from the latest screenshot)."
    parameters = {
        "x": {"type": "integer", "description": "X in image pixels"},
        "y": {"type": "integer", "description": "Y in image pixels"},
        "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "Mouse button (default left)"},
        "clicks": {"type": "integer", "description": "Number of clicks (2 = double-click, default 1)"},
    }
    required = ["x", "y"]
    risk = Risk.WRITE

    def run(self, ctx: AgentContext, x: int, y: int, button: str = "left", clicks: int = 1) -> str:
        pag = _pyautogui()
        pag.PAUSE = float(ctx.cfg.computer.get("pause_between_actions", 0.15))
        sx, sy = _to_screen(x, y)
        pag.click(sx, sy, clicks=clicks, button=button, duration=0.2)
        return f"Clicked {button} x{clicks} at image ({x},{y}) -> screen ({sx},{sy})"


class TypeTextTool(Tool):
    name = "type_text"
    description = ("Type text at the current cursor position. Handles unicode (Czech diacritics etc.) "
                   "via clipboard paste automatically. Click the target field first!")
    parameters = {"text": {"type": "string", "description": "Text to type"}}
    required = ["text"]
    risk = Risk.WRITE

    def run(self, ctx: AgentContext, text: str) -> str:
        pag = _pyautogui()
        pag.PAUSE = float(ctx.cfg.computer.get("pause_between_actions", 0.15))
        try:
            text.encode("ascii")
            ascii_ok = True
        except UnicodeEncodeError:
            ascii_ok = False
        if ascii_ok and len(text) < 200:
            pag.write(text, interval=0.02)
            return f"Typed {len(text)} chars (keyboard)"
        # non-ASCII nebo dlouhý text -> schránka + Ctrl+V
        import pyperclip
        pyperclip.copy(text)
        time.sleep(0.1)
        pag.hotkey("ctrl", "v")
        return f"Pasted {len(text)} chars via clipboard"


class PressKeyTool(Tool):
    name = "press_key"
    description = ("Press a key or key combination. Examples: 'enter', 'esc', 'tab', 'win', 'ctrl+s', "
                   "'ctrl+shift+t', 'alt+f4', 'win+d'. Use lowercase key names.")
    parameters = {"keys": {"type": "string", "description": "Key or combo, joined with '+'"}}
    required = ["keys"]
    risk = Risk.WRITE

    KEY_ALIASES = {"windows": "win", "super": "win", "return": "enter", "del": "delete", "space": "space"}

    def run(self, ctx: AgentContext, keys: str) -> str:
        pag = _pyautogui()
        pag.PAUSE = float(ctx.cfg.computer.get("pause_between_actions", 0.15))
        parts = [self.KEY_ALIASES.get(k.strip().lower(), k.strip().lower()) for k in keys.split("+") if k.strip()]
        if not parts:
            return "ERROR: empty key combo"
        if len(parts) == 1:
            pag.press(parts[0])
        else:
            pag.hotkey(*parts)
        return f"Pressed: {'+'.join(parts)}"


class ScrollTool(Tool):
    name = "scroll"
    description = "Scroll the mouse wheel. Positive amount scrolls UP, negative scrolls DOWN. Optional x,y = image coordinates to scroll at."
    parameters = {
        "amount": {"type": "integer", "description": "Scroll amount in 'clicks' (positive=up, negative=down)"},
        "x": {"type": "integer", "description": "X in image pixels (optional)"},
        "y": {"type": "integer", "description": "Y in image pixels (optional)"},
    }
    required = ["amount"]
    risk = Risk.WRITE

    def run(self, ctx: AgentContext, amount: int, x: int | None = None, y: int | None = None) -> str:
        pag = _pyautogui()
        pag.PAUSE = float(ctx.cfg.computer.get("pause_between_actions", 0.15))
        if x is not None and y is not None:
            sx, sy = _to_screen(x, y)
            pag.moveTo(sx, sy)
        pag.scroll(amount)
        return f"Scrolled {amount:+d}"


class MoveMouseTool(Tool):
    name = "move_mouse"
    description = "Move the mouse pointer to IMAGE coordinates (useful for hover tooltips)."
    parameters = {
        "x": {"type": "integer", "description": "X in image pixels"},
        "y": {"type": "integer", "description": "Y in image pixels"},
    }
    required = ["x", "y"]
    risk = Risk.WRITE

    def run(self, ctx: AgentContext, x: int, y: int) -> str:
        pag = _pyautogui()
        pag.PAUSE = float(ctx.cfg.computer.get("pause_between_actions", 0.15))
        sx, sy = _to_screen(x, y)
        pag.moveTo(sx, sy, duration=0.2)
        return f"Mouse moved to image ({x},{y}) -> screen ({sx},{sy})"


class GetScreenSizeTool(Tool):
    name = "get_screen_info"
    description = "Get screen resolution and info about the last screenshot (coordinate mapping)."
    parameters = {}

    def run(self, ctx: AgentContext) -> str:
        pag = _pyautogui()
        w, h = pag.size()
        if _last_shot["img_w"]:
            return (f"Screen: {w}x{h}px. Last screenshot image: {_last_shot['img_w']}x{_last_shot['img_h']}px "
                    f"(scale {_last_shot['screen_w'] / _last_shot['img_w']:.2f}x). Use IMAGE pixel coordinates.")
        return f"Screen: {w}x{h}px. No screenshot taken yet - call screenshot() first."


def register_computer_tools(registry) -> None:
    registry.register(ScreenshotTool())
    registry.register(ClickTool())
    registry.register(TypeTextTool())
    registry.register(PressKeyTool())
    registry.register(ScrollTool())
    registry.register(MoveMouseTool())
    registry.register(GetScreenSizeTool())
