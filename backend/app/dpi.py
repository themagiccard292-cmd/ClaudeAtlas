"""DPI awareness + primary-monitor geometry.

``enable_dpi_awareness()`` MUST run before pyautogui / mss / pywinauto are imported,
otherwise Windows hands those libraries *virtualised* (scaled) coordinates and every
click lands in the wrong place on a 125%/150% display.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass


def enable_dpi_awareness() -> str:
    """Make the process per-monitor DPI aware. Returns the mode that was applied."""
    if sys.platform != "win32":
        return "n/a"
    import ctypes

    try:
        # 2 = PROCESS_PER_MONITOR_DPI_AWARE
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "per-monitor"
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return "system"
    except Exception:
        return "unaware"


@dataclass(frozen=True)
class Geometry:
    """Primary monitor rectangle in *physical* pixels (origin of the primary is 0,0)."""

    left: int
    top: int
    width: int
    height: int


def primary_screen_size() -> tuple[int, int]:
    """Physical pixel size of the primary monitor (valid once DPI-aware)."""
    if sys.platform == "win32":
        import ctypes

        user32 = ctypes.windll.user32
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    raise RuntimeError("primary_screen_size() is Windows-only; inject a screen driver")


def pick_primary_monitor(monitors: list[dict], primary_size: tuple[int, int] | None = None) -> dict:
    """Choose the primary monitor from ``mss`` monitor dicts.

    ``mss.monitors[0]`` is the virtual union of all screens, so it is skipped. The primary
    monitor always sits at the virtual origin (0, 0); on multi-monitor rigs the first
    entry is *not* guaranteed to be the primary one, hence the explicit search.
    """
    real = monitors[1:] if len(monitors) > 1 else monitors
    at_origin = [m for m in real if m["left"] == 0 and m["top"] == 0]
    if primary_size:
        for m in at_origin:
            if (m["width"], m["height"]) == tuple(primary_size):
                return m
    if at_origin:
        return at_origin[0]
    return real[0]
