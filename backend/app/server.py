"""FastAPI app: authenticated WebSocket API for the Electron UI + the local MCP bridge for Claude Code."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import json
import logging
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from . import __version__
from .claude_cli import CliManager
from .config import Paths, SettingsStore
from .events import Client, Hub
from .history import ChatStore, WorkflowStore
from .hudshield import HudShield
from .input_control import InputController, InputDriver, ToolError
from .mcp_server import McpHandler, mount_mcp
from .osagent_tools import OsTools
from .runner import AgentRunner
from .safety import ApprovalManager, HotkeyLogic, HotkeyService, KillSwitch
from .screen import ScreenCapturer, ScreenDriver
from .uia import UiaBackend, UiaService, make_default_backend
from .voice import SpeechToText, strip_wake_word, tts_chunks

log = logging.getLogger("server")

ALLOWED_ORIGINS = {None, "null", "file://", "http://localhost:5173", "http://127.0.0.1:5173"}


class UnavailableDriver:
    """Stand-in used when the Windows drivers fail to initialise: every use raises a clear ToolError."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)

        def fail(*a, **k):
            raise ToolError(self.reason)

        return fail


@dataclass
class Drivers:
    """Platform drivers; tests inject fakes, production uses the Windows implementations."""

    input: InputDriver | None = None
    screen: ScreenDriver | None = None
    uia: UiaBackend | None = None
    open_url: Callable[[str], None] | None = None
    hotkeys: bool = True


@dataclass
class Services:
    paths: Paths
    settings: SettingsStore
    hub: Hub
    kill: KillSwitch
    approvals: ApprovalManager
    chats: ChatStore
    workflows: WorkflowStore
    controller: InputController
    screen: ScreenCapturer
    uia: UiaService
    tools: OsTools
    runner: AgentRunner
    cli: CliManager
    stt: SpeechToText
    mcp: McpHandler
    ws_token: str
    mcp_token: str
    hotkeys: HotkeyService | None = None
    port: int = 0
    os_errors: list[str] = field(default_factory=list)
    hotkeys_ok: bool = False
    tts_task: asyncio.Task | None = None
    voice_paused: bool = False


def create_services(data_dir: Path, *, ws_token: str, mcp_token: str, drivers: Drivers | None = None, port: int = 0) -> Services:
    drivers = drivers or Drivers()
    paths = Paths(data_dir).ensure()
    settings = SettingsStore(paths.settings)
    hub = Hub()
    kill = KillSwitch()
    approvals = ApprovalManager(hub.emit, kill, mode=lambda: settings.value.approval_mode)
    chats, workflows = ChatStore(paths.chats, paths.shots), WorkflowStore(paths.workflows)

    os_errors: list[str] = []
    input_driver = drivers.input
    if input_driver is None:
        try:
            from .input_control import PyAutoGuiDriver

            input_driver = PyAutoGuiDriver()
        except Exception as e:  # noqa: BLE001 - keep the app (and its UI) alive; runs will explain the problem
            log.error("input control unavailable: %s", e)
            os_errors.append(f"Mouse/keyboard control unavailable: {e}")
            input_driver = UnavailableDriver(os_errors[-1])
    screen_driver = drivers.screen
    if screen_driver is None:
        try:
            from .screen import MssScreenDriver

            screen_driver = MssScreenDriver()
            if sys.platform != "win32":
                raise RuntimeError("screen capture is only supported on Windows")
        except Exception as e:  # noqa: BLE001
            log.error("screen capture unavailable: %s", e)
            os_errors.append(f"Screen capture unavailable: {e}")
            screen_driver = UnavailableDriver(os_errors[-1])
    uia_backend = drivers.uia if drivers.uia is not None else make_default_backend()

    controller = InputController(input_driver, kill, shield=HudShield(), human_mouse=lambda: settings.value.human_mouse)
    screen = ScreenCapturer(screen_driver, max_edge=lambda: settings.value.screenshot_max_edge, quality=lambda: settings.value.jpeg_quality, draw_cursor=lambda: settings.value.draw_cursor)
    uia = UiaService(uia_backend, to_model=lambda x, y: screen.to_model(x, y, input_driver.size()))
    cli = CliManager(lambda: settings.value.claude_path)

    # The runner is the ActionSink; create tools with a forwarding sink to break the cycle.
    class _Sink:
        runner: AgentRunner | None = None

        def begin(self, *a):
            return self.runner.begin(*a)  # type: ignore[union-attr]

        def finish(self, *a):
            return self.runner.finish(*a)  # type: ignore[union-attr]

    sink = _Sink()
    tools = OsTools(controller, screen, uia, kill, approvals, lambda: settings.value, sink, cwd=str(Path.home()), open_url_fn=drivers.open_url)
    runner = AgentRunner(hub=hub, settings=settings, chats=chats, tools=tools, kill=kill, approvals=approvals, paths=paths, cli=cli, mcp_url=lambda: f"http://127.0.0.1:{svc_port()}/mcp", mcp_token=mcp_token)
    sink.runner = runner
    stt = SpeechToText(paths.models, lambda: settings.value.stt_model, lambda: settings.value.stt_language)

    holder: dict[str, int] = {"port": port}

    def svc_port() -> int:
        return holder["port"]

    svc = Services(paths, settings, hub, kill, approvals, chats, workflows, controller, screen, uia, tools, runner, cli, stt, McpHandler(tools), ws_token, mcp_token, port=port, os_errors=os_errors)
    svc._port_holder = holder  # type: ignore[attr-defined]

    if drivers.hotkeys:
        logic = HotkeyLogic(
            on_kill=lambda reason: kill.trigger(reason) if runner.active else None,
            on_ptt_down=lambda: hub.emit_threadsafe("hotkey", name="ptt_down"),
            on_ptt_up=lambda: hub.emit_threadsafe("hotkey", name="ptt_up"),
            armed=lambda: runner.active,
        )
        svc.hotkeys = HotkeyService(logic)
    return svc


def set_port(svc: Services, port: int) -> None:
    svc.port = port
    svc._port_holder["port"] = port  # type: ignore[attr-defined]


def origin_ok(origin: str | None) -> bool:
    return origin in ALLOWED_ORIGINS


def create_app(svc: Services) -> FastAPI:
    hub = svc.hub

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.bind_loop(asyncio.get_running_loop())
        svc.runner._loop = asyncio.get_running_loop()
        if svc.hotkeys:
            svc.hotkeys_ok = await asyncio.to_thread(svc.hotkeys.start)
        svc.runner.on_finished = lambda chat_id, status, text: _maybe_speak(svc, chat_id, status, text)
        yield
        await svc.runner.stop("app closing")
        if svc.hotkeys:
            svc.hotkeys.stop()
        with contextlib.suppress(OSError):  # the per-launch MCP token must not outlive this process
            (svc.paths.runtime / "mcp.json").unlink()

    app = FastAPI(title="Claude OS Agent backend", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    mount_mcp(app, svc.mcp, svc.mcp_token)

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "version": __version__, "platform": sys.platform}

    @app.get("/shots/{chat_id}/{name}")
    async def shots(chat_id: str, name: str, token: str = "") -> Response:
        if not hmac.compare_digest(token, svc.ws_token):
            return Response(status_code=401)
        p = svc.chats.shot_path(chat_id, name)
        if not p:
            return Response(status_code=404)
        return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        token = ws.query_params.get("token", "")
        if not hmac.compare_digest(token, svc.ws_token) or not origin_ok(ws.headers.get("origin")):
            await ws.close(code=4401)
            return
        await ws.accept()
        client = await hub.add(ws)
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                try:
                    await dispatch(svc, client, msg)
                except Exception as e:  # noqa: BLE001
                    log.exception("handler %s failed", msg.get("type"))
                    hub.send_to(client, "error", message=f"{msg.get('type')}: {e}")
        except WebSocketDisconnect:
            pass
        finally:
            await hub.remove(client)

    return app


# ----------------------------------------------------------------------------------------
# message dispatch
# ----------------------------------------------------------------------------------------
def screen_info(svc: Services) -> dict:
    try:
        w, h = svc.controller.d.size()
        s = svc.screen.scale_for(w, h)
        return {"width": w, "height": h, "scale": round(s, 4), "shot_width": round(w * s), "shot_height": round(h * s)}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


async def refresh_auth(svc: Services, force: bool = False) -> None:
    a = await asyncio.to_thread(svc.cli.auth, force)
    svc.hub.emit("auth.status", **a.to_event())


async def dispatch(svc: Services, client: Client, msg: dict) -> None:
    hub, runner = svc.hub, svc.runner
    t = msg.get("type")

    if t == "hello":
        hub.send_to(client, "ready", version=__version__, platform=sys.platform, screen=screen_info(svc), uia=svc.uia.available, stt=SpeechToText.available()[0], hotkeys=svc.hotkeys_ok, os_errors=svc.os_errors)
        hub.send_to(client, "settings", settings=svc.settings.value.model_dump())
        hub.send_to(client, "chats", chats=svc.chats.list())
        hub.send_to(client, "workflows", workflows=svc.workflows.list())
        hub.send_to(client, "state", state=runner.state, chat_id=runner.run.chat["id"] if runner.run else None)
        for p in svc.approvals.pending():
            hub.send_to(client, "approval.request", **p)
        asyncio.create_task(refresh_auth(svc))

    elif t == "auth.status":
        await refresh_auth(svc, bool(msg.get("force")))

    elif t == "auth.login":
        def on_url(url: str) -> None:
            hub.emit_threadsafe("auth.login.url", url=url)

        def on_done(ok: bool, detail: str) -> None:
            svc.cli.invalidate()
            hub.emit_threadsafe("auth.login.done", ok=ok, detail=detail)
            loop = hub._loop
            if loop:
                asyncio.run_coroutine_threadsafe(refresh_auth(svc, True), loop)

        started = await asyncio.to_thread(svc.cli.start_login, on_url, on_done)
        hub.send_to(client, "auth.login.started", started=started)
        if not started:
            await refresh_auth(svc, True)  # CLI missing -> status explains how to install it

    elif t == "auth.cancel":
        svc.cli.cancel_login()

    elif t == "auth.logout":
        await asyncio.to_thread(svc.cli.logout)
        await refresh_auth(svc, True)

    elif t == "settings.set":
        try:
            s = svc.settings.update(msg.get("patch") or {})
        except Exception as e:  # noqa: BLE001
            hub.send_to(client, "error", message=f"Invalid settings: {e}")
            return
        hub.emit("settings", settings=s.model_dump())
        if "claude_path" in (msg.get("patch") or {}):
            svc.cli.info(force=True)
            await refresh_auth(svc, True)

    elif t == "chats.list":
        hub.send_to(client, "chats", chats=svc.chats.list())

    elif t == "chat.load":
        chat = svc.chats.get(str(msg.get("id", "")))
        if chat:
            hub.send_to(client, "chat.opened", chat=chat)

    elif t == "chat.delete":
        if not (runner.run and runner.run.chat["id"] == msg.get("id")):
            svc.chats.delete(str(msg.get("id", "")))
            hub.emit("chats", chats=svc.chats.list())

    elif t == "chat.rename":
        svc.chats.rename(str(msg.get("id", "")), str(msg.get("title", "")))
        hub.emit("chats", chats=svc.chats.list())

    elif t == "chat.send":
        text = str(msg.get("text", "")).strip()
        if not text:
            return
        if runner.active:
            await runner.send(None, text)
            return
        chat = (svc.chats.get(msg["chat_id"]) if msg.get("chat_id") else None) or svc.chats.create()
        hub.emit("chat.opened", chat=chat)
        await runner.send(chat["id"], text)

    elif t == "agent.stop":
        await runner.stop("Stopped by you")
    elif t == "agent.pause":
        runner.pause()
    elif t == "agent.resume":
        runner.resume()

    elif t == "approval.respond":
        svc.approvals.resolve(str(msg.get("id")), bool(msg.get("approved")), via=str(msg.get("via", "ui")))

    elif t == "workflows.list":
        hub.send_to(client, "workflows", workflows=svc.workflows.list())
    elif t == "workflows.save":
        svc.workflows.save(str(msg.get("name", "")), str(msg.get("prompt", "")), msg.get("id"))
        hub.emit("workflows", workflows=svc.workflows.list())
    elif t == "workflows.delete":
        svc.workflows.delete(str(msg.get("id", "")))
        hub.emit("workflows", workflows=svc.workflows.list())
    elif t == "workflows.run":
        wf = svc.workflows.get(str(msg.get("id", "")))
        if wf and not runner.active:
            svc.workflows.touch(wf["id"])
            chat = svc.chats.create(wf["name"])
            hub.emit("chat.opened", chat=chat)
            hub.emit("workflows", workflows=svc.workflows.list())
            await runner.send(chat["id"], wf["prompt"])

    elif t == "voice.speech_start":
        _cancel_tts(svc)
        if runner.active and runner.state == "running":
            runner.pause()
            svc.voice_paused = True
    elif t == "voice.speech_cancel":
        if svc.voice_paused:
            svc.voice_paused = False
            runner.resume()
    elif t == "voice.utterance":
        asyncio.create_task(handle_utterance(svc, msg))
    elif t == "tts.speak":
        _start_tts(svc, str(msg.get("text", "")))
    elif t == "tts.cancel":
        _cancel_tts(svc)

    elif t == "screen.info":
        hub.send_to(client, "screen.info", **screen_info(svc))


# ----------------------------------------------------------------------------------------
# voice
# ----------------------------------------------------------------------------------------
async def handle_utterance(svc: Services, msg: dict) -> None:
    hub, runner = svc.hub, svc.runner
    mode = msg.get("mode", "ptt")
    was_voice_paused = svc.voice_paused

    def release() -> None:
        if svc.voice_paused:
            svc.voice_paused = False
            runner.resume()

    ok, why = SpeechToText.available()
    if not ok:
        hub.emit("voice.error", message=why)
        release()
        return
    try:
        wav = base64.b64decode(msg.get("audio_b64", ""))
        hub.emit("voice.status", state="transcribing")
        text = await asyncio.to_thread(svc.stt.transcribe, wav)
    except Exception as e:  # noqa: BLE001
        log.exception("transcription failed")
        hub.emit("voice.error", message=f"Speech recognition failed: {e}")
        release()
        return
    finally:
        hub.emit("voice.status", state="idle")

    if not text:
        hub.emit("voice.transcript", text="", ignored=True)
        release()
        return

    # 1) a spoken yes/no answers a pending approval prompt
    if svc.approvals.pending():
        decision = svc.approvals.resolve_by_voice(text)
        hub.emit("voice.transcript", text=text, approval=decision)
        if decision is not None:
            release()
            return

    # 2) hands-free wake word
    cfg = svc.settings.value
    if mode == "vad" and cfg.wake_word_enabled:
        rest = strip_wake_word(text, cfg.wake_word)
        if rest is None:
            hub.emit("voice.transcript", text=text, ignored=True, reason="wake word not heard")
            release()
            return
        text = rest
        if not text:
            hub.emit("voice.transcript", text="", ignored=True, reason="wake word only")
            release()
            return

    hub.emit("voice.transcript", text=text, final=True)
    svc.voice_paused = False
    if runner.active:
        await runner.send(None, text)  # voice interruption: abort the current step and follow the new instruction
    else:
        chat = (svc.chats.get(msg["chat_id"]) if msg.get("chat_id") else None) or svc.chats.create()
        hub.emit("chat.opened", chat=chat)
        await runner.send(chat["id"], text)


def _cancel_tts(svc: Services) -> None:
    if svc.tts_task and not svc.tts_task.done():
        svc.tts_task.cancel()
    svc.hub.emit("tts.stop")


def _start_tts(svc: Services, text: str) -> None:
    _cancel_tts(svc)
    if not text.strip():
        return
    cfg = svc.settings.value

    async def run() -> None:
        try:
            async for seq, audio, last in tts_chunks(text, cfg.tts_voice, cfg.tts_rate):
                svc.hub.emit("tts.chunk", seq=seq, last=last, mime="audio/mpeg", audio_b64=base64.b64encode(audio).decode("ascii"))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            svc.hub.emit("voice.error", message=f"Text-to-speech failed: {e}")

    svc.tts_task = asyncio.create_task(run())


def _maybe_speak(svc: Services, chat_id: str, status: str, text: str) -> None:
    if status == "done" and text and svc.settings.value.speak_responses:
        _start_tts(svc, text)
