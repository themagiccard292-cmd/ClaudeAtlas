"""Fakes for the Windows-only drivers + a live backend fixture talking to a fake `claude` CLI."""
from __future__ import annotations

import json
import os
import socket
import stat
import sys
import threading
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.server import Drivers, create_app, create_services, set_port  # noqa: E402

HERE = Path(__file__).parent


class FakeInputDriver:
    def __init__(self, size=(1920, 1080)):
        self._size = size
        self.pos = (0, 0)
        self.calls: list[tuple] = []
        self.down_keys: set[str] = set()
        self.down_buttons: set[str] = set()

    def size(self):
        return self._size

    def position(self):
        return self.pos

    def move_to(self, x, y):
        self.pos = (x, y)
        self.calls.append(("move", x, y))

    def mouse_down(self, button):
        self.down_buttons.add(button)
        self.calls.append(("mouse_down", button))

    def mouse_up(self, button):
        self.down_buttons.discard(button)
        self.calls.append(("mouse_up", button))

    def click(self, x, y, button, clicks):
        self.pos = (x, y)
        self.calls.append(("click", x, y, button, clicks))

    def scroll(self, dy):
        self.calls.append(("scroll", dy))

    def hscroll(self, dx):
        self.calls.append(("hscroll", dx))

    def key_down(self, key):
        self.down_keys.add(key)
        self.calls.append(("key_down", key))

    def key_up(self, key):
        self.down_keys.discard(key)
        self.calls.append(("key_up", key))

    def type_text(self, text):
        self.calls.append(("type", text))

    def of(self, name):
        return [c for c in self.calls if c[0] == name]


class FakeScreenDriver:
    def __init__(self, inp: FakeInputDriver):
        self.inp = inp

    def grab(self):
        w, h = self.inp.size()
        img = Image.new("RGB", (w, h), (40, 90, 140))
        d = ImageDraw.Draw(img)
        d.rectangle((w // 4, h // 4, w // 2, h // 2), fill=(240, 240, 240))
        d.text((w // 4 + 10, h // 4 + 10), "Hello fake desktop", fill=(0, 0, 0))
        return img, 0, 0

    def cursor(self):
        return self.inp.position()


class FakeNode:
    def __init__(self, name, control_type, rect, auto_id="", class_name="", enabled=True, value=None, kids=None):
        self.name, self.control_type, self.rect = name, control_type, rect
        self.automation_id, self.class_name, self.enabled = auto_id, class_name, enabled
        self._value, self._kids = value, kids or []
        self.invoked = False
        self.text_set = None
        self.focused = False

    def children(self):
        return self._kids

    def value(self):
        return self._value

    def invoke(self):
        self.invoked = True

    def set_text(self, text):
        self.text_set = text
        self._value = text

    def focus(self):
        self.focused = True


class FakeUiaBackend:
    def __init__(self):
        self.save_btn = FakeNode("Save", "Button", (100, 100, 180, 130), auto_id="btnSave")
        self.delete_btn = FakeNode("Delete", "Button", (200, 100, 280, 130))
        self.edit = FakeNode("File name", "Edit", (100, 200, 500, 230), value="report.txt")
        self.window = FakeNode("Untitled - Notepad", "Window", (0, 0, 800, 600), class_name="Notepad", kids=[
            FakeNode("", "Pane", (0, 0, 800, 600), kids=[self.save_btn, self.delete_btn, self.edit, FakeNode("hidden", "Button", (0, 0, 0, 0))]),
        ])
        self.other = FakeNode("Calculator", "Window", (900, 0, 1400, 500), class_name="CalcFrame")

    def root(self, scope):
        return self.window

    def top_windows(self):
        return [self.window, self.other, FakeNode("", "Window", (0, 0, 10, 10))]

    def element_at(self, x, y):
        for n in (self.save_btn, self.delete_btn):
            l, t, r, b = n.rect
            if l <= x <= r and t <= y <= b:
                return n
        return None


@pytest.fixture
def fake_input():
    return FakeInputDriver()


@pytest.fixture
def fake_uia():
    return FakeUiaBackend()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def fake_claude_path(tmp_path) -> str:
    wrapper = tmp_path / "bin" / "claude"
    wrapper.parent.mkdir()
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{HERE / "fake_claude.py"}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    return str(wrapper)


class LiveBackend:
    def __init__(self, svc, port, ws_token, drv):
        self.svc, self.port, self.ws_token, self.drv = svc, port, ws_token, drv
        self.url = f"ws://127.0.0.1:{port}/ws?token={ws_token}"


@pytest.fixture
def live_backend(tmp_path, fake_claude_path, fake_uia, monkeypatch):
    """A real uvicorn server (real sockets, so the fake CLI can call /mcp) with fake OS drivers."""
    import uvicorn

    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude-calls.jsonl"))
    monkeypatch.setenv("FAKE_CLAUDE_TOOLLOG", str(tmp_path / "tool-calls.jsonl"))
    inp = FakeInputDriver()
    drv = Drivers(input=inp, screen=FakeScreenDriver(inp), uia=fake_uia, open_url=lambda u: None, hotkeys=False)
    ws_token = "w" * 32
    port = _free_port()
    svc = create_services(tmp_path / "data", ws_token=ws_token, mcp_token="m" * 32, drivers=drv, port=port)
    svc.settings.update({"claude_path": fake_claude_path, "settle_ms": 0, "human_mouse": False})
    set_port(svc, port)
    app = create_app(svc)
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(16)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    t = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    t.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    assert server.started, "uvicorn did not start"
    lb = LiveBackend(svc, port, ws_token, inp)
    lb.tmp = tmp_path  # type: ignore[attr-defined]
    yield lb
    server.should_exit = True
    t.join(timeout=10)
    sock.close()
