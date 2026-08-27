"""Persistent isolated browser session for web-development agent tools."""
from __future__ import annotations

import asyncio
import atexit
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Coroutine


class BrowserSession:
    """Run Playwright on one dedicated event-loop thread.

    Agent steps use different worker threads, while Playwright objects must stay on
    the thread where they were created. Public methods therefore submit coroutines
    to this private loop and remain synchronous for ordinary tools.
    """

    def __init__(self, session=None):
        self._session = session
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._console: list[dict[str, Any]] = []
        self._network: list[dict[str, Any]] = []
        self._status = {"running": False, "url": "", "title": ""}
        self._registered_atexit = False

    def bind_session(self, session) -> None:
        self._session = session

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            return dict(self._status)

    def open(self, url: str, wait_until: str = "domcontentloaded") -> dict[str, Any]:
        return self._call(self._open(url, wait_until), timeout=90)

    def snapshot(self, max_text_chars: int = 12_000,
                 max_elements: int = 160) -> dict[str, Any]:
        return self._call(self._snapshot(max_text_chars, max_elements), timeout=30)

    def click(self, ref: str) -> dict[str, Any]:
        return self._call(self._click(ref), timeout=30)

    def fill(self, ref: str, text: str) -> dict[str, Any]:
        return self._call(self._fill(ref, text), timeout=30)

    def press(self, key: str) -> dict[str, Any]:
        return self._call(self._press(key), timeout=30)

    def wait(self, milliseconds: int) -> dict[str, Any]:
        return self._call(self._wait(milliseconds), timeout=35)

    def screenshot(self, full_page: bool = False) -> dict[str, Any]:
        return self._call(self._screenshot(full_page), timeout=45)

    def console(self, cursor: int = 0, max_entries: int = 100) -> dict[str, Any]:
        with self._state_lock:
            start = max(0, int(cursor))
            end = min(len(self._console), start + max(1, int(max_entries)))
            return {"cursor": end, "entries": list(self._console[start:end])}

    def network(self, cursor: int = 0, max_entries: int = 100) -> dict[str, Any]:
        with self._state_lock:
            start = max(0, int(cursor))
            end = min(len(self._network), start + max(1, int(max_entries)))
            return {"cursor": end, "entries": list(self._network[start:end])}

    def close(self) -> dict[str, Any]:
        if self._loop is None:
            return {"closed": True, "already_closed": True}
        return self._call(self._close(), timeout=30)

    def shutdown(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            self._call(self._close(), timeout=10)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop and self._thread and self._thread.is_alive():
            return self._loop
        with self._start_lock:
            if self._loop and self._thread and self._thread.is_alive():
                return self._loop
            ready = threading.Event()

            def run_loop() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                ready.set()
                loop.run_forever()
                loop.close()

            self._thread = threading.Thread(
                target=run_loop, name="qwen-browser", daemon=True)
            self._thread.start()
            if not ready.wait(5):
                raise RuntimeError("Browser event loop did not start")
            if not self._registered_atexit:
                atexit.register(self.shutdown)
                self._registered_atexit = True
        return self._loop

    def _call(self, coroutine: Coroutine, timeout: int):
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        return future.result(timeout=timeout)

    async def _ensure_browser(self):
        if self._browser and self._page:
            return self._page
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Run the environment repair/update step.") from exc
        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch(
                channel="msedge", headless=True)
        except Exception:
            candidates = [
                Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
                Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            ]
            executable = next((path for path in candidates if path.is_file()), None)
            if executable is None:
                raise RuntimeError("Microsoft Edge was not found on this Windows installation")
            try:
                self._browser = await self._playwright.chromium.launch(
                    executable_path=str(executable), headless=True)
            except Exception:
                await self._playwright.stop()
                self._playwright = None
                raise
        self._context = await self._browser.new_context(
            viewport={"width": 1440, "height": 900})
        self._page = await self._context.new_page()
        self._page.on("console", self._on_console)
        self._page.on("response", self._on_response)
        self._page.on("requestfailed", self._on_request_failed)
        with self._state_lock:
            self._status = {"running": True, "url": "about:blank", "title": ""}
        return self._page

    async def _refresh_status(self) -> dict[str, Any]:
        if not self._page:
            with self._state_lock:
                self._status = {"running": False, "url": "", "title": ""}
                return dict(self._status)
        title = await self._page.title()
        url = self._page.url
        with self._state_lock:
            self._status = {"running": True, "url": url, "title": title}
            return dict(self._status)

    async def _open(self, url: str, wait_until: str) -> dict[str, Any]:
        page = await self._ensure_browser()
        wait = wait_until if wait_until in {"commit", "domcontentloaded", "load", "networkidle"} \
            else "domcontentloaded"
        response = await page.goto(str(url), wait_until=wait, timeout=60_000)
        status = await self._refresh_status()
        status["http_status"] = response.status if response else None
        return status

    async def _snapshot(self, max_text_chars: int, max_elements: int) -> dict[str, Any]:
        page = await self._ensure_browser()
        payload = await page.evaluate(
            r"""({maxText, maxElements}) => {
              const visible = (el) => {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
              };
              const nodes = [...document.querySelectorAll(
                'a,button,input,textarea,select,[role="button"],[role="link"],[role="textbox"],summary'
              )].filter(visible).slice(0, maxElements);
              const elements = nodes.map((el, index) => {
                const ref = `e${index + 1}`;
                el.setAttribute('data-qwen-ref', ref);
                const id = el.id;
                const label = (id && document.querySelector(`label[for="${CSS.escape(id)}"]`)) ||
                  el.closest('label');
                const name = el.getAttribute('aria-label') ||
                  (label && label.innerText) || el.getAttribute('placeholder') ||
                  el.getAttribute('title') || el.innerText || el.value || '';
                const rect = el.getBoundingClientRect();
                return {
                  ref,
                  tag: el.tagName.toLowerCase(),
                  role: el.getAttribute('role') || '',
                  name: String(name).trim().replace(/\s+/g, ' ').slice(0, 240),
                  type: el.getAttribute('type') || '',
                  disabled: Boolean(el.disabled),
                  checked: typeof el.checked === 'boolean' ? el.checked : null,
                  x: Math.round(rect.x), y: Math.round(rect.y),
                  width: Math.round(rect.width), height: Math.round(rect.height)
                };
              });
              return {
                title: document.title,
                url: location.href,
                text: (document.body && document.body.innerText || '').slice(0, maxText),
                elements
              };
            }""",
            {"maxText": max(1000, min(int(max_text_chars), 100_000)),
             "maxElements": max(1, min(int(max_elements), 500))},
        )
        await self._refresh_status()
        return payload

    async def _locator_for_ref(self, ref: str):
        page = await self._ensure_browser()
        if not re.fullmatch(r"e\d+", str(ref)):
            raise ValueError(f"Invalid browser ref {ref!r}; call browser_snapshot again")
        locator = page.locator(f'[data-qwen-ref="{str(ref)}"]')
        if await locator.count() != 1:
            raise ValueError(
                f"Browser ref {ref!r} is missing or stale; call browser_snapshot again")
        return locator

    async def _click(self, ref: str) -> dict[str, Any]:
        locator = await self._locator_for_ref(ref)
        await locator.click(timeout=20_000)
        await asyncio.sleep(0.25)
        return await self._refresh_status()

    async def _fill(self, ref: str, text: str) -> dict[str, Any]:
        locator = await self._locator_for_ref(ref)
        await locator.fill(str(text), timeout=20_000)
        return await self._refresh_status()

    async def _press(self, key: str) -> dict[str, Any]:
        page = await self._ensure_browser()
        await page.keyboard.press(str(key))
        await asyncio.sleep(0.15)
        return await self._refresh_status()

    async def _wait(self, milliseconds: int) -> dict[str, Any]:
        await self._ensure_browser()
        duration = max(0, min(int(milliseconds), 30_000))
        await asyncio.sleep(duration / 1000)
        status = await self._refresh_status()
        status["waited_ms"] = duration
        return status

    async def _screenshot(self, full_page: bool) -> dict[str, Any]:
        page = await self._ensure_browser()
        if self._session is None:
            raise RuntimeError("Browser screenshot has no active chat session")
        directory = self._session.dir / "browser"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"browser-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.png"
        await page.screenshot(path=str(target), full_page=bool(full_page))
        return {"path": str(target), "width": 1440, "height": 900,
                "full_page": bool(full_page), **(await self._refresh_status())}

    async def _close(self) -> dict[str, Any]:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._page = self._context = self._browser = self._playwright = None
        with self._state_lock:
            self._status = {"running": False, "url": "", "title": ""}
            return {"closed": True, **self._status}

    def _on_console(self, message) -> None:
        try:
            entry = {"type": message.type, "text": message.text, "time": time.time()}
        except Exception:
            return
        with self._state_lock:
            self._console.append(entry)
            self._console = self._console[-500:]

    def _on_response(self, response) -> None:
        try:
            entry = {"method": response.request.method, "status": response.status,
                     "url": response.url, "time": time.time()}
        except Exception:
            return
        with self._state_lock:
            self._network.append(entry)
            self._network = self._network[-500:]

    def _on_request_failed(self, request) -> None:
        try:
            entry = {"method": request.method, "status": "failed", "url": request.url,
                     "error": str(request.failure or ""), "time": time.time()}
        except Exception:
            return
        with self._state_lock:
            self._network.append(entry)
            self._network = self._network[-500:]
