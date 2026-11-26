"""Entry point of backend.exe (also `python -m app.main` in development)."""
from __future__ import annotations

# --- must run before pyautogui / mss / pywinauto are imported anywhere ---------------------
from app import dpi  # noqa: E402

DPI_MODE = dpi.enable_dpi_awareness()

import argparse  # noqa: E402
import logging  # noqa: E402
import logging.handlers  # noqa: E402
import os  # noqa: E402
import secrets  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402


def watch_parent(pid: int) -> None:
    """If the Electron parent dies (crash / killed), take the backend (and its input control) down too."""
    import psutil

    def loop() -> None:
        while True:
            time.sleep(2)
            if not psutil.pid_exists(pid):
                os._exit(0)

    threading.Thread(target=loop, daemon=True, name="parent-watch").start()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Claude OS Agent backend")
    p.add_argument("--port", type=int, default=0, help="localhost port (0 = pick a free one)")
    p.add_argument("--data-dir", type=Path, default=None)
    p.add_argument("--parent-pid", type=int, default=0)
    args = p.parse_args(argv)

    ws_token = os.environ.get("CLAUDE_OS_AGENT_TOKEN", "")
    if len(ws_token) < 16:
        print("error: CLAUDE_OS_AGENT_TOKEN (>= 16 chars) must be set by the launcher", file=sys.stderr)
        return 2

    from app.config import default_data_dir
    from app.server import create_app, create_services, set_port

    data_dir = args.data_dir or default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr), logging.handlers.RotatingFileHandler(data_dir / "logs" / "backend.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")],
    )
    log = logging.getLogger("main")
    log.info("starting; dpi=%s data_dir=%s", DPI_MODE, data_dir)

    if args.parent_pid:
        watch_parent(args.parent_pid)

    import socket

    import uvicorn

    # Bind the socket ourselves so the chosen port is known before the app starts serving.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", args.port))
    port = sock.getsockname()[1]
    sock.listen(64)

    svc = create_services(data_dir, ws_token=ws_token, mcp_token=secrets.token_urlsafe(32), port=port)
    set_port(svc, port)
    app = create_app(svc)

    print(f"READY port={port}", flush=True)  # the launcher also polls /health; this line is for humans/logs
    config = uvicorn.Config(app, log_level="warning", ws_ping_interval=20, ws_ping_timeout=20)
    server = uvicorn.Server(config)
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
