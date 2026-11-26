"""PowerShell execution tool (files, apps, folders, scripts)."""
from __future__ import annotations

import base64
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

from .procutil import kill_tree, popen_flags
from .safety import Halted, KillSwitch

MAX_OUTPUT_CHARS = 20_000
_PREFIX = (
    "$ProgressPreference='SilentlyContinue'; $ErrorActionPreference='Continue'; "
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; $OutputEncoding=[System.Text.Encoding]::UTF8; "
)


@dataclass
class ShellResult:
    exit_code: int | None
    output: str
    timed_out: bool = False
    killed: bool = False
    duration: float = 0.0
    truncated: bool = False

    def render(self) -> str:
        head = []
        if self.timed_out:
            head.append("[timed out - process was terminated]")
        if self.killed:
            head.append("[stopped by user]")
        head.append(f"[exit code {self.exit_code}]")
        return ("\n".join(head) + "\n" + (self.output or "(no output)")).strip()


def build_argv(command: str) -> list[str]:
    """Windows PowerShell 5.1 with an encoded command (immune to quoting problems)."""
    if sys.platform == "win32":
        encoded = base64.b64encode((_PREFIX + command).encode("utf-16-le")).decode("ascii")
        return ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]
    pwsh = shutil.which("pwsh")
    if pwsh:
        return [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["bash", "-c", command]  # dev / test fallback on non-Windows hosts


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    head, tail = int(limit * 0.6), int(limit * 0.4)
    return f"{text[:head]}\n... [{len(text) - limit} characters omitted] ...\n{text[-tail:]}", True


def run_command(argv: list[str], timeout: float, cwd: str | None, kill: KillSwitch, max_chars: int = MAX_OUTPUT_CHARS) -> ShellResult:
    """Blocking; run it in a worker thread. Honors the kill switch and the timeout."""
    start = time.monotonic()
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd, **popen_flags())
    chunks: list[bytes] = []

    def reader() -> None:
        assert proc.stdout is not None
        for block in iter(lambda: proc.stdout.read(4096), b""):
            chunks.append(block)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    timed_out = killed = False
    try:
        while proc.poll() is None:
            if kill.halted:
                killed = True
                break
            if time.monotonic() - start > timeout:
                timed_out = True
                break
            time.sleep(0.05)
    finally:
        if proc.poll() is None:
            kill_tree(proc.pid)
        t.join(timeout=2)
    out, truncated = _truncate(b"".join(chunks).decode("utf-8", errors="replace"), max_chars)
    if killed:
        raise Halted(kill.reason or "halted")
    return ShellResult(proc.returncode, out, timed_out=timed_out, killed=killed, duration=time.monotonic() - start, truncated=truncated)
