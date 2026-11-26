"""Live screen stream (v1.1): the user watches the desktop in real time instead of looking at screenshots.

Frames are JPEG binary WebSocket messages, sent only to clients that subscribed (`live.subscribe`).
Each client keeps only the *latest* frame, so a slow viewer skips frames rather than lagging.
Unchanged screens are not re-sent (a 1 s keep-alive still goes out). Claude's own perception is
unchanged (it still gets screenshots through MCP) - this stream is purely for the human.
"""
from __future__ import annotations

import asyncio
import io
import logging
import time
from typing import Callable

from PIL import Image

from .config import Settings
from .events import Hub

log = logging.getLogger("live")


class LiveStreamer:
    def __init__(self, hub: Hub, grab: Callable[[], tuple[Image.Image, int, int]], settings: Callable[[], Settings]) -> None:
        self.hub, self.grab, self.settings = hub, grab, settings
        self._task: asyncio.Task | None = None
        self._reported_error = False
        self.frames_sent = 0

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="live-stream")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    def encode(self, cfg: Settings) -> tuple[bytes, bytes, tuple[int, int]]:
        """Blocking: grab + downscale + JPEG. Returns (jpeg, tiny change-signature, physical size)."""
        img, _l, _t = self.grab()
        pw, ph = img.size
        k = min(1.0, cfg.live_width / pw)
        small = img.resize((max(1, round(pw * k)), max(1, round(ph * k))), Image.BILINEAR) if k < 1 else img
        sig = small.resize((48, 27), Image.BILINEAR).tobytes()
        buf = io.BytesIO()
        small.convert("RGB").save(buf, "JPEG", quality=cfg.live_quality)
        return buf.getvalue(), sig, (pw, ph)

    async def _loop(self) -> None:
        last_sig: bytes | None = None
        last_sent = 0.0
        announced: set[int] = set()
        while True:
            viewers = [c for c in self.hub.clients if c.live]
            if not viewers:
                last_sig = None
                announced.clear()
                await asyncio.sleep(0.15)
                continue
            cfg = self.settings()
            t0 = time.monotonic()
            try:
                jpeg, sig, (pw, ph) = await asyncio.to_thread(self.encode, cfg)
                self._reported_error = False
            except Exception as e:  # noqa: BLE001 - e.g. screen capture unavailable
                if not self._reported_error:
                    self._reported_error = True
                    self.hub.emit("live.error", message=str(e))
                await asyncio.sleep(1.0)
                continue
            for c in viewers:
                if id(c) not in announced:  # new viewer: tell it the real screen size, send a frame right away
                    announced.add(id(c))
                    self.hub.send_to(c, "live.info", width=pw, height=ph)
                    c.offer_frame(jpeg)
            now = time.monotonic()
            if sig != last_sig or now - last_sent > 1.0:
                for c in viewers:
                    c.offer_frame(jpeg)
                self.frames_sent += 1
                last_sent, last_sig = now, sig
            await asyncio.sleep(max(0.0, 1.0 / cfg.live_fps - (time.monotonic() - t0)))
