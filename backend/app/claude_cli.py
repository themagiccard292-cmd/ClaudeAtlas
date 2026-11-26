"""Integration with the user's own, unmodified `claude` CLI (Claude Code), signed in with Claude Pro.

Design rules (Anthropic's Claude Code legal & compliance terms):
  * Authentication is done by Anthropic's own flow (`claude auth login --claudeai`). This app
    never reads, stores or forwards credentials, cookies or tokens - it only launches the binary.
  * The binary is not modified; we only pass documented command-line flags.
  * API-billing environment variables are removed from the child's environment so runs are
    billed to the Pro subscription, never silently to an API key.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .procutil import kill_tree, popen_flags, run_quiet

log = logging.getLogger("claude_cli")

# Variables that would make Claude Code bill an API account / third-party provider instead of Pro.
STRIP_ENV = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_VERTEX_BASE_URL", "ANTHROPIC_VERTEX_PROJECT_ID", "ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_RESOURCE",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "AWS_BEARER_TOKEN_BEDROCK",
)
SUBSCRIPTION_METHODS = {"claude.ai", "oauth_token"}
INSTALL_COMMAND = "irm https://claude.ai/install.ps1 | iex"
INSTALL_GUIDE_URL = "https://code.claude.com/docs/en/setup"


def sanitized_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    env.setdefault("NO_COLOR", "1")
    env["CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT"] = "3600000"  # approvals / pauses can outlast the 5-minute default
    env.update(extra or {})
    return env


def find_claude(custom: str = "") -> str | None:
    if custom:
        return custom if Path(custom).is_file() else None
    found = shutil.which("claude")
    if found:
        return found
    cands: list[Path] = []
    home = Path.home()
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        cands = [home / ".local" / "bin" / "claude.exe", local / "Programs" / "claude" / "claude.exe", local / "AnthropicClaude" / "claude.exe", appdata / "npm" / "claude.cmd", home / ".claude" / "local" / "claude.exe"]
    else:
        cands = [home / ".local" / "bin" / "claude", Path("/usr/local/bin/claude"), home / ".claude" / "local" / "claude"]
    return next((str(c) for c in cands if c.is_file()), None)


@dataclass
class CliInfo:
    path: str
    version: str
    flags: frozenset[str]
    help_text: str = ""

    def has(self, flag: str) -> bool:
        return flag in self.flags


def probe(path: str) -> CliInfo | None:
    try:
        ver = run_quiet([path, "--version"], timeout=25, env=sanitized_env())
        help_ = run_quiet([path, "--help"], timeout=25, env=sanitized_env())
    except (OSError, Exception) as e:  # noqa: BLE001
        log.warning("probe failed for %s: %s", path, e)
        return None
    text = (help_.stdout or b"").decode("utf-8", "replace")
    found = set(re.findall(r"--[a-zA-Z][a-zA-Z0-9-]*", text))
    # help sometimes abbreviates pairs as "--append-system-prompt[-file]"
    found |= {m + "-file" for m in re.findall(r"(--[a-zA-Z][a-zA-Z0-9-]*)\[-file\]", text)}
    flags = frozenset(found)
    return CliInfo(path=path, version=(ver.stdout or b"").decode("utf-8", "replace").strip().splitlines()[0] if ver.stdout else "unknown", flags=flags, help_text=text)


# ----------------------------------------------------------------------------------
# Auth
# ----------------------------------------------------------------------------------
@dataclass
class AuthInfo:
    cli_found: bool = False
    path: str = ""
    version: str = ""
    logged_in: bool = False
    method: str = ""
    provider: str = ""
    email: str = ""
    plan: str = ""
    subscription_ok: bool = False
    message: str = ""

    def to_event(self) -> dict:
        d = {k: getattr(self, k) for k in ("cli_found", "path", "version", "logged_in", "method", "provider", "email", "plan", "subscription_ok", "message")}
        d["install_command"] = INSTALL_COMMAND
        d["install_guide_url"] = INSTALL_GUIDE_URL
        return d


def parse_auth_status(raw: str) -> AuthInfo:
    """Interpret the JSON printed by `claude auth status` (defensive: field names vary by version)."""
    a = AuthInfo(cli_found=True)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        a.message = "Could not read `claude auth status` output."
        return a
    a.logged_in = bool(data.get("loggedIn"))
    a.method = str(data.get("authMethod") or "")
    a.provider = str(data.get("apiProvider") or "")
    acct = data.get("account") if isinstance(data.get("account"), dict) else {}
    a.email = str(data.get("email") or data.get("userEmail") or acct.get("email") or "")
    plan = data.get("subscriptionType") or data.get("subscription") or data.get("plan") or data.get("planType") or acct.get("subscriptionType") or ""
    a.plan = str(plan).replace("_", " ").title() if plan else ""
    first_party = a.provider in ("", "firstParty")
    a.subscription_ok = a.logged_in and a.method in SUBSCRIPTION_METHODS and first_party
    if not a.logged_in:
        a.message = "Not signed in."
    elif not a.subscription_ok:
        a.message = (
            f"Claude Code is using '{a.method or 'unknown'}' billing"
            + (f" via {a.provider}" if a.provider and a.provider != "firstParty" else "")
            + ", not a Claude subscription. Sign in with your Claude Pro account to continue."
        )
    return a


class CliManager:
    """Caches CLI discovery/probing and exposes auth operations (all blocking; call from threads)."""

    def __init__(self, custom_path: Callable[[], str]) -> None:
        self._custom = custom_path
        self._info: CliInfo | None = None
        self._info_key: tuple | None = None
        self._login: LoginFlow | None = None
        self._auth_cache: tuple[float, AuthInfo] | None = None

    def info(self, force: bool = False) -> CliInfo | None:
        path = find_claude(self._custom())
        if not path:
            self._info = None
            return None
        try:
            key = (path, os.stat(path).st_mtime)
        except OSError:
            key = (path, 0)
        if force or self._info is None or self._info_key != key:
            self._info = probe(path)
            self._info_key = key
        return self._info

    def auth(self, force: bool = False, max_age: float = 45.0) -> AuthInfo:
        if not force and self._auth_cache and time.monotonic() - self._auth_cache[0] < max_age and self._auth_cache[1].cli_found:
            return self._auth_cache[1]
        info = self.info()
        if not info:
            a = AuthInfo(cli_found=False, message="Claude Code is not installed.")
            self._auth_cache = None
            return a
        try:
            cp = run_quiet([info.path, "auth", "status", "--json"], timeout=30, env=sanitized_env())
            a = parse_auth_status((cp.stdout or b"").decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            a = AuthInfo(cli_found=True, message=f"`claude auth status` failed: {e}")
        a.path, a.version = info.path, info.version
        self._auth_cache = (time.monotonic(), a)
        return a

    def invalidate(self) -> None:
        self._auth_cache = None

    # -- login / logout ----------------------------------------------------------------
    def start_login(self, on_url: Callable[[str], None], on_done: Callable[[bool, str], None]) -> bool:
        info = self.info()
        if not info or (self._login and self._login.running):
            return False
        self._login = LoginFlow(info.path, on_url, on_done)
        self._login.start()
        return True

    def cancel_login(self) -> None:
        if self._login:
            self._login.cancel()

    def logout(self) -> None:
        info = self.info()
        if info:
            run_quiet([info.path, "auth", "logout"], timeout=30, env=sanitized_env())
        self.invalidate()


class LoginFlow:
    """`claude auth login --claudeai`: Anthropic's own flow; opens the system browser itself.

    We only relay the printed sign-in URL (as a fallback if the browser does not open) and
    report when the process finishes. We never see the resulting credentials.
    """

    _URL = re.compile(r"https?://[^\s\"'<>]+")

    def __init__(self, path: str, on_url: Callable[[str], None], on_done: Callable[[bool, str], None]) -> None:
        self.path, self.on_url, self.on_done = path, on_url, on_done
        self.proc = None
        self.running = False
        self._cancelled = False

    def start(self) -> None:
        import subprocess

        self.proc = subprocess.Popen(
            [self.path, "auth", "login", "--claudeai"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=sanitized_env(), **popen_flags(),
        )
        self.running = True
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc and self.proc.stdout
        tail: list[str] = []
        seen: set[str] = set()
        for raw in iter(self.proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            tail = (tail + [line])[-8:]
            for m in self._URL.findall(line):
                if m not in seen:
                    seen.add(m)
                    self.on_url(m.rstrip(".,)"))
        rc = self.proc.wait()
        self.running = False
        ok = rc == 0 and not self._cancelled
        self.on_done(ok, "" if ok else ("Sign-in cancelled." if self._cancelled else " ".join(tail)[-300:] or f"exit code {rc}"))

    def cancel(self) -> None:
        self._cancelled = True
        if self.proc and self.proc.poll() is None:
            kill_tree(self.proc.pid)


# ----------------------------------------------------------------------------------
# Run command line
# ----------------------------------------------------------------------------------
def _cmd_safe(text: str) -> str:
    """One line, without characters cmd.exe treats specially (matters only if `claude` is a .cmd shim)."""
    return " ".join(re.sub(r'["%&|^<>]', " ", text).split())


def _choice_supported(info: CliInfo, value: str) -> bool:
    return value in info.help_text


def build_run_argv(
    info: CliInfo,
    *,
    mcp_config: Path,
    allowed_tools: list[str],
    system_prompt: str,
    prompt_file: Path | None,
    model: str = "",
    effort: str = "",
    resume_id: str | None = None,
) -> list[str]:
    """Headless run. The user prompt is written to stdin (never on the command line: avoids
    cmd.exe quoting problems when `claude` is an npm `.cmd` shim, and the 32k-char limit)."""
    argv = [info.path, "-p", "--output-format", "stream-json", "--verbose"]
    if info.has("--include-partial-messages"):
        argv.append("--include-partial-messages")
    argv += ["--mcp-config", str(mcp_config)]
    if info.has("--strict-mcp-config"):
        argv.append("--strict-mcp-config")
    argv += ["--allowedTools", ",".join(allowed_tools)]
    if info.has("--tools"):
        argv += ["--tools", "WebSearch,WebFetch"]  # built-in tools limited to read-only web lookups; MCP tools are unaffected
    if info.has("--permission-mode") and _choice_supported(info, "dontAsk"):
        argv += ["--permission-mode", "dontAsk"]  # nothing outside the allow-list can hang waiting for a prompt
    if info.has("--permission-prompts"):
        argv += ["--permission-prompts", "none"]
    if info.has("--disable-slash-commands"):
        argv.append("--disable-slash-commands")
    if prompt_file is not None and info.has("--append-system-prompt-file"):
        argv += ["--append-system-prompt-file", str(prompt_file)]
    elif info.has("--append-system-prompt"):
        argv += ["--append-system-prompt", _cmd_safe(system_prompt)]
    if model:
        argv += ["--model", model]
    if effort and info.has("--effort"):
        argv += ["--effort", effort]
    if resume_id:
        argv += ["--resume", resume_id]
    return argv


# ----------------------------------------------------------------------------------
# stream-json parsing
# ----------------------------------------------------------------------------------
def _blocks(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in (content or []) if isinstance(b, dict)]


def _tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for b in _blocks(content):
        if b.get("type") == "text":
            parts.append(b.get("text", ""))
        elif b.get("type") == "image":
            parts.append("[image]")
    return "\n".join(parts)


def parse_stream_line(line: str) -> list[dict]:
    """One stdout line of `--output-format stream-json` -> zero or more normalised events."""
    line = line.strip()
    if not line.startswith("{"):
        return []
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return []
    t = obj.get("type")
    if obj.get("parent_tool_use_id"):  # sub-agent chatter: ignore
        return []
    out: list[dict] = []
    if t == "system" and obj.get("subtype") == "init":
        out.append({"kind": "init", "session_id": obj.get("session_id"), "mcp_servers": obj.get("mcp_servers") or [], "model": obj.get("model"), "errors": obj.get("mcp_server_errors") or []})
    elif t == "stream_event":
        ev = obj.get("event") or {}
        delta = ev.get("delta") or {}
        if delta.get("type") == "text_delta" and delta.get("text"):
            out.append({"kind": "text_delta", "text": delta["text"]})
    elif t == "assistant":
        msg = obj.get("message") or {}
        text = "".join(b.get("text", "") for b in _blocks(msg.get("content")) if b.get("type") == "text").strip()
        if text:
            out.append({"kind": "assistant_text", "text": text})
        for b in _blocks(msg.get("content")):
            if b.get("type") == "tool_use":
                out.append({"kind": "tool_use", "id": b.get("id"), "name": b.get("name", ""), "input": b.get("input") or {}})
    elif t == "user":
        for b in _blocks((obj.get("message") or {}).get("content")):
            if b.get("type") == "tool_result":
                out.append({"kind": "tool_result", "tool_use_id": b.get("tool_use_id"), "is_error": bool(b.get("is_error")), "text": _tool_result_text(b.get("content"))[:1500]})
    elif t == "result":
        is_err = bool(obj.get("is_error")) or (obj.get("subtype") not in (None, "success"))
        out.append({"kind": "result", "ok": not is_err, "text": str(obj.get("result") or ""), "session_id": obj.get("session_id"), "turns": obj.get("num_turns"), "duration_ms": obj.get("duration_ms"), "subtype": obj.get("subtype")})
    return out
