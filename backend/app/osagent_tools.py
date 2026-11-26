"""The OS-control toolbox exposed to Claude Code over MCP.

Perception -> action -> verify: every state-changing tool returns a fresh screenshot (unless
the user disabled ``auto_screenshot``) so Claude can verify the effect in the same round trip.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import webbrowser
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol
from urllib.parse import urlparse

from .config import Settings
from .input_control import InputController, ToolError
from .safety import ApprovalManager, Halted, KillSwitch, Risk, classify_powershell, classify_ui_label
from .screen import ScreenCapturer, Shot
from .shell import build_argv, run_command
from .uia import UiaService

log = logging.getLogger("tools")
SERVER_NAME = "osagent"


@dataclass
class ToolOutput:
    text: str
    shot: Shot | None = None  # image returned to Claude
    is_error: bool = False
    coord: tuple[int, int] | None = None  # model-space point, for the UI marker
    detail: str = ""  # extra text for the UI (e.g. shell output)


class ActionSink(Protocol):
    def begin(self, tool: str, args: dict, summary: str, kind: str) -> str: ...
    def finish(self, action_id: str, ok: bool, output: str, shot: Shot | None, coord: tuple[int, int] | None, detail: str) -> None: ...


def _num(args: dict, key: str, default: float | None = None) -> float:
    v = args.get(key, default)
    if v is None:
        raise ToolError(f"'{key}' is required")
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ToolError(f"'{key}' must be a number") from None


def _clip(s: str, n: int = 60) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


NUM = {"type": "number"}
SHOT_FLAG = {"type": "boolean", "description": "Return a screenshot after the action (default true)."}


def _schema(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


TOOL_SPECS: list[dict] = [
    {"name": "screenshot", "description": "Capture the primary monitor. Returns an image; ALL x,y coordinates you pass to other tools are pixel positions in this image (top-left is 0,0). The mouse pointer is drawn as a white arrow.", "inputSchema": _schema({})},
    {"name": "zoom", "description": "Zoom into a rectangle (screenshot coordinates) at native resolution to read small text. Coordinates for other tools still refer to the full screenshot.", "inputSchema": _schema({"x1": NUM, "y1": NUM, "x2": NUM, "y2": NUM}, ["x1", "y1", "x2", "y2"])},
    {"name": "click", "description": "Move the mouse to (x, y) and click. Use clicks=2 for double-click, clicks=3 for triple-click (select line/paragraph), button=right for a context menu. Optional modifiers such as 'ctrl' or 'shift+ctrl' are held during the click.", "inputSchema": _schema({"x": NUM, "y": NUM, "button": {"type": "string", "enum": ["left", "right", "middle"]}, "clicks": {"type": "integer", "enum": [1, 2, 3]}, "modifiers": {"type": "string"}, "screenshot": SHOT_FLAG}, ["x", "y"])},
    {"name": "drag", "description": "Press the left button at (from_x, from_y), drag to (to_x, to_y) and release (drag-and-drop, selecting text, resizing).", "inputSchema": _schema({"from_x": NUM, "from_y": NUM, "to_x": NUM, "to_y": NUM, "modifiers": {"type": "string"}, "screenshot": SHOT_FLAG}, ["from_x", "from_y", "to_x", "to_y"])},
    {"name": "move_mouse", "description": "Move the pointer to (x, y) without clicking (hover menus/tooltips).", "inputSchema": _schema({"x": NUM, "y": NUM, "screenshot": SHOT_FLAG}, ["x", "y"])},
    {"name": "scroll", "description": "Scroll the mouse wheel. If x,y are given the pointer moves there first. amount = wheel notches (default 3).", "inputSchema": _schema({"direction": {"type": "string", "enum": ["up", "down", "left", "right"]}, "amount": {"type": "integer"}, "x": NUM, "y": NUM, "screenshot": SHOT_FLAG}, ["direction"])},
    {"name": "type_text", "description": "Type text into the focused control (full Unicode; newline presses Enter). Click the field first.", "inputSchema": _schema({"text": {"type": "string"}, "screenshot": SHOT_FLAG}, ["text"])},
    {"name": "press_keys", "description": "Press a key or chord, e.g. 'enter', 'ctrl+c', 'alt+tab', 'win+r', 'ctrl+shift+esc'... Use repeat to press it several times. (Win+L is blocked.)", "inputSchema": _schema({"keys": {"type": "string"}, "repeat": {"type": "integer"}, "screenshot": SHOT_FLAG}, ["keys"])},
    {"name": "wait", "description": "Wait for something to load (seconds, max 60), then return a screenshot.", "inputSchema": _schema({"seconds": NUM, "screenshot": SHOT_FLAG}, ["seconds"])},
    {"name": "cursor_position", "description": "Return the mouse pointer position in screenshot coordinates.", "inputSchema": _schema({})},
    {"name": "batch", "description": "Run several actions in order with ONE screenshot at the end (faster and cheaper than separate calls). Stops at the first failure. Each item is an object with 'action' (click|drag|move_mouse|scroll|type_text|press_keys|wait) plus that tool's arguments.", "inputSchema": _schema({"actions": {"type": "array", "items": {"type": "object"}}}, ["actions"])},
    {"name": "ui_snapshot", "description": "Windows UI Automation tree of the foreground window: controls with [id], role, name, value and bounds. Prefer this over guessing pixels for buttons/fields; ids are valid until the next snapshot. scope='windows' lists all open top-level windows instead.", "inputSchema": _schema({"scope": {"type": "string", "enum": ["foreground", "windows"]}, "max_elements": {"type": "integer"}})},
    {"name": "ui_click", "description": "Click a control from the latest ui_snapshot by its [id]. method='invoke' triggers it without moving the mouse (works even if partly covered).", "inputSchema": _schema({"element_id": {"type": "integer"}, "method": {"type": "string", "enum": ["click", "invoke"]}, "screenshot": SHOT_FLAG}, ["element_id"])},
    {"name": "ui_set_text", "description": "Set the text of an edit/combo control from the latest ui_snapshot (more reliable than typing).", "inputSchema": _schema({"element_id": {"type": "integer"}, "text": {"type": "string"}, "screenshot": SHOT_FLAG}, ["element_id", "text"])},
    {"name": "ui_read", "description": "Read the text/value of a control from the latest ui_snapshot.", "inputSchema": _schema({"element_id": {"type": "integer"}}, ["element_id"])},
    {"name": "focus_window", "description": "Bring a window to the foreground by (part of) its title, restoring it if minimized.", "inputSchema": _schema({"title": {"type": "string"}, "screenshot": SHOT_FLAG}, ["title"])},
    {"name": "powershell", "description": "Run a Windows PowerShell command/script (search files, open apps with Start-Process, manage folders, read system info, run scripts). Returns combined stdout/stderr. Risky commands (deleting, admin, system changes) require the user's approval and may be denied.", "inputSchema": _schema({"command": {"type": "string"}, "timeout_seconds": {"type": "integer"}, "working_directory": {"type": "string"}}, ["command"])},
    {"name": "open_url", "description": "Open an http(s) URL in the user's default browser, then return a screenshot. Use the browser UI (click/type) to search, fill forms and read pages.", "inputSchema": _schema({"url": {"type": "string"}, "screenshot": SHOT_FLAG}, ["url"])},
    {"name": "request_approval", "description": "Ask the user to approve an action BEFORE doing anything destructive or irreversible: deleting data, sending email/messages, purchases or payments, changing system settings, running as administrator. Returns APPROVED or DENIED. Never proceed if DENIED.", "inputSchema": _schema({"action": {"type": "string", "description": "What you are about to do, in one sentence."}, "category": {"type": "string", "enum": ["delete", "send", "purchase", "admin", "system", "other"]}, "details": {"type": "string"}}, ["action"])},
]

MUTATING = {"click", "drag", "move_mouse", "scroll", "type_text", "press_keys", "wait", "ui_click", "ui_set_text", "focus_window", "open_url", "batch"}
BATCH_OPS = ("click", "drag", "move_mouse", "scroll", "type_text", "press_keys", "wait")


def allowed_tool_names() -> list[str]:
    return [f"mcp__{SERVER_NAME}__{t['name']}" for t in TOOL_SPECS]


class OsTools:
    def __init__(
        self,
        controller: InputController,
        screen: ScreenCapturer,
        uia: UiaService,
        kill: KillSwitch,
        approvals: ApprovalManager,
        settings: Callable[[], Settings],
        sink: ActionSink,
        cwd: str | None = None,
        open_url_fn: Callable[[str], None] | None = None,
    ) -> None:
        self.ctl, self.screen, self.uia, self.kill, self.approvals = controller, screen, uia, kill, approvals
        self.settings, self.sink = settings, sink
        self.cwd = cwd
        self._open_url = open_url_fn or _default_open_url
        self.active = False
        self.count = 0
        self.over_budget_calls = 0
        self.on_over_budget: Callable[[], None] | None = None
        self._lock = asyncio.Lock()  # one OS action at a time, even if Claude issues parallel calls

    # ---- run lifecycle -------------------------------------------------------------
    def begin_run(self) -> None:
        self.active, self.count, self.over_budget_calls = True, 0, 0

    def end_run(self) -> None:
        self.active = False

    # ---- dispatch ------------------------------------------------------------------
    async def call(self, name: str, args: dict | None) -> ToolOutput:
        args = args or {}
        if name not in {t["name"] for t in TOOL_SPECS}:
            return ToolOutput(f"Unknown tool '{name}'.", is_error=True)
        if not self.active:
            return ToolOutput("No agent run is active; the OS bridge only accepts calls during a run started from the Claude OS Agent app.", is_error=True)
        self.count += 1
        cfg = self.settings()
        if self.count > cfg.max_actions:
            self.over_budget_calls += 1
            if self.over_budget_calls == 3 and self.on_over_budget:
                self.on_over_budget()
            return ToolOutput(f"Action budget of {cfg.max_actions} reached. STOP now and tell the user what you accomplished and what remains.", is_error=True)

        summary, kind = self._describe(name, args)
        action_id = self.sink.begin(name, args, summary, kind)
        out: ToolOutput
        async with self._lock:
            try:
                await self.kill.agate()
                out = await getattr(self, f"_t_{name}")(args)
            except Halted as e:
                out = ToolOutput(f"STOPPED by the user ({e}). Do not continue the task; briefly confirm you have stopped.", is_error=True)
            except ToolError as e:
                out = ToolOutput(f"Error: {e}", is_error=True)
            except Exception as e:  # pragma: no cover - defensive: never crash the MCP session
                log.exception("tool %s failed", name)
                out = ToolOutput(f"Error: {type(e).__name__}: {e}", is_error=True)
        self.sink.finish(action_id, not out.is_error, out.text, out.shot, out.coord, out.detail)
        return out

    # ---- helpers -------------------------------------------------------------------
    def _describe(self, name: str, a: dict) -> tuple[str, str]:
        try:
            if name == "click":
                n = {2: "Double-click", 3: "Triple-click"}.get(int(a.get("clicks", 1) or 1), "Right-click" if a.get("button") == "right" else "Click")
                return f"{n} ({round(float(a['x']))}, {round(float(a['y']))})", "mouse"
            if name == "drag":
                return f"Drag ({round(float(a['from_x']))}, {round(float(a['from_y']))}) → ({round(float(a['to_x']))}, {round(float(a['to_y']))})", "mouse"
            if name == "move_mouse":
                return f"Move mouse to ({round(float(a['x']))}, {round(float(a['y']))})", "mouse"
            if name == "scroll":
                return f"Scroll {a.get('direction', '?')} ×{a.get('amount', 3)}", "mouse"
            if name == "type_text":
                return f'Type "{_clip(a.get("text", ""), 48)}"', "keyboard"
            if name == "press_keys":
                return f"Press {a.get('keys', '?')}", "keyboard"
            if name == "powershell":
                return f"PowerShell: {_clip(a.get('command', ''), 70)}", "terminal"
            if name == "screenshot":
                return "Take screenshot", "screen"
            if name == "zoom":
                return "Zoom into region", "screen"
            if name == "wait":
                return f"Wait {a.get('seconds', '?')}s", "screen"
            if name == "ui_snapshot":
                return "Inspect UI controls" + (" (all windows)" if a.get("scope") == "windows" else ""), "uia"
            if name == "ui_click":
                return f"Click control [{a.get('element_id')}]", "uia"
            if name == "ui_set_text":
                return f'Set control [{a.get("element_id")}] text', "uia"
            if name == "ui_read":
                return f"Read control [{a.get('element_id')}]", "uia"
            if name == "focus_window":
                return f'Focus window "{_clip(a.get("title", ""), 40)}"', "uia"
            if name == "open_url":
                return f"Open {_clip(a.get('url', ''), 60)}", "web"
            if name == "request_approval":
                return f"Ask permission: {_clip(a.get('action', ''), 60)}", "approval"
            if name == "batch":
                return f"Batch of {len(a.get('actions') or [])} actions", "mouse"
            if name == "cursor_position":
                return "Read cursor position", "mouse"
        except (KeyError, TypeError, ValueError):
            pass
        return name, "other"

    def _phys(self) -> tuple[int, int]:
        return self.ctl.d.size()

    def _to_phys(self, x: float, y: float) -> tuple[int, int]:
        return self.screen.to_physical(x, y, self._phys())

    async def _shot(self, delay: bool = True) -> Shot:
        settle = self.settings().settle_ms / 1000 if delay else 0

        def work() -> Shot:
            if settle:
                self.kill.wait(settle)
            self.kill.gate()
            return self.screen.capture()

        return await asyncio.to_thread(work)

    async def _result(self, args: dict, text: str, coord: tuple[int, int] | None = None, force_shot: bool = False) -> ToolOutput:
        want = args.get("screenshot", True) and self.settings().auto_screenshot
        if want or force_shot:
            shot = await self._shot()
            return ToolOutput(f"{text}\nScreenshot {shot.width}x{shot.height} after the action (coordinates refer to this image).", shot=shot, coord=coord)
        return ToolOutput(text, coord=coord)

    async def _approve(self, risk: Risk, kind: str, summary: str, details: str, ask_in_all_mode: bool = True) -> None:
        mode = self.approvals.mode
        if mode == "never" or (not risk.needs_approval and not (mode == "all" and ask_in_all_mode)):
            return
        reasons = "; ".join(risk.reasons) if risk.reasons else "approval mode is set to ask for every action"
        decision = await self.approvals.request(kind, summary, f"{details}\n\nWhy: {reasons}".strip(), risk=risk.level if risk.needs_approval else "medium")
        if not decision.approved:
            raise ToolError(f"The user DENIED this action ({decision.reason}). Do not perform it or work around the denial; ask the user how to proceed.")

    async def _gui_guard(self, px: int, py: int, summary: str) -> None:
        """Smart approval for clicks: look at the control under the pointer (UIA) for send/buy/delete..."""
        if self.approvals.mode == "never":
            return
        label = None
        if self.uia.available and self.approvals.mode in ("smart", "all"):
            try:
                label = await self.uia.run(self.uia.label_at, px, py, timeout=1.5)
            except Exception:
                label = None
        await self._approve(classify_ui_label(label), "gui", summary + (f' on "{label}"' if label else ""), f"Control under the pointer: {label or 'unknown'}")

    # ---- primitive ops (shared by tools and batch; they return a status line) ----------
    async def _op_click(self, a: dict) -> tuple[str, tuple[int, int]]:
        x, y = _num(a, "x"), _num(a, "y")
        px, py = self._to_phys(x, y)
        clicks = int(a.get("clicks", 1) or 1)
        await self._gui_guard(px, py, self._describe("click", a)[0])
        await asyncio.to_thread(self.ctl.click, px, py, a.get("button", "left"), clicks, a.get("modifiers"))
        return f"Clicked at ({round(x)}, {round(y)}).", (round(x), round(y))

    async def _op_drag(self, a: dict) -> tuple[str, tuple[int, int] | None]:
        x1, y1, x2, y2 = (_num(a, k) for k in ("from_x", "from_y", "to_x", "to_y"))
        p1, p2 = self._to_phys(x1, y1), self._to_phys(x2, y2)
        await self._gui_guard(p1[0], p1[1], self._describe("drag", a)[0])
        await asyncio.to_thread(self.ctl.drag, p1[0], p1[1], p2[0], p2[1], a.get("modifiers"))
        return f"Dragged from ({round(x1)}, {round(y1)}) to ({round(x2)}, {round(y2)}).", (round(x2), round(y2))

    async def _op_move_mouse(self, a: dict) -> tuple[str, tuple[int, int] | None]:
        x, y = _num(a, "x"), _num(a, "y")
        px, py = self._to_phys(x, y)
        await asyncio.to_thread(self.ctl.move, px, py)
        return f"Moved the mouse to ({round(x)}, {round(y)}).", (round(x), round(y))

    async def _op_scroll(self, a: dict) -> tuple[str, tuple[int, int] | None]:
        direction = a.get("direction")
        if direction not in ("up", "down", "left", "right"):
            raise ToolError("direction must be up, down, left or right")
        px = py = None
        coord = None
        if a.get("x") is not None and a.get("y") is not None:
            x, y = _num(a, "x"), _num(a, "y")
            px, py = self._to_phys(x, y)
            coord = (round(x), round(y))
        amount = int(a.get("amount", 3) or 3)
        await asyncio.to_thread(self.ctl.scroll, px, py, direction, amount)
        return f"Scrolled {direction} by {amount}.", coord

    async def _op_type_text(self, a: dict) -> tuple[str, None]:
        text = a.get("text")
        if not isinstance(text, str) or text == "":
            raise ToolError("'text' must be a non-empty string")
        if len(text) > 20_000:
            raise ToolError("text too long (max 20,000 characters); split it up or write it with PowerShell")
        await self._approve(Risk(), "gui", f'Type "{_clip(text, 60)}"', text[:500], ask_in_all_mode=True)
        await asyncio.to_thread(self.ctl.type_text, text)
        return f"Typed {len(text)} characters.", None

    async def _op_press_keys(self, a: dict) -> tuple[str, None]:
        keys = a.get("keys")
        if not isinstance(keys, str):
            raise ToolError("'keys' must be a string like 'ctrl+s'")
        await asyncio.to_thread(self.ctl.press, keys, int(a.get("repeat", 1) or 1))
        return f"Pressed {keys}.", None

    async def _op_wait(self, a: dict) -> tuple[str, None]:
        s = _num(a, "seconds")
        await asyncio.to_thread(self.ctl.wait, s)
        return f"Waited {s:g}s.", None

    # ---- tools ---------------------------------------------------------------------
    async def _t_screenshot(self, a: dict) -> ToolOutput:
        shot = await self._shot(delay=False)
        return ToolOutput(f"Screenshot {shot.width}x{shot.height}. Coordinates you send refer to this image.", shot=shot)

    async def _t_zoom(self, a: dict) -> ToolOutput:
        x1, y1, x2, y2 = (_num(a, k) for k in ("x1", "y1", "x2", "y2"))
        try:
            shot = await asyncio.to_thread(self.screen.zoom, x1, y1, x2, y2)
        except ValueError as e:
            raise ToolError(str(e)) from None
        return ToolOutput(f"Zoomed region ({round(x1)},{round(y1)})-({round(x2)},{round(y2)}). Do NOT use this image's pixels as coordinates; use the full screenshot.", shot=shot)

    async def _t_click(self, a: dict) -> ToolOutput:
        msg, coord = await self._op_click(a)
        return await self._result(a, msg, coord)

    async def _t_drag(self, a: dict) -> ToolOutput:
        msg, coord = await self._op_drag(a)
        return await self._result(a, msg, coord)

    async def _t_move_mouse(self, a: dict) -> ToolOutput:
        msg, coord = await self._op_move_mouse(a)
        return await self._result(a, msg, coord)

    async def _t_scroll(self, a: dict) -> ToolOutput:
        msg, coord = await self._op_scroll(a)
        return await self._result(a, msg, coord)

    async def _t_type_text(self, a: dict) -> ToolOutput:
        msg, _ = await self._op_type_text(a)
        return await self._result(a, msg)

    async def _t_press_keys(self, a: dict) -> ToolOutput:
        msg, _ = await self._op_press_keys(a)
        return await self._result(a, msg)

    async def _t_wait(self, a: dict) -> ToolOutput:
        msg, _ = await self._op_wait(a)
        return await self._result(a, msg, force_shot=True)

    async def _t_cursor_position(self, a: dict) -> ToolOutput:
        px, py = await asyncio.to_thread(self.ctl.cursor_position)
        mx, my = self.screen.to_model(px, py, self._phys())
        return ToolOutput(f"Cursor at ({mx}, {my}) in screenshot coordinates.", coord=(mx, my))

    async def _t_batch(self, a: dict) -> ToolOutput:
        items = a.get("actions")
        if not isinstance(items, list) or not items:
            raise ToolError("'actions' must be a non-empty list")
        if len(items) > 25:
            raise ToolError("a batch may contain at most 25 actions")
        log_lines: list[str] = []
        coord = None
        failed = False
        for i, item in enumerate(items, 1):
            op = item.get("action") if isinstance(item, dict) else None
            if op not in BATCH_OPS:
                log_lines.append(f"{i}. NOT RUN: unsupported action {op!r} (allowed: {', '.join(BATCH_OPS)})")
                failed = True
                break
            try:
                await self.kill.agate()
                msg, c = await getattr(self, f"_op_{op}")(item)
                coord = c or coord
                log_lines.append(f"{i}. {msg}")
            except ToolError as e:
                log_lines.append(f"{i}. FAILED: {e}")
                failed = True
                break
        if failed and i < len(items):
            log_lines.append(f"Remaining {len(items) - i} action(s) were NOT executed.")
        out = await self._result({"screenshot": True}, "\n".join(log_lines), coord)
        out.is_error = failed
        return out

    async def _t_ui_snapshot(self, a: dict) -> ToolOutput:
        scope = a.get("scope", "foreground")
        text = await self.uia.run(self.uia.snapshot, scope, int(a.get("max_elements", 200) or 200))
        return ToolOutput(text, detail=text[:3000])

    async def _t_ui_click(self, a: dict) -> ToolOutput:
        eid = int(_num(a, "element_id"))
        if a.get("method") == "invoke":
            node = await self.uia.run(self.uia.element, eid)
            await self._approve(classify_ui_label(node.name), "gui", f'Invoke control "{node.name}"', node.name)
            name = await self.uia.run(self.uia.invoke, eid)
            return await self._result(a, f'Invoked "{name}".')
        cx, cy, name = await self.uia.run(self.uia.center, eid)
        await self._approve(classify_ui_label(name), "gui", f'Click control "{name}"', name)
        await asyncio.to_thread(self.ctl.click, cx, cy, "left", 1, None)
        mx, my = self.screen.to_model(cx, cy, self._phys())
        return await self._result(a, f'Clicked "{name}".', (mx, my))

    async def _t_ui_set_text(self, a: dict) -> ToolOutput:
        eid = int(_num(a, "element_id"))
        text = a.get("text")
        if not isinstance(text, str):
            raise ToolError("'text' must be a string")
        await self._approve(Risk(), "gui", f"Set text of control [{eid}]", text[:500])
        name = await self.uia.run(self.uia.set_text, eid, text)
        return await self._result(a, f'Set text of "{name}" ({len(text)} characters).')

    async def _t_ui_read(self, a: dict) -> ToolOutput:
        text = await self.uia.run(self.uia.read, int(_num(a, "element_id")))
        return ToolOutput(text, detail=text[:2000])

    async def _t_focus_window(self, a: dict) -> ToolOutput:
        name = await self.uia.run(self.uia.focus_window, str(a.get("title", "")))
        return await self._result(a, f'Focused window "{name}".')

    async def _t_powershell(self, a: dict) -> ToolOutput:
        command = a.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ToolError("'command' must be a non-empty string")
        timeout = max(1, min(int(a.get("timeout_seconds", 60) or 60), 600))
        cwd = a.get("working_directory") or self.cwd
        if cwd and not os.path.isdir(cwd):
            raise ToolError(f"working_directory does not exist: {cwd}")
        risk = classify_powershell(command)
        await self._approve(risk, "powershell", "Run PowerShell command", command, ask_in_all_mode=True)
        res = await asyncio.to_thread(run_command, build_argv(command), timeout, cwd, self.kill)
        text = res.render()
        return ToolOutput(text, is_error=bool(res.exit_code), detail=f"$ {command}\n\n{text}"[:6000])

    async def _t_open_url(self, a: dict) -> ToolOutput:
        url = str(a.get("url", "")).strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ToolError("only absolute http(s) URLs can be opened")
        await asyncio.to_thread(self._open_url, url)
        await asyncio.to_thread(self.kill.wait, 1.2)
        return await self._result(a, f"Opened {url} in the default browser.")

    async def _t_request_approval(self, a: dict) -> ToolOutput:
        action = str(a.get("action", "")).strip()
        if not action:
            raise ToolError("'action' is required")
        category = a.get("category", "other")
        if self.approvals.mode == "never":
            return ToolOutput("APPROVED (the user has disabled approval prompts).")
        decision = await self.approvals.request(category, action, str(a.get("details", "")), risk="high" if category in ("purchase", "admin", "system") else "medium")
        if decision.approved:
            return ToolOutput(f"APPROVED by the user ({decision.via}). You may proceed with exactly that action.")
        return ToolOutput(f"DENIED by the user ({decision.reason}). Do NOT perform this action; ask how they want to proceed.")


def _default_open_url(url: str) -> None:
    if sys.platform == "win32":
        os.startfile(url)  # type: ignore[attr-defined]
    else:
        webbrowser.open(url)
