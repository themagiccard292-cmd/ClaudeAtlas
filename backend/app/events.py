"""Fan-out of backend events to every connected WebSocket client (main window + HUD)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

log = logging.getLogger("hub")


class Client:
    def __init__(self, ws: Any):
        self.ws = ws
        self.queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=2000)
        self.task: asyncio.Task | None = None
        self.live = False  # subscribed to the live screen stream
        self.frame_task: asyncio.Task | None = None
        self._frame: bytes | None = None
        self._frame_evt = asyncio.Event()

    def offer_frame(self, data: bytes) -> None:
        """Latest-frame-wins: a slow client skips frames instead of building a backlog (= lag)."""
        self._frame = data
        self._frame_evt.set()

    async def pump_frames(self) -> None:
        while True:
            await self._frame_evt.wait()
            self._frame_evt.clear()
            data, self._frame = self._frame, None
            if data is None:
                continue
            try:
                await self.ws.send_bytes(data)
            except Exception:
                return

    async def pump(self) -> None:
        while True:
            msg = await self.queue.get()
            if msg is None:
                return
            try:
                await self.ws.send_text(msg)
            except Exception:  # socket closed
                return


class Hub:
    def __init__(self) -> None:
        self.clients: set[Client] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.history: list[dict] = []  # tests / debugging: last events
        self.record = False

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def add(self, ws: Any) -> Client:
        c = Client(ws)
        c.task = asyncio.create_task(c.pump())
        c.frame_task = asyncio.create_task(c.pump_frames())
        self.clients.add(c)
        return c

    async def remove(self, c: Client) -> None:
        self.clients.discard(c)
        with contextlib.suppress(Exception):
            c.queue.put_nowait(None)
        for t in (c.task, c.frame_task):
            if t:
                t.cancel()

    def emit(self, type_: str, **payload: Any) -> None:
        """Queue an event for all clients. Must be called on the event-loop thread."""
        event = {"type": type_, **payload}
        if self.record:
            self.history.append(event)
        if not self.clients:
            return
        data = json.dumps(event, default=str)
        for c in list(self.clients):
            try:
                c.queue.put_nowait(data)
            except asyncio.QueueFull:
                log.warning("dropping event for slow client: %s", type_)

    def emit_threadsafe(self, type_: str, **payload: Any) -> None:
        """Emit from any thread (hotkey listener, worker threads)."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(lambda: self.emit(type_, **payload))

    def send_to(self, c: Client, type_: str, **payload: Any) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            c.queue.put_nowait(json.dumps({"type": type_, **payload}, default=str))
