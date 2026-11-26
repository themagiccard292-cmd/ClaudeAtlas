"""Windows UI Automation (pywinauto) so Claude can target *controls*, not just pixels.

``UiaService`` contains all the logic (tree walking, ids, formatting) against a tiny node
interface. ``PywinautoBackend`` adapts pywinauto to that interface on Windows; tests use a
fake backend. All COM work is pinned to ONE worker thread (COM objects are thread-affine).
"""
from __future__ import annotations

import asyncio
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Protocol

from .input_control import ToolError

INTERESTING = {
    "Button", "SplitButton", "CheckBox", "RadioButton", "ComboBox", "Edit", "Document", "Hyperlink", "MenuItem", "Menu",
    "MenuBar", "ListItem", "List", "TabItem", "Tab", "TreeItem", "DataItem", "Slider", "Spinner", "Text", "ToolBar",
    "Window", "Pane", "Group", "Image", "ScrollBar", "Table", "TitleBar", "StatusBar", "Custom",
}
ALWAYS_SHOW = {"Button", "SplitButton", "CheckBox", "RadioButton", "ComboBox", "Edit", "Document", "Hyperlink", "MenuItem", "ListItem", "TabItem", "TreeItem", "DataItem", "Slider", "Spinner"}


class UiaNode(Protocol):
    name: str
    control_type: str
    automation_id: str
    class_name: str
    rect: tuple[int, int, int, int]
    enabled: bool

    def children(self) -> list["UiaNode"]: ...
    def value(self) -> str | None: ...
    def invoke(self) -> None: ...
    def set_text(self, text: str) -> None: ...
    def focus(self) -> None: ...


class UiaBackend(Protocol):
    def root(self, scope: str) -> UiaNode: ...
    def top_windows(self) -> list[UiaNode]: ...
    def element_at(self, x: int, y: int) -> UiaNode | None: ...


class UiaService:
    def __init__(self, backend: UiaBackend | None, to_model: Callable[[float, float], tuple[int, int]] = lambda x, y: (round(x), round(y))):
        self.backend = backend
        self.to_model = to_model
        self._elements: dict[int, UiaNode] = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="uia")

    @property
    def available(self) -> bool:
        return self.backend is not None

    async def run(self, fn, *args, timeout: float = 20.0):
        """Run a UIA call on the dedicated COM thread."""
        if not self.backend:
            raise ToolError("Windows UI Automation is not available on this machine; use screenshot coordinates instead.")
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(loop.run_in_executor(self._pool, lambda: fn(*args)), timeout=timeout)
        except asyncio.TimeoutError:
            raise ToolError("UI Automation timed out (the target app may be busy or hung)") from None

    # ---- snapshot ------------------------------------------------------------------
    def snapshot(self, scope: str = "foreground", max_elements: int = 200, max_depth: int = 9) -> str:
        if scope == "windows":
            return self.list_windows()
        root = self.backend.root("foreground")
        self._elements = {}
        lines: list[str] = []
        l, t, r, b = root.rect
        ml, mt = self.to_model(l, t)
        mr, mb = self.to_model(r, b)
        lines.append(f'Foreground window: "{root.name}" [{root.class_name}] at ({ml},{mt})-({mr},{mb})')
        lines.append("Coordinates are in screenshot space. Use ui_click / ui_set_text with the [id].")
        counter = [0]

        def visit(node: UiaNode, depth: int) -> None:
            if counter[0] >= max_elements or depth > max_depth:
                return
            try:
                kids = node.children()
            except Exception:
                return
            for ch in kids:
                if counter[0] >= max_elements:
                    return
                try:
                    ct = ch.control_type
                    x1, y1, x2, y2 = ch.rect
                    if x2 - x1 <= 0 or y2 - y1 <= 0:
                        continue
                    name = (ch.name or "").strip()
                    show = ct in ALWAYS_SHOW or (ct in INTERESTING and (name or ch.automation_id) and ct not in {"Pane", "Group", "Custom", "Window"})
                    if show:
                        counter[0] += 1
                        eid = counter[0]
                        self._elements[eid] = ch
                        mx1, my1 = self.to_model(x1, y1)
                        mx2, my2 = self.to_model(x2, y2)
                        extra = []
                        if ch.automation_id:
                            extra.append(f"auto_id={ch.automation_id}")
                        if not ch.enabled:
                            extra.append("disabled")
                        if ct in ("Edit", "Document", "ComboBox", "CheckBox", "Slider", "Spinner"):
                            try:
                                v = ch.value()
                            except Exception:
                                v = None
                            if v:
                                extra.append("value=" + repr(v[:80]))
                        lines.append(f'{"  " * min(depth, 4)}[{eid}] {ct} "{name[:70]}" ({mx1},{my1})-({mx2},{my2}) ' + " ".join(extra))
                except Exception:
                    continue
                visit(ch, depth + 1)

        visit(root, 0)
        if counter[0] == 0:
            lines.append("(no accessible controls found - this app may not expose UI Automation; rely on the screenshot)")
        elif counter[0] >= max_elements:
            lines.append(f"... truncated at {max_elements} elements; call again after navigating, or use screenshot")
        return "\n".join(lines)

    def list_windows(self) -> str:
        out = []
        for w in self.backend.top_windows():
            try:
                if not (w.name or "").strip():
                    continue
                l, t, r, b = w.rect
                if r - l <= 0 or b - t <= 0:
                    continue
                ml, mt = self.to_model(l, t)
                mr, mb = self.to_model(r, b)
                out.append(f'"{w.name[:80]}" [{w.class_name}] ({ml},{mt})-({mr},{mb})')
            except Exception:
                continue
        return "Open top-level windows:\n" + ("\n".join(out) if out else "(none)")

    # ---- element actions -------------------------------------------------------------
    def element(self, element_id: int) -> UiaNode:
        node = self._elements.get(int(element_id))
        if node is None:
            raise ToolError(f"unknown element id {element_id}; call ui_snapshot again (ids are only valid for the latest snapshot)")
        return node

    def center(self, element_id: int) -> tuple[int, int, str]:
        n = self.element(element_id)
        l, t, r, b = n.rect
        return (l + r) // 2, (t + b) // 2, n.name

    def invoke(self, element_id: int) -> str:
        n = self.element(element_id)
        n.invoke()
        return n.name

    def set_text(self, element_id: int, text: str) -> str:
        n = self.element(element_id)
        if n.control_type not in ("Edit", "Document", "ComboBox", "Spinner", "Slider"):
            raise ToolError(f"element {element_id} is a {n.control_type}, not a text field")
        n.set_text(text)
        return n.name

    def read(self, element_id: int) -> str:
        n = self.element(element_id)
        v = None
        try:
            v = n.value()
        except Exception:
            pass
        return f'{n.control_type} "{n.name}"' + (f"\nvalue: {v}" if v else "\n(no text value exposed)")

    def focus_window(self, title: str) -> str:
        needle = title.strip().lower()
        if not needle:
            raise ToolError("title is required")
        matches = [w for w in self.backend.top_windows() if needle in (w.name or "").lower()]
        if not matches:
            raise ToolError(f'no open window title contains "{title}"; use ui_snapshot(scope="windows") to list them')
        w = matches[0]
        w.focus()
        return w.name

    def label_at(self, x: int, y: int) -> str | None:
        n = self.backend.element_at(x, y)
        if n is None:
            return None
        name = (n.name or "").strip()
        if name:
            return name
        return None


# ----------------------------------------------------------------------------------
# pywinauto adapter (Windows only)
# ----------------------------------------------------------------------------------
class _PwNode:
    def __init__(self, info):
        self.info = info

    @property
    def name(self) -> str:
        return self.info.name or ""

    @property
    def control_type(self) -> str:
        return self.info.control_type or ""

    @property
    def automation_id(self) -> str:
        return self.info.automation_id or ""

    @property
    def class_name(self) -> str:
        return self.info.class_name or ""

    @property
    def rect(self) -> tuple[int, int, int, int]:
        r = self.info.rectangle
        return int(r.left), int(r.top), int(r.right), int(r.bottom)

    @property
    def enabled(self) -> bool:
        try:
            return bool(self.info.enabled)
        except Exception:
            return True

    def children(self) -> list["_PwNode"]:
        return [_PwNode(c) for c in self.info.children()]

    def _wrapper(self):
        from pywinauto.controls.uiawrapper import UIAWrapper

        return UIAWrapper(self.info)

    def value(self) -> str | None:
        w = self._wrapper()
        for getter in (lambda: w.iface_value.CurrentValue, lambda: w.get_value(), lambda: w.window_text()):
            try:
                v = getter()
                if v:
                    return str(v)
            except Exception:
                continue
        return None

    def invoke(self) -> None:
        w = self._wrapper()
        try:
            w.invoke()
        except Exception:
            w.click_input()

    def set_text(self, text: str) -> None:
        w = self._wrapper()
        try:
            w.iface_value.SetValue(text)
        except Exception:
            w.set_edit_text(text)

    def focus(self) -> None:
        w = self._wrapper()
        try:
            if w.is_minimized():
                w.restore()
        except Exception:
            pass
        w.set_focus()


class PywinautoBackend:
    """pywinauto is imported lazily *inside the UIA worker thread* (first call): comtypes
    initialises COM on the importing thread, and COM objects must stay on that thread."""

    def root(self, scope: str) -> _PwNode:
        import ctypes

        from pywinauto.uia_element_info import UIAElementInfo

        if scope == "desktop":
            return _PwNode(UIAElementInfo())
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            raise ToolError("no foreground window")
        return _PwNode(UIAElementInfo(hwnd))

    def top_windows(self) -> list[_PwNode]:
        from pywinauto.uia_element_info import UIAElementInfo

        return [_PwNode(c) for c in UIAElementInfo().children()]

    def element_at(self, x: int, y: int) -> _PwNode | None:
        try:
            import ctypes
            from ctypes import wintypes

            from pywinauto.uia_defines import IUIA
            from pywinauto.uia_element_info import UIAElementInfo

            elem = IUIA().iuia.ElementFromPoint(wintypes.POINT(int(x), int(y)))
            return _PwNode(UIAElementInfo(elem))
        except Exception:
            return None


def make_default_backend() -> UiaBackend | None:
    if sys.platform != "win32":
        return None
    import importlib.util

    # find_spec: do NOT import pywinauto here (main thread) - see PywinautoBackend docstring.
    if importlib.util.find_spec("pywinauto") is None:
        return None
    return PywinautoBackend()
