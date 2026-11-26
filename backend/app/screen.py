"""Primary-monitor screen capture, scaling and coordinate mapping.

Claude sees a *downscaled* screenshot; every coordinate it sends back is in that image's
pixel space. ``scale`` converts model space -> physical pixels (pyautogui space, which
equals physical pixels because the process is per-monitor DPI aware).

The image is kept deliberately small (<= ~1.15 MP, long edge <= 1280 by default): Claude
Code may rescale larger MCP images before the model sees them, which would silently shift
every coordinate. Staying under that threshold keeps the mapping exact.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Callable, Protocol

from PIL import Image, ImageDraw

MAX_PIXELS = 1_150_000
THUMB_EDGE = 760


def compute_scale(width: int, height: int, max_edge: int, max_pixels: int = MAX_PIXELS) -> float:
    """Largest scale <= 1 keeping the long edge <= max_edge and area <= max_pixels."""
    long_edge = max(width, height)
    s = min(1.0, max_edge / long_edge, math.sqrt(max_pixels / (width * height)))
    return max(s, 0.05)


def scaled_size(width: int, height: int, scale: float) -> tuple[int, int]:
    return max(1, round(width * scale)), max(1, round(height * scale))


class ScreenDriver(Protocol):
    def grab(self) -> tuple[Image.Image, int, int]:
        """Return (RGB image of the primary monitor, left, top)."""

    def cursor(self) -> tuple[int, int] | None:
        """Physical cursor position, if known."""


class MssScreenDriver:
    """Fast primary-monitor grab via mss (a fresh handle per call: mss objects are per-thread)."""

    def grab(self) -> tuple[Image.Image, int, int]:
        import mss

        from .dpi import pick_primary_monitor, primary_screen_size

        try:
            psize = primary_screen_size()
        except Exception:
            psize = None
        with mss.mss() as sct:
            mon = pick_primary_monitor(sct.monitors, psize)
            raw = sct.grab(mon)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            return img, int(mon["left"]), int(mon["top"])

    def cursor(self) -> tuple[int, int] | None:
        try:
            import ctypes
            from ctypes import wintypes

            pt = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            return int(pt.x), int(pt.y)
        except Exception:
            return None


@dataclass
class Shot:
    jpeg: bytes  # what Claude sees
    width: int
    height: int
    scale: float  # model px -> physical px is x / scale
    phys_width: int
    phys_height: int
    thumb: bytes  # small JPEG for the chat UI


def _jpeg(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _draw_cursor(img: Image.Image, x: float, y: float, size: int) -> None:
    d = ImageDraw.Draw(img)
    pts = [(x, y), (x, y + size), (x + size * 0.28, y + size * 0.78), (x + size * 0.5, y + size * 1.15), (x + size * 0.66, y + size * 1.07), (x + size * 0.44, y + size * 0.7), (x + size * 0.8, y + size * 0.7)]
    d.polygon(pts, fill=(255, 255, 255), outline=(0, 0, 0))


class ScreenCapturer:
    def __init__(self, driver: ScreenDriver, max_edge: Callable[[], int], quality: Callable[[], int], draw_cursor: Callable[[], bool] = lambda: True):
        self.driver = driver
        self._max_edge, self._quality, self._draw_cursor = max_edge, quality, draw_cursor
        self.last: Shot | None = None
        self.origin = (0, 0)

    # -- geometry -------------------------------------------------------------------
    def scale_for(self, phys_w: int, phys_h: int) -> float:
        return compute_scale(phys_w, phys_h, self._max_edge())

    def to_physical(self, x: float, y: float, phys_size: tuple[int, int]) -> tuple[int, int]:
        """Map model-space (screenshot) coordinates to physical primary-monitor pixels."""
        pw, ph = phys_size
        s = self.scale_for(pw, ph)
        px = min(max(round(x / s), 0), pw - 1)
        py = min(max(round(y / s), 0), ph - 1)
        return px, py

    def to_model(self, px: float, py: float, phys_size: tuple[int, int]) -> tuple[int, int]:
        s = self.scale_for(*phys_size)
        return round(px * s), round(py * s)

    # -- capture --------------------------------------------------------------------
    def capture(self) -> Shot:
        img, left, top = self.driver.grab()
        self.origin = (left, top)
        pw, ph = img.size
        s = self.scale_for(pw, ph)
        w, h = scaled_size(pw, ph, s)
        small = img.resize((w, h), Image.LANCZOS) if (w, h) != (pw, ph) else img.copy()
        if self._draw_cursor():
            cur = self.driver.cursor()
            if cur:
                cx, cy = (cur[0] - left) * s, (cur[1] - top) * s
                if 0 <= cx < w and 0 <= cy < h:
                    _draw_cursor(small, cx, cy, max(14, round(w / 60)))
        shot = Shot(
            jpeg=_jpeg(small, self._quality()),
            width=w,
            height=h,
            scale=s,
            phys_width=pw,
            phys_height=ph,
            thumb=self._thumb(small),
        )
        self.last = shot
        return shot

    @staticmethod
    def _thumb(img: Image.Image) -> bytes:
        w, h = img.size
        k = min(1.0, THUMB_EDGE / max(w, h))
        t = img.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS) if k < 1 else img
        return _jpeg(t, 62)

    def zoom(self, x1: float, y1: float, x2: float, y2: float) -> Shot:
        """Crop a region (given in screenshot coordinates) at native resolution, for reading small text."""
        img, left, top = self.driver.grab()
        pw, ph = img.size
        s = self.scale_for(pw, ph)
        a = self.to_physical(min(x1, x2), min(y1, y2), (pw, ph))
        b = self.to_physical(max(x1, x2), max(y1, y2), (pw, ph))
        if b[0] - a[0] < 8 or b[1] - a[1] < 8:
            raise ValueError("zoom region is too small")
        crop = img.crop((a[0], a[1], b[0], b[1]))
        cw, ch = crop.size
        k = min(self._max_edge() / max(cw, ch), math.sqrt(MAX_PIXELS / (cw * ch)))
        if max(cw, ch) < 480:  # tiny regions: upscale for legibility
            k = min(k, 2.0) if k >= 1 else k
            k = max(k, 1.0)
        if abs(k - 1.0) > 0.01:
            crop = crop.resize((max(1, round(cw * k)), max(1, round(ch * k))), Image.LANCZOS)
        return Shot(jpeg=_jpeg(crop, self._quality()), width=crop.size[0], height=crop.size[1], scale=s, phys_width=pw, phys_height=ph, thumb=self._thumb(crop))
