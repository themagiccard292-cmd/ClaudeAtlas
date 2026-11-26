"""Small cross-platform process helpers (no asyncio subprocess: avoids event-loop-policy traps on Windows)."""
from __future__ import annotations

import contextlib
import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def popen_flags() -> dict:
    """kwargs that start a child silently (no console window flashing) on Windows."""
    if sys.platform == "win32":
        return {"creationflags": CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def kill_tree(pid: int, timeout: float = 3.0) -> None:
    """Kill a process and every descendant (PowerShell spawns children; so does the claude CLI)."""
    import psutil

    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = parent.children(recursive=True) + [parent]
    for p in procs:
        with contextlib.suppress(psutil.Error):
            p.kill()
    psutil.wait_procs(procs, timeout=timeout)


def run_quiet(argv: list[str], timeout: float = 20.0, env: dict | None = None, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run a short helper command (e.g. `claude auth status`) without a console window."""
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = CREATE_NO_WINDOW
    return subprocess.run(argv, capture_output=True, timeout=timeout, env=env, cwd=cwd, stdin=subprocess.DEVNULL, **kw)
