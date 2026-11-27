"""Remember / restore the foreground window.

The HUD must be clickable, so clicking Approve/Deny/Stop activates it and moves keyboard focus off the app Claude
is controlling. After an approval answer we hand focus back to the window that had it before the prompt.
"""
from __future__ import annotations

import sys


def foreground() -> int | None:
    if sys.platform != "win32":
        return None
    import ctypes

    return int(ctypes.windll.user32.GetForegroundWindow() or 0) or None


def restore(hwnd: int | None) -> None:
    if sys.platform != "win32" or not hwnd:
        return
    import ctypes
    from ctypes import wintypes

    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    u.IsWindow.argtypes = [wintypes.HWND]
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    u.IsIconic.argtypes = [wintypes.HWND]
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    if not u.IsWindow(hwnd):
        return
    fg = u.GetForegroundWindow()
    if fg == hwnd:
        return
    cur = k.GetCurrentThreadId()
    fg_thread = u.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(fg_thread and fg_thread != cur and u.AttachThreadInput(cur, fg_thread, True))
    try:
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)  # SW_RESTORE
        if not u.SetForegroundWindow(hwnd):
            u.SwitchToThisWindow(hwnd, True)
    finally:
        if attached:
            u.AttachThreadInput(cur, fg_thread, False)
