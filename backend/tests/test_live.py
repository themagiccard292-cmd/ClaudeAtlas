"""v1.1 live view: binary frame stream + Claude's cursor events."""
from __future__ import annotations

import asyncio
import io
import json
import time

from PIL import Image
from websockets.asyncio.client import connect

from app.events import Client


async def collect(ws, seconds, frames, events):
    end = time.time() + seconds
    while time.time() < end:
        try:
            m = await asyncio.wait_for(ws.recv(), 0.2)
        except asyncio.TimeoutError:
            continue
        (frames if isinstance(m, bytes) else events).append(m if isinstance(m, bytes) else json.loads(m))


async def test_subscribed_client_receives_scaled_jpeg_frames(live_backend):
    lb = live_backend
    lb.svc.settings.update({"live_fps": 20, "live_width": 960})
    async with connect(lb.url, origin=None) as ws:
        await ws.send(json.dumps({"type": "hello"}))
        frames, events = [], []
        await collect(ws, 0.6, frames, events)
        assert frames == []  # not subscribed: no video
        await ws.send(json.dumps({"type": "live.subscribe"}))
        await collect(ws, 1.5, frames, [e for e in events])
        info = []
        await collect(ws, 0.1, frames, info)
        assert frames, "no frames received after subscribing"
        img = Image.open(io.BytesIO(frames[0]))
        assert img.format == "JPEG" and img.size == (960, 540)  # 1920x1080 desktop scaled to live_width
        # screen did not change -> unchanged frames are suppressed (only the 1 s keep-alive + first frame)
        assert len(frames) <= 4
        await ws.send(json.dumps({"type": "live.unsubscribe"}))
        await asyncio.sleep(0.4)
        before = len(frames)
        await collect(ws, 1.3, frames, [])
        assert len(frames) == before  # stream stops when nobody watches


async def test_live_info_event_reports_real_screen_size(live_backend):
    async with connect(live_backend.url, origin=None) as ws:
        await ws.send(json.dumps({"type": "live.subscribe"}))
        frames, events = [], []
        await collect(ws, 1.0, frames, events)
        info = next(e for e in events if e["type"] == "live.info")
        assert (info["width"], info["height"]) == (1920, 1080)


async def test_changed_screen_is_streamed_again(live_backend):
    lb = live_backend
    lb.svc.settings.update({"live_fps": 20})
    drv = lb.svc.live.grab.__self__  # FakeScreenDriver
    async with connect(lb.url, origin=None) as ws:
        await ws.send(json.dumps({"type": "live.subscribe"}))
        frames: list = []
        await collect(ws, 0.8, frames, [])
        n = len(frames)
        orig = drv.grab

        def changed():
            img, l, t = orig()
            img.paste((255, 0, 0), (0, 0, 800, 600))
            return img, l, t

        lb.svc.live.grab = changed
        await collect(ws, 0.8, frames, [])
        assert len(frames) > n


async def test_cursor_events_follow_a_real_run(live_backend, monkeypatch):
    lb = live_backend
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", json.dumps([
        {"tool": "click", "args": {"x": 640, "y": 360}},
        {"tool": "type_text", "args": {"text": "hi"}},
        {"tool": "scroll", "args": {"direction": "down", "amount": 2}},
        {"tool": "press_keys", "args": {"keys": "ctrl+s"}},
    ]))
    async with connect(lb.url, origin=None) as ws:
        await ws.send(json.dumps({"type": "chat.send", "text": "go"}))
        events: list = []
        for _ in range(200):
            await collect(ws, 0.1, [], events)
            if any(e["type"] == "run.finished" for e in events):
                break
        cur = [e for e in events if e["type"] == "agent.cursor"]
        click = next(e for e in cur if e["kind"] == "click")
        assert abs(click["fx"] - 0.5) < 0.01 and abs(click["fy"] - 0.5) < 0.01  # normalised: model centre = screen centre
        assert click["button"] == "left" and click["clicks"] == 1
        assert [e["kind"] for e in cur if e["kind"] != "move"] == ["click", "type", "scroll", "key"]
        assert next(e for e in cur if e["kind"] == "key")["keys"] == "ctrl+s"
        assert next(e for e in cur if e["kind"] == "type")["n"] == 2
        assert all(0 <= e["fx"] <= 1 and 0 <= e["fy"] <= 1 for e in cur)


async def test_slow_viewer_only_keeps_latest_frame():
    sent: list[bytes] = []

    class SlowWs:
        async def send_bytes(self, b):
            await asyncio.sleep(0.05)
            sent.append(b)

        async def send_text(self, t):
            pass

    c = Client(SlowWs())
    t = asyncio.create_task(c.pump_frames())
    for i in range(10):
        c.offer_frame(bytes([i]))
        await asyncio.sleep(0.001)
    await asyncio.sleep(0.3)
    t.cancel()
    assert sent[-1] == bytes([9]) and len(sent) < 5  # stale frames dropped, newest delivered
