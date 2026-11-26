"""Mouse / keyboard control. Every primitive passes through the kill switch + pause gate."""
from __future__ import annotations

import contextlib
import math
import threading
from typing import Callable, Protocol

from .safety import KillSwitch


class ToolError(Exception):
    """A problem that should be reported back to Claude as a normal tool error."""


# ----------------------------------------------------------------------------------
# Key-name normalisation (Claude may say "Return", "Control", "super", "Page_Down" ...)
# ----------------------------------------------------------------------------------
_ALIASES = {
    "return": "enter", "enter": "enter", "kp_enter": "enter",
    "esc": "esc", "escape": "esc",
    "super": "win", "super_l": "win", "super_r": "win", "win": "win", "windows": "win", "meta": "win", "cmd": "win", "command": "win",
    "control": "ctrl", "ctrl": "ctrl", "control_l": "ctrl", "control_r": "ctrl", "ctl": "ctrl",
    "alt": "alt", "alt_l": "alt", "alt_r": "alt", "option": "alt", "menu": "alt",
    "shift": "shift", "shift_l": "shift", "shift_r": "shift",
    "backspace": "backspace", "back_space": "backspace", "delete": "delete", "del": "delete",
    "tab": "tab", "space": "space", "spacebar": "space",
    "page_up": "pageup", "pageup": "pageup", "prior": "pageup", "pgup": "pageup",
    "page_down": "pagedown", "pagedown": "pagedown", "next": "pagedown", "pgdn": "pagedown",
    "home": "home", "end": "end", "insert": "insert", "ins": "insert",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
    "caps_lock": "capslock", "capslock": "capslock", "num_lock": "numlock", "scroll_lock": "scrolllock",
    "print": "printscreen", "printscreen": "printscreen", "prtsc": "printscreen", "pause": "pause",
    "plus": "+", "minus": "-", "comma": ",", "period": ".", "slash": "/", "backslash": "\\",
}
MODIFIERS = {"ctrl", "alt", "shift", "win"}
_BLOCKED = [{"win", "l"}, {"ctrl", "alt", "delete"}]


def split_combo(text: str) -> list[str]:
    """'ctrl+shift+t' -> ['ctrl','shift','t']; handles a literal '+' key ('ctrl++')."""
    text = text.strip()
    if not text:
        raise ToolError("empty key combination")
    if text == "+":
        return ["+"]
    if text.endswith("++"):  # e.g. "ctrl++" = Ctrl and the plus key
        return [p.strip() for p in text[:-2].split("+") if p.strip()] + ["+"]
    if "+" in text:
        return [p.strip() for p in text.split("+") if p.strip()]
    return text.split()  # "ctrl shift t" style


def normalize_key(name: str) -> str:
    k = name.strip()
    low = k.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    if len(k) == 1:
        return k.lower() if k.isalpha() else k
    if low.startswith("f") and low[1:].isdigit():
        return low
    return low


def normalize_combo(text: str) -> list[str]:
    keys = [normalize_key(k) for k in split_combo(text)]
    if set(keys) in _BLOCKED or any(set(keys) >= b for b in _BLOCKED):
        raise ToolError(f"'{text}' is blocked for safety (it would lock or hijack the session)")
    return keys


# ----------------------------------------------------------------------------------
# Driver abstraction
# ----------------------------------------------------------------------------------
class InputDriver(Protocol):
    def size(self) -> tuple[int, int]: ...
    def position(self) -> tuple[int, int]: ...
    def move_to(self, x: int, y: int) -> None: ...
    def mouse_down(self, button: str) -> None: ...
    def mouse_up(self, button: str) -> None: ...
    def click(self, x: int, y: int, button: str, clicks: int) -> None: ...
    def scroll(self, dy: int) -> None: ...
    def hscroll(self, dx: int) -> None: ...
    def key_down(self, key: str) -> None: ...
    def key_up(self, key: str) -> None: ...
    def type_text(self, text: str) -> None: ...


class PyAutoGuiDriver:
    """pyautogui for the mouse and key chords, pynput for (unicode-safe) text entry."""

    def __init__(self) -> None:
        import pyautogui
        from pynput.keyboard import Controller

        pyautogui.FAILSAFE = False  # corner-to-abort would fire on legit clicks (e.g. window close button)
        pyautogui.PAUSE = 0
        self.pg = pyautogui
        self.kb = Controller()

    def size(self) -> tuple[int, int]:
        s = self.pg.size()
        return int(s.width), int(s.height)

    def position(self) -> tuple[int, int]:
        p = self.pg.position()
        return int(p.x), int(p.y)

    def move_to(self, x: int, y: int) -> None:
        self.pg.moveTo(x, y, duration=0)

    def mouse_down(self, button: str) -> None:
        self.pg.mouseDown(button=button)

    def mouse_up(self, button: str) -> None:
        self.pg.mouseUp(button=button)

    def click(self, x: int, y: int, button: str, clicks: int) -> None:
        self.pg.click(x, y, clicks=clicks, interval=0.09, button=button)

    def scroll(self, dy: int) -> None:
        self.pg.scroll(dy)

    def hscroll(self, dx: int) -> None:
        self.pg.hscroll(dx)

    def _check(self, key: str) -> None:
        if key not in self.pg.KEYBOARD_KEYS:
            raise ToolError(f"unknown key '{key}'")

    def key_down(self, key: str) -> None:
        self._check(key)
        self.pg.keyDown(key)

    def key_up(self, key: str) -> None:
        self._check(key)
        self.pg.keyUp(key)

    def type_text(self, text: str) -> None:
        try:
            self.kb.type(text)
        except Exception as e:  # pynput raises InvalidCharacterException for unsupported characters
            raise ToolError(f"could not type that text: {e}") from None


# ----------------------------------------------------------------------------------
# Controller
# ----------------------------------------------------------------------------------
class InputController:
    WHEEL_NOTCH = 120  # one physical wheel click on Windows

    def __init__(self, driver: InputDriver, kill: KillSwitch, shield=None, human_mouse: Callable[[], bool] = lambda: True):
        self.d = driver
        self.kill = kill
        self.shield = shield
        self._human = human_mouse
        self._lock = threading.RLock()
        self._down_keys: list[str] = []
        self._down_buttons: set[str] = set()
        kill.on_trigger(lambda _r: self.release_all())

    # -- helpers --------------------------------------------------------------------
    def _shield(self, x: int | None, y: int | None):
        return self.shield.around(x, y) if self.shield else contextlib.nullcontext()

    def _clamp(self, x: float, y: float) -> tuple[int, int]:
        w, h = self.d.size()
        return min(max(int(round(x)), 0), w - 1), min(max(int(round(y)), 0), h - 1)

    def _sleep(self, seconds: float) -> None:
        self.kill.gate()
        self.kill.wait(seconds)

    def release_all(self) -> None:
        """Let go of anything we are holding so a halt never leaves a stuck key/button."""
        for k in reversed(self._down_keys):
            with contextlib.suppress(Exception):
                self.d.key_up(k)
        self._down_keys.clear()
        for b in list(self._down_buttons):
            with contextlib.suppress(Exception):
                self.d.mouse_up(b)
        self._down_buttons.clear()

    def _glide(self, x: int, y: int) -> None:
        """Move the pointer, optionally along a short eased path (checks the kill switch each step)."""
        self.kill.gate()
        if not self._human():
            self.d.move_to(x, y)
            return
        x0, y0 = self.d.position()
        dist = math.hypot(x - x0, y - y0)
        if dist < 6:
            self.d.move_to(x, y)
            return
        duration = min(0.4, 0.08 + dist / 4500)
        steps = max(4, int(duration / 0.012))
        for i in range(1, steps + 1):
            t = i / steps
            e = 1 - (1 - t) ** 3  # ease-out cubic
            self.kill.gate()
            self.d.move_to(round(x0 + (x - x0) * e), round(y0 + (y - y0) * e))
            self.kill.wait(duration / steps)
        self.d.move_to(x, y)

    @contextlib.contextmanager
    def _modifiers(self, modifiers: list[str]):
        held: list[str] = []
        try:
            for m in modifiers:
                self.kill.gate()
                self.d.key_down(m)
                held.append(m)
                self._down_keys.append(m)
            yield
        finally:
            for m in reversed(held):
                with contextlib.suppress(Exception):
                    self.d.key_up(m)
                if m in self._down_keys:
                    self._down_keys.remove(m)

    @staticmethod
    def parse_modifiers(text: str | None) -> list[str]:
        if not text:
            return []
        mods = [normalize_key(k) for k in split_combo(text)]
        bad = [m for m in mods if m not in MODIFIERS]
        if bad:
            raise ToolError(f"'modifiers' only accepts ctrl/alt/shift/win, got {bad}")
        return mods

    # -- pointer --------------------------------------------------------------------
    def move(self, x: float, y: float) -> tuple[int, int]:
        with self._lock:
            px, py = self._clamp(x, y)
            self._glide(px, py)
            return px, py

    def click(self, x: float, y: float, button: str = "left", clicks: int = 1, modifiers: str | None = None) -> tuple[int, int]:
        if button not in ("left", "right", "middle"):
            raise ToolError("button must be left, right or middle")
        if clicks not in (1, 2, 3):
            raise ToolError("clicks must be 1, 2 or 3")
        mods = self.parse_modifiers(modifiers)
        with self._lock:
            px, py = self._clamp(x, y)
            self._glide(px, py)
            with self._shield(px, py), self._modifiers(mods):
                self.kill.gate()
                self.d.click(px, py, button, clicks)
            return px, py

    def drag(self, x1: float, y1: float, x2: float, y2: float, modifiers: str | None = None) -> None:
        mods = self.parse_modifiers(modifiers)
        with self._lock:
            ax, ay = self._clamp(x1, y1)
            bx, by = self._clamp(x2, y2)
            self._glide(ax, ay)
            with self._shield(ax, ay), self._modifiers(mods):
                self.kill.gate()
                self.d.mouse_down("left")
                self._down_buttons.add("left")
                try:
                    self._sleep(0.08)
                    dist = math.hypot(bx - ax, by - ay)
                    steps = max(8, int(dist / 18))
                    for i in range(1, steps + 1):
                        self.kill.gate()
                        t = i / steps
                        self.d.move_to(round(ax + (bx - ax) * t), round(ay + (by - ay) * t))
                        self.kill.wait(0.012)
                    self._sleep(0.1)
                finally:
                    self.d.mouse_up("left")
                    self._down_buttons.discard("left")

    def mouse_button(self, down: bool, button: str = "left") -> None:
        with self._lock:
            self.kill.gate()
            if down:
                self.d.mouse_down(button)
                self._down_buttons.add(button)
            else:
                self.d.mouse_up(button)
                self._down_buttons.discard(button)

    def scroll(self, x: float | None, y: float | None, direction: str, amount: int = 3) -> None:
        if direction not in ("up", "down", "left", "right"):
            raise ToolError("direction must be up, down, left or right")
        amount = max(1, min(int(amount), 50))
        with self._lock:
            px = py = None
            if x is not None and y is not None:
                px, py = self._clamp(x, y)
                self._glide(px, py)
            with self._shield(px, py):
                for _ in range(amount):
                    self.kill.gate()
                    n = self.WHEEL_NOTCH
                    if direction == "up":
                        self.d.scroll(n)
                    elif direction == "down":
                        self.d.scroll(-n)
                    elif direction == "right":
                        self.d.hscroll(n)
                    else:
                        self.d.hscroll(-n)
                    self.kill.wait(0.03)

    # -- keyboard -------------------------------------------------------------------
    def type_text(self, text: str) -> None:
        with self._lock:
            for i in range(0, len(text), 16):  # chunked so the kill switch can interrupt long text
                self.kill.gate()
                self.d.type_text(text[i : i + 16])
                self.kill.wait(0.01)

    def press(self, combo: str, repeat: int = 1) -> None:
        keys = normalize_combo(combo)
        repeat = max(1, min(int(repeat), 100))
        with self._lock:
            for _ in range(repeat):
                pressed: list[str] = []
                try:
                    for k in keys:
                        self.kill.gate()
                        self.d.key_down(k)
                        pressed.append(k)
                        self._down_keys.append(k)
                        self.kill.wait(0.01)
                finally:
                    for k in reversed(pressed):
                        with contextlib.suppress(Exception):
                            self.d.key_up(k)
                        if k in self._down_keys:
                            self._down_keys.remove(k)
                self.kill.wait(0.04)

    def hold(self, combo: str, seconds: float) -> None:
        keys = normalize_combo(combo)
        seconds = max(0.0, min(float(seconds), 60.0))
        with self._lock:
            pressed: list[str] = []
            try:
                for k in keys:
                    self.kill.gate()
                    self.d.key_down(k)
                    pressed.append(k)
                    self._down_keys.append(k)
                self.kill.wait(seconds)
            finally:
                for k in reversed(pressed):
                    with contextlib.suppress(Exception):
                        self.d.key_up(k)
                    if k in self._down_keys:
                        self._down_keys.remove(k)

    def cursor_position(self) -> tuple[int, int]:
        return self.d.position()

    def wait(self, seconds: float) -> None:
        self.kill.gate()
        self.kill.wait(max(0.0, min(float(seconds), 60.0)))
