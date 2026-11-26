"""Orchestrates an agent run: spawns `claude -p` (Pro login), relays its stream-json to the UI,
records the transcript, and implements stop / pause / voice-interrupt (kill + `--resume`)."""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .claude_cli import CliManager, build_run_argv, parse_stream_line, sanitized_env
from .config import Paths, SettingsStore
from .events import Hub
from .history import ChatStore
from .osagent_tools import SERVER_NAME, OsTools, allowed_tool_names
from .procutil import kill_tree, popen_flags
from .prompts import SYSTEM_PROMPT
from .safety import ApprovalManager, KillSwitch
from .screen import Shot

log = logging.getLogger("runner")


@dataclass
class Run:
    chat: dict
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    proc: subprocess.Popen | None = None
    session_id: str | None = None
    stop_reason: str = ""
    user_stopped: bool = False
    pending_interrupt: str | None = None
    final_text: str = ""
    failed: str = ""
    resume_failed: bool = False
    original_prompt: str = ""
    started: float = field(default_factory=time.time)


def write_private(path: Path, text: str) -> None:
    """Write a small file readable only by the current user (0600 where the OS supports it)."""
    import os

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def _clip_args(args: dict) -> dict:
    out = {}
    for k, v in (args or {}).items():
        if isinstance(v, str) and len(v) > 600:
            v = v[:600] + f"… (+{len(v) - 600} chars)"
        elif isinstance(v, list):
            v = json.loads(json.dumps(v, default=str)[:1500]) if len(json.dumps(v, default=str)) <= 1500 else f"[{len(v)} items]"
        out[k] = v
    return out


class AgentRunner:
    def __init__(
        self,
        *,
        hub: Hub,
        settings: SettingsStore,
        chats: ChatStore,
        tools: OsTools,
        kill: KillSwitch,
        approvals: ApprovalManager,
        paths: Paths,
        cli: CliManager,
        mcp_url: Callable[[], str],
        mcp_token: str,
    ) -> None:
        self.hub, self.settings, self.chats, self.tools = hub, settings, chats, tools
        self.kill, self.approvals, self.paths, self.cli = kill, approvals, paths, cli
        self.mcp_url, self.mcp_token = mcp_url, mcp_token
        self.run: Run | None = None
        self.state = "idle"
        self.on_finished: Callable[[str, str, str], None] | None = None  # (chat_id, status, final_text)
        self._task: asyncio.Task | None = None
        self._stream_item: dict | None = None
        self._tool_items: dict[str, str] = {}
        self._last_save = 0.0
        self._loop: asyncio.AbstractEventLoop | None = None
        kill.on_trigger(self._on_kill_threadsafe)
        tools.on_over_budget = self._budget_stop

    # ------------------------------------------------------------------ public API
    @property
    def active(self) -> bool:
        return self.run is not None

    async def send(self, chat_id: str | None, text: str) -> dict:
        text = text.strip()
        if not text:
            return {"ok": False, "error": "empty message"}
        self._loop = asyncio.get_running_loop()
        if self.run:  # steer a run in progress (e.g. typed or spoken interruption)
            await self.interrupt_with(text)
            return {"ok": True, "chat_id": self.run.chat["id"], "injected": True}
        chat = (self.chats.get(chat_id) if chat_id else None) or self.chats.create()
        self._task = asyncio.create_task(self._main(chat, text))
        return {"ok": True, "chat_id": chat["id"], "injected": False}

    async def stop(self, reason: str = "Stopped by you") -> None:
        run = self.run
        if not run:
            return
        run.user_stopped = True
        run.stop_reason = reason
        run.pending_interrupt = None
        self.kill.trigger(reason)  # -> _on_kill: halts input threads, ends the CLI process

    def pause(self) -> None:
        if self.run and self.state == "running":
            self.kill.pause()
            self._set_state("paused")

    def resume(self) -> None:
        if self.run and self.state == "paused":
            self.kill.resume()
            self._set_state("running")

    async def interrupt_with(self, text: str) -> None:
        """New instruction mid-run: abort the current CLI process and continue the same session."""
        run = self.run
        if not run:
            return
        run.pending_interrupt = text
        self.kill.resume()
        self.kill.trigger("interrupted")  # releases anything waiting in the pause gate / approvals

    async def wait_idle(self, timeout: float = 30.0) -> None:
        if self._task:
            await asyncio.wait_for(asyncio.shield(self._task), timeout)

    # ------------------------------------------------------------------ kill handling
    def _on_kill_threadsafe(self, reason: str) -> None:
        loop = self._loop
        if loop and not loop.is_closed():
            loop.call_soon_threadsafe(self._on_kill, reason)

    def _on_kill(self, reason: str) -> None:
        run = self.run
        if not run:
            return
        if not run.pending_interrupt:
            run.user_stopped = True
            run.stop_reason = run.stop_reason or reason
            self.hub.emit("kill", reason=reason, chat_id=run.chat["id"])
        self.approvals.cancel_all(f"stopped ({reason})")
        self._terminate(reason)

    def _budget_stop(self) -> None:
        run = self.run
        if run:
            run.user_stopped = True
            run.stop_reason = "the per-run action limit was reached (raise it in Settings → Safety)"
        self._terminate("action budget exhausted")

    def _terminate(self, why: str) -> None:
        run = self.run
        proc = run.proc if run else None
        if proc and proc.poll() is None:
            log.info("terminating claude process (%s)", why)
            threading.Thread(target=kill_tree, args=(proc.pid,), daemon=True).start()

    # ------------------------------------------------------------------ state / transcript helpers
    def _set_state(self, state: str, **extra: Any) -> None:
        self.state = state
        chat_id = self.run.chat["id"] if self.run else None
        self.hub.emit("state", state=state, chat_id=chat_id, **extra)

    def _add_item(self, item: dict) -> dict:
        chat = self.run.chat if self.run else None
        if chat is None:
            return item
        item.setdefault("id", uuid.uuid4().hex[:10])
        item.setdefault("ts", time.time())
        chat["items"].append(item)
        self.hub.emit("item", chat_id=chat["id"], item=item)
        self._save()
        return item

    def _patch_item(self, item_id: str, **patch: Any) -> None:
        chat = self.run.chat if self.run else None
        if chat is None:
            return
        for it in reversed(chat["items"]):
            if it["id"] == item_id:
                it.update(patch)
                break
        self.hub.emit("item.update", chat_id=chat["id"], id=item_id, patch=patch)
        self._save()

    def _note(self, text: str, level: str = "info") -> None:
        self._add_item({"role": "system", "level": level, "text": text})

    def _save(self, force: bool = False) -> None:
        if self.run and (force or time.monotonic() - self._last_save > 1.5):
            self.chats.save(self.run.chat)
            self._last_save = time.monotonic()

    # ------------------------------------------------------------------ ActionSink (called by OsTools)
    def begin(self, tool: str, args: dict, summary: str, kind: str) -> str:
        self._stream_item = None
        item = self._add_item({"role": "action", "tool": tool, "summary": summary, "kind": kind, "status": "running", "input": _clip_args(args)})
        return item["id"]

    def finish(self, action_id: str, ok: bool, output: str, shot: Shot | None, coord: tuple[int, int] | None, detail: str) -> None:
        patch: dict[str, Any] = {"status": "done" if ok else "error", "output": output[:1500]}
        if detail:
            patch["detail"] = detail
        if shot is not None and self.run:
            patch["shot"] = self.chats.save_shot(self.run.chat["id"], shot.thumb)
            patch["shot_size"] = [shot.width, shot.height]
        if coord:
            patch["coord"] = list(coord)
        self._patch_item(action_id, **patch)

    # ------------------------------------------------------------------ main loop
    async def _main(self, chat: dict, text: str) -> None:
        run = Run(chat=chat, original_prompt=text)
        self.run = run
        self.kill.reset()
        self.tools.begin_run()
        self._set_state("running")
        self._add_item({"role": "user", "text": text})
        if chat.get("title", "New chat") == "New chat":
            flat = " ".join(text.split())
            chat["title"] = (flat[:48].rstrip() + "…" if len(flat) > 50 else flat) or "New chat"
            self.hub.emit("chats", chats=self.chats.list())
        status = "done"
        try:
            prompt, resume = text, chat.get("session_id")
            retried = False
            while True:
                await self._one_process(run, prompt, resume)
                if run.pending_interrupt:
                    new = run.pending_interrupt
                    run.pending_interrupt = None
                    run.stop_reason = ""
                    self.kill.reset()
                    self._note("Interrupted — following your new instruction.")
                    self._add_item({"role": "user", "text": new})
                    resume = run.session_id or chat.get("session_id")
                    prompt = (
                        f"[The user interrupted what you were doing.] Their new instruction: {new}\n"
                        "Take a fresh screenshot first: the screen may have changed since your last action."
                        if resume
                        else f"Original task: {run.original_prompt}\n[The user interrupted:] {new}\nTake a fresh screenshot first."
                    )
                    run.original_prompt = run.original_prompt if resume else new
                    continue
                if run.resume_failed and not retried:
                    retried = True
                    run.resume_failed = run.failed = ""
                    chat["session_id"] = None
                    prompt = self._context_prompt(chat, text)
                    resume = None
                    continue
                break
            if run.user_stopped:
                status = "stopped"
                self._note(f"Stopped: {run.stop_reason or 'by you'}.")
            elif run.failed:
                status = "error"
        except asyncio.CancelledError:
            status = "stopped"
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("run crashed")
            status = "error"
            self._note(f"Unexpected error: {type(e).__name__}: {e}", "error")
        finally:
            self.tools.end_run()
            self.approvals.cancel_all("run ended")
            self._save(force=True)
            final = run.final_text
            self.run = None
            self.kill.reset()
            self._set_state("idle")
            self.hub.emit("run.finished", chat_id=chat["id"], status=status)
            self.hub.emit("chats", chats=self.chats.list())
            if self.on_finished:
                try:
                    self.on_finished(chat["id"], status, final)
                except Exception:  # noqa: BLE001
                    log.exception("on_finished failed")

    @staticmethod
    def _context_prompt(chat: dict, new_text: str) -> str:
        lines = []
        for it in chat["items"][:-1][-24:]:
            if it.get("role") in ("user", "assistant") and it.get("text"):
                lines.append(f"{it['role'].upper()}: {it['text'][:600]}")
        history = "\n".join(lines)
        return (f"Earlier in this conversation:\n{history}\n\n" if history else "") + f"User's new message: {new_text}"

    # ------------------------------------------------------------------ one CLI process
    async def _one_process(self, run: Run, prompt: str, resume: str | None) -> None:
        chat = run.chat
        info = await asyncio.to_thread(self.cli.info)
        if not info:
            run.failed = "Claude Code is not installed."
            self._note("Claude Code (the `claude` CLI) was not found. Install it and sign in with your Claude Pro account.", "error")
            self.hub.emit("auth.status", **(await asyncio.to_thread(self.cli.auth, True)).to_event())
            return
        auth = await asyncio.to_thread(self.cli.auth)
        if not auth.subscription_ok:
            run.failed = auth.message or "Not signed in."
            self._note(auth.message or "Not signed in with a Claude subscription.", "error")
            self.hub.emit("auth.status", **auth.to_event())
            return

        cfg = self.settings.value
        rt = self.paths.runtime
        mcp_cfg = rt / "mcp.json"
        # Literal URL + token (not ${VAR} placeholders): robust across CLI versions. The token is random per
        # app launch, only valid for this backend process, and the file lives in the user's private AppData.
        write_private(mcp_cfg, json.dumps({"mcpServers": {SERVER_NAME: {"type": "http", "url": self.mcp_url(), "headers": {"Authorization": f"Bearer {self.mcp_token}"}}}}))
        sp_file = rt / "system-prompt.txt"
        sp_file.write_text(SYSTEM_PROMPT, encoding="utf-8")
        allowed = allowed_tool_names() + ["WebSearch", "WebFetch"]
        argv = build_run_argv(info, mcp_config=mcp_cfg, allowed_tools=allowed, system_prompt=SYSTEM_PROMPT, prompt_file=sp_file, model=cfg.model, effort=cfg.effort, resume_id=resume)
        env = sanitized_env()
        log.info("spawning: %s", " ".join(argv[:6]) + " ...")

        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(self.paths.workspace), env=env, **popen_flags())
        except OSError as e:
            run.failed = str(e)
            self._note(f"Could not start Claude Code: {e}", "error")
            return
        run.proc = proc
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[str | None] = asyncio.Queue()
        stderr_lines: list[str] = []

        def put(v: str | None) -> None:
            try:
                loop.call_soon_threadsafe(q.put_nowait, v)
            except RuntimeError:
                pass

        def read_out() -> None:
            assert proc.stdout
            for raw in iter(proc.stdout.readline, b""):
                put(raw.decode("utf-8", "replace"))
            put(None)

        def read_err() -> None:
            assert proc.stderr
            for raw in iter(proc.stderr.readline, b""):
                stderr_lines.append(raw.decode("utf-8", "replace").rstrip())
                del stderr_lines[:-40]

        threading.Thread(target=read_out, daemon=True).start()
        threading.Thread(target=read_err, daemon=True).start()

        def feed() -> None:
            try:
                assert proc.stdin
                proc.stdin.write(prompt.encode("utf-8"))
                proc.stdin.close()
            except OSError:
                pass

        await asyncio.to_thread(feed)
        got_result = False
        try:
            while True:
                line = await q.get()
                if line is None:
                    break
                for ev in parse_stream_line(line):
                    got_result = self._handle(run, ev) or got_result
        finally:
            if proc.poll() is None:
                await asyncio.to_thread(kill_tree, proc.pid)
            rc = await asyncio.to_thread(proc.wait)
            self._stream_item = None
        if chat.get("session_id") != run.session_id and run.session_id:
            chat["session_id"] = run.session_id
        interrupted = bool(run.pending_interrupt) or run.user_stopped
        if not got_result and not interrupted:
            tail = " ".join(stderr_lines[-6:]).strip()
            if "No conversation found" in tail or "No conversation found" in run.failed:
                run.resume_failed = True
                return
            run.failed = tail or f"Claude Code exited unexpectedly (code {rc})."
            self._note(f"Claude Code stopped unexpectedly: {run.failed[:400]}", "error")

    # ------------------------------------------------------------------ event handling
    def _handle(self, run: Run, ev: dict) -> bool:
        kind = ev["kind"]
        if kind == "init":
            run.session_id = ev.get("session_id") or run.session_id
            servers = {s.get("name"): s.get("status") for s in ev.get("mcp_servers", []) if isinstance(s, dict)}
            status = servers.get(SERVER_NAME)
            if status is not None and status != "connected":
                run.failed = f"OS bridge status: {status}"
                self._note(f"Claude Code could not connect to this app's OS-control bridge (status: {status}). {json.dumps(ev.get('errors'))[:300]}", "error")
                self._terminate("mcp not connected")
        elif kind == "text_delta":
            if self._stream_item is None:
                self._stream_item = self._add_item({"role": "assistant", "text": "", "streaming": True})
            self._stream_item["text"] += ev["text"]
            self.hub.emit("item.update", chat_id=run.chat["id"], id=self._stream_item["id"], patch={"text": self._stream_item["text"]})
        elif kind == "assistant_text":
            if self._stream_item is not None:
                self._patch_item(self._stream_item["id"], text=ev["text"], streaming=False)
            else:
                self._add_item({"role": "assistant", "text": ev["text"]})
            self._stream_item = None
            run.final_text = ev["text"]
        elif kind == "tool_use":
            name = ev["name"]
            if not name.startswith(f"mcp__{SERVER_NAME}__"):  # our own tools are reported by OsTools itself
                summary = {"WebSearch": "Web search", "WebFetch": "Fetch web page"}.get(name, name)
                arg = ev["input"].get("query") or ev["input"].get("url") or ""
                item = self._add_item({"role": "action", "tool": name, "summary": f"{summary}: {str(arg)[:80]}", "kind": "web", "status": "running", "input": _clip_args(ev["input"])})
                self._tool_items[ev["id"]] = item["id"]
        elif kind == "tool_result":
            item_id = self._tool_items.pop(ev.get("tool_use_id"), None)
            if item_id:
                self._patch_item(item_id, status="error" if ev["is_error"] else "done", output=ev["text"][:800])
        elif kind == "result":
            run.session_id = ev.get("session_id") or run.session_id
            if not ev["ok"]:
                self._on_result_error(run, ev["text"] or ev.get("subtype") or "error")
            elif ev["text"] and not run.final_text:
                self._add_item({"role": "assistant", "text": ev["text"]})
                run.final_text = ev["text"]
            self.hub.emit("usage", chat_id=run.chat["id"], turns=ev.get("turns"), duration_ms=ev.get("duration_ms"))
            return True
        return False

    def _on_result_error(self, run: Run, text: str) -> None:
        low = text.lower()
        if "no conversation found" in low:
            run.resume_failed = True
            run.failed = text
            return
        run.failed = text
        if "limit" in low and any(w in low for w in ("reached", "resets", "exceeded", "usage")):
            msg = f"You've reached your Claude plan's usage limit: {text}. Wait for the limit to reset, or check your usage at claude.ai/settings/usage."
        elif "login" in low or "authenticat" in low or "401" in low or "oauth" in low:
            self.cli.invalidate()
            msg = f"Claude Code says you need to sign in again: {text}"
        else:
            msg = f"Claude Code reported an error: {text}"
        self._note(msg[:600], "error")
