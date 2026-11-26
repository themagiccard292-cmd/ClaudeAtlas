"""Safety layer: kill switch, global hotkeys, risk classification and user approvals."""
from __future__ import annotations

import asyncio
import logging
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger("safety")


# ----------------------------------------------------------------------------------
# Kill switch + pause gate (shared by every thread that touches mouse/keyboard)
# ----------------------------------------------------------------------------------
class Halted(Exception):
    """Raised inside any input/automation thread once the kill switch has fired."""


class KillSwitch:
    def __init__(self) -> None:
        self._halt = threading.Event()
        self._running = threading.Event()  # set = not paused
        self._running.set()
        self.reason: str = ""
        self._callbacks: list[Callable[[str], None]] = []

    # -- kill -----------------------------------------------------------------
    @property
    def halted(self) -> bool:
        return self._halt.is_set()

    def trigger(self, reason: str = "kill switch") -> None:
        if not self._halt.is_set():
            self.reason = reason
            self._halt.set()
            self._running.set()  # wake anything parked in the pause gate so it can die
            for cb in list(self._callbacks):
                try:
                    cb(reason)
                except Exception:  # never let a callback break the kill path
                    log.exception("kill callback failed")

    def reset(self) -> None:
        self._halt.clear()
        self._running.set()
        self.reason = ""

    def on_trigger(self, cb: Callable[[str], None]) -> None:
        self._callbacks.append(cb)

    def check(self) -> None:
        if self._halt.is_set():
            raise Halted(self.reason or "halted")

    def wait(self, seconds: float) -> None:
        """Interruptible sleep: returns early (raising) if the kill switch fires."""
        if self._halt.wait(timeout=max(0.0, seconds)):
            raise Halted(self.reason or "halted")

    # -- pause (voice interruption / user pause) ---------------------------------
    @property
    def paused(self) -> bool:
        return not self._running.is_set()

    def pause(self) -> None:
        self._running.clear()

    def resume(self) -> None:
        self._running.set()

    def gate(self) -> None:
        """Blocking gate for worker threads: waits while paused, raises if halted."""
        while True:
            self.check()
            if self._running.wait(timeout=0.05):
                self.check()
                return

    async def agate(self) -> None:
        """Async twin of :meth:`gate` for coroutines."""
        while True:
            self.check()
            if not self.paused:
                return
            await asyncio.sleep(0.05)


# ----------------------------------------------------------------------------------
# Global hotkeys: logic is pure (testable); the pynput glue is Windows-only below.
# ----------------------------------------------------------------------------------
VK_ESCAPE, VK_SPACE = 0x1B, 0x20
_CTRL = {0x11, 0xA2, 0xA3}
_SHIFT = {0x10, 0xA0, 0xA1}
_ALT = {0x12, 0xA4, 0xA5}


class HotkeyLogic:
    """Turns raw (vk, down/up, injected) key events into kill / push-to-talk actions.

    * Kill:  Ctrl+Shift+Esc, or Esc pressed twice within ``double_esc_window`` seconds.
    * PTT:   hold Alt+Space (down on Space-with-Alt, up when Space *or* Alt is released).
    * Events synthesised by *us* (pyautogui/pynput SendInput, flagged ``injected``) are
      ignored, otherwise the agent pressing Esc would trigger its own kill switch.
    """

    def __init__(
        self,
        on_kill: Callable[[str], None],
        on_ptt_down: Callable[[], None],
        on_ptt_up: Callable[[], None],
        armed: Callable[[], bool] = lambda: True,
        double_esc_window: float = 0.6,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.on_kill, self.on_ptt_down, self.on_ptt_up = on_kill, on_ptt_down, on_ptt_up
        self.armed = armed
        self.window = double_esc_window
        self.now = now
        self._down: set[int] = set()
        self._last_esc: float = -1e9
        self.ptt_active = False

    def _held(self, group: set[int]) -> bool:
        return bool(self._down & group)

    def feed(self, vk: int, down: bool, injected: bool = False) -> bool:
        """Process one key event. Returns True if the event should be swallowed."""
        if injected:
            return False
        was_down = vk in self._down
        if down:
            self._down.add(vk)
        else:
            self._down.discard(vk)

        suppress = False
        if down and not was_down:  # real key-down transition (ignore auto-repeat)
            if vk == VK_ESCAPE:
                if self._held(_CTRL) and self._held(_SHIFT):
                    self.on_kill("Ctrl+Shift+Esc")
                    suppress = self.armed()  # keep Task Manager from opening mid-run
                    self._last_esc = -1e9
                else:
                    t = self.now()
                    if t - self._last_esc <= self.window:
                        self._last_esc = -1e9
                        self.on_kill("Esc Esc")
                    else:
                        self._last_esc = t
            elif vk == VK_SPACE and self._held(_ALT) and not self.ptt_active:
                self.ptt_active = True
                self.on_ptt_down()
        elif not down and self.ptt_active and (vk == VK_SPACE or vk in _ALT):
            self.ptt_active = False
            self.on_ptt_up()
        return suppress


class HotkeyService:
    """pynput low-level keyboard hook (Windows). Safe to construct elsewhere: it just no-ops."""

    LLKHF_INJECTED = 0x10

    def __init__(self, logic: HotkeyLogic) -> None:
        self.logic = logic
        self._listener = None
        self.error: str | None = None

    def start(self) -> bool:
        try:
            from pynput import keyboard  # imported lazily: needs a desktop session
        except Exception as e:  # pragma: no cover - environment specific
            self.error = f"pynput unavailable: {e}"
            log.warning(self.error)
            return False

        def win32_filter(msg: int, data) -> bool:
            # msg: 0x100 KEYDOWN, 0x101 KEYUP, 0x104 SYSKEYDOWN, 0x105 SYSKEYUP
            down = msg in (0x100, 0x104)
            injected = bool(getattr(data, "flags", 0) & self.LLKHF_INJECTED)
            suppress = False
            try:
                suppress = self.logic.feed(int(data.vkCode), down, injected)
            except Exception:  # pragma: no cover
                log.exception("hotkey logic failed")
            if suppress and self._listener is not None:
                # NOTE: pynput implements this by raising an exception that its own hook machinery catches,
                # so it must stay outside the try/except above.
                self._listener.suppress_event()
            return False  # we handle everything here; no pynput callbacks needed

        try:
            self._listener = keyboard.Listener(win32_event_filter=win32_filter, on_press=lambda k: None, on_release=lambda k: None)
            self._listener.daemon = True
            self._listener.start()
            return True
        except Exception as e:  # pragma: no cover
            self.error = str(e)
            log.warning("hotkey listener failed: %s", e)
            return False

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None


# ----------------------------------------------------------------------------------
# Risk classification
# ----------------------------------------------------------------------------------
@dataclass
class Risk:
    level: str = "none"  # none | medium | high
    reasons: list[str] = field(default_factory=list)
    category: str = "other"

    @property
    def needs_approval(self) -> bool:
        return self.level != "none"


def _w(words: str) -> str:
    return rf"(?<![\w-])(?:{words})(?![\w-])"


_PS_RULES: list[tuple[str, str, str, str]] = [
    # (regex, level, category, reason)
    (r"-verb\s+['\"]?runas|" + _w(r"runas|gsudo|sudo"), "high", "admin", "runs with administrator privileges"),
    (_w(r"stop-computer|restart-computer|shutdown(?:\.exe)?|logoff"), "high", "system", "shuts down / restarts / logs off the PC"),
    (_w(r"format-volume|clear-disk|remove-partition|diskpart|bcdedit|vssadmin|cipher"), "high", "system", "modifies disks or boot configuration"),
    (_w(r"set-executionpolicy|set-mppreference|add-mppreference|new-service|remove-service|set-service"), "high", "system", "changes security or service configuration"),
    (_w(r"reg(?:\.exe)?\s+(?:delete|add|import|load)|remove-itemproperty|new-itemproperty|set-itemproperty") + r"[^\n]*", "high", "system", "edits the Windows registry"),
    (_w(r"schtasks(?:\.exe)?") + r"[^\n]*/(?:create|delete|change)", "high", "system", "creates or changes scheduled tasks"),
    (_w(r"net(?:\.exe)?\s+(?:user|localgroup)|netsh\s+advfirewall"), "high", "admin", "changes accounts or firewall"),
    (_w(r"remove-item|ri|rm|del|erase|rmdir|rd") , "medium", "delete", "deletes files or folders"),
    (r"\[(?:system\.)?io\.(?:file|directory)\]::delete|\.delete\(\s*\)|clear-recyclebin", "medium", "delete", "deletes files or folders"),
    (_w(r"send-mailmessage") + r"|outlook\.application|mailto:", "medium", "send", "sends an email"),
    (_w(r"stop-process|taskkill(?:\.exe)?|kill|pkill"), "medium", "system", "terminates running programs"),
    (r"(?:iex|invoke-expression)[^\n]*(?:iwr|invoke-webrequest|downloadstring|curl|wget)|(?:iwr|invoke-webrequest|curl|wget)[^\n|]*\|\s*(?:iex|invoke-expression)", "high", "system", "downloads and executes remote code"),
    (_w(r"invoke-webrequest|iwr|invoke-restmethod|irm|curl|wget|start-bitstransfer") + r"[^\n]*(?:-outfile|-o\b)", "medium", "other", "downloads files"),
    (r"\bgit\s+(?:push\b[^\n]*--force|reset\s+--hard|clean\s+-[a-z]*f)", "medium", "delete", "destructive git operation"),
    (_w(r"move-item|mv|move") + r"[^\n]*-force", "medium", "delete", "may overwrite files"),
]
_PS_COMPILED = [(re.compile(p, re.I), lvl, cat, why) for p, lvl, cat, why in _PS_RULES]


def classify_powershell(command: str) -> Risk:
    risk = Risk()
    for rx, level, cat, why in _PS_COMPILED:
        if not rx.search(command):
            continue
        risk.reasons.append(why)
        # first match sets the level; a later "high" match always wins over "medium"
        if risk.level == "none" or (level == "high" and risk.level != "high"):
            risk.level, risk.category = level, cat
    risk.reasons = list(dict.fromkeys(risk.reasons))
    return risk


_LABEL_RX = re.compile(
    _w(
        r"delete|remove|erase|uninstall|format|wipe|empty recycle bin|factory reset|permanently delete|"
        r"send|send now|send message|buy|buy now|purchase|pay|pay now|place order|checkout|complete order|"
        r"confirm purchase|confirm order|submit order|subscribe|transfer|withdraw|donate|run as administrator"
    ),
    re.I,
)


def classify_ui_label(label: str | None) -> Risk:
    """Heuristic for GUI clicks: looks at the accessible name of the element under the pointer."""
    if not label or len(label) > 80:
        return Risk()
    m = _LABEL_RX.search(label)
    if not m:
        return Risk()
    word = m.group(0).lower()
    cat = "delete"
    if word.startswith(("send",)):
        cat = "send"
    elif word in {"buy", "buy now", "purchase", "pay", "pay now", "place order", "checkout", "complete order", "confirm purchase", "confirm order", "submit order", "subscribe", "transfer", "withdraw", "donate"}:
        cat = "purchase"
    elif "administrator" in word:
        cat = "admin"
    return Risk(level="high" if cat in ("purchase", "admin") else "medium", reasons=[f'clicks a "{label.strip()}" control'], category=cat)


_YES = re.compile(r"\b(yes|yeah|yep|yup|approve|approved|confirm|confirmed|go ahead|do it|proceed|allow|okay|ok|sure|affirmative)\b", re.I)
_NO = re.compile(r"\b(no|nope|nah|deny|denied|cancel|stop|abort|reject|negative|don'?t|do not)\b", re.I)


def parse_voice_decision(text: str) -> bool | None:
    """'yes, go ahead' -> True; any negative word -> False; neither/empty -> None.

    Fail-safe by design: "don't do it" contains the affirmative phrase "do it" but is a refusal,
    so a negative word always wins. (An unwanted denial is just one click on the Approve button.)
    """
    if _NO.search(text or ""):
        return False
    return True if _YES.search(text or "") else None


# ----------------------------------------------------------------------------------
# Approvals
# ----------------------------------------------------------------------------------
@dataclass
class Decision:
    approved: bool
    reason: str = ""
    via: str = "ui"


class ApprovalManager:
    def __init__(self, emit: Callable[..., None], kill: KillSwitch, mode: Callable[[], str] = lambda: "smart") -> None:
        self._emit = emit
        self._kill = kill
        self._mode = mode
        self._pending: dict[str, tuple[asyncio.Future, dict]] = {}
        kill.on_trigger(lambda r: self._cancel_threadsafe(f"stopped ({r})"))
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def mode(self) -> str:
        return self._mode()

    def pending(self) -> list[dict]:
        return [info for _, info in self._pending.values()]

    async def request(self, kind: str, summary: str, details: str = "", risk: str = "medium", timeout: float = 240.0) -> Decision:
        """Ask the user. In 'never' mode (explicit opt-in to full autonomy) it auto-approves."""
        if self.mode == "never":
            return Decision(True, "auto-approved (approval mode: never ask)", via="auto")
        loop = asyncio.get_running_loop()
        self._loop = loop
        aid = uuid.uuid4().hex[:10]
        fut: asyncio.Future = loop.create_future()
        info = {"id": aid, "kind": kind, "summary": summary, "details": details[:4000], "risk": risk, "ts": time.time()}
        self._pending[aid] = (fut, info)
        self._emit("approval.request", **info)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return Decision(False, "no response from the user (timed out)", via="timeout")
        finally:
            self._pending.pop(aid, None)
            self._emit("approval.resolved", id=aid)

    def resolve(self, approval_id: str, approved: bool, via: str = "ui") -> bool:
        item = self._pending.get(approval_id)
        if not item or item[0].done():
            return False
        item[0].set_result(Decision(approved, "approved by user" if approved else "denied by user", via))
        return True

    def resolve_by_voice(self, transcript: str) -> bool | None:
        """Resolve the oldest pending approval from a spoken 'yes' / 'no'. Returns decision or None."""
        decision = parse_voice_decision(transcript)
        if decision is None or not self._pending:
            return None
        aid = next(iter(self._pending))
        self.resolve(aid, decision, via="voice")
        return decision

    def cancel_all(self, reason: str) -> None:
        for fut, _ in list(self._pending.values()):
            if not fut.done():
                fut.set_result(Decision(False, reason, via="cancelled"))

    def _cancel_threadsafe(self, reason: str) -> None:
        loop = self._loop
        if loop and not loop.is_closed():
            loop.call_soon_threadsafe(self.cancel_all, reason)
        else:
            self.cancel_all(reason)


def is_windows() -> bool:
    return sys.platform == "win32"
