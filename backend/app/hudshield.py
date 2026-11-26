"""Keeps the always-on-top Mini-HUD from swallowing the agent's own clicks.

The HUD pill sits above the taskbar. If Claude's target pixel happens to lie under it, a
real click would hit the HUD's STOP button instead of the app underneath. Just before a
pointer action we flip the HUD window to ``WS_EX_TRANSPARENT`` (click-through) *only if*
the target is inside its rectangle, then restore the style. The HUD window is found by
its title, so no IPC with Electron is needed.
"""
from __future__ import annotations

import contextlib
import sys

HUD_TITLE = "Claude OS Agent HUD"
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20
WS_EX_LAYERED = 0x80000


class HudShield:
    def __init__(self, title: str = HUD_TITLE, margin: int = 10) -> None:
        self.title = title
        self.margin = margin

    @contextlib.contextmanager
    def around(self, x: int | None = None, y: int | None = None):
        if sys.platform != "win32" or x is None or y is None:
            yield
            return
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        user32.SetWindowLongW.restype = ctypes.c_long
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

        hwnd = user32.FindWindowW(None, self.title)
        if not hwnd or not user32.IsWindowVisible(hwnd):
            yield
            return
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        m = self.margin
        if not (rect.left - m <= x <= rect.right + m and rect.top - m <= y <= rect.bottom + m):
            yield
            return
        original = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, original | WS_EX_TRANSPARENT | WS_EX_LAYERED)
        try:
            yield
        finally:
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, original)
