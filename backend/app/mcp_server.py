"""Minimal MCP (Model Context Protocol) server over streamable HTTP, JSON-response mode.

Claude Code connects here (``--mcp-config``) and calls the OS-control tools. It implements
just what a tool server needs: initialize, ping, tools/list, tools/call and notifications.
Security: bearer token (per launch, handed to the CLI via an env var, never on disk) and a
refusal of any request carrying an ``Origin`` header (i.e. anything coming from a web page).
"""
from __future__ import annotations

import base64
import hmac
import json
import logging
from typing import Any

from fastapi import FastAPI, Request, Response

from . import __version__
from .osagent_tools import SERVER_NAME, TOOL_SPECS, OsTools

log = logging.getLogger("mcp")

SUPPORTED_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
INSTRUCTIONS = (
    "Controls the user's Windows PC (primary monitor). Take a screenshot, then act with click/type_text/"
    "press_keys/scroll or UI Automation tools; verify results on the returned screenshots."
)


def _rpc_error(id_: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


class McpHandler:
    def __init__(self, tools: OsTools) -> None:
        self.tools = tools

    async def handle_message(self, msg: Any) -> dict | None:
        """Return a JSON-RPC response dict, or None for notifications."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return _rpc_error(None, -32600, "Invalid Request")
        method, id_, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        is_notification = "id" not in msg
        if not isinstance(method, str):  # a response from the client; nothing to do
            return None
        if is_notification:
            return None
        try:
            if method == "initialize":
                requested = params.get("protocolVersion")
                version = requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
                result = {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": __version__},
                    "instructions": INSTRUCTIONS,
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOL_SPECS}
            elif method == "tools/call":
                name = params.get("name")
                if not isinstance(name, str):
                    return _rpc_error(id_, -32602, "tools/call requires a tool name")
                out = await self.tools.call(name, params.get("arguments") or {})
                content: list[dict] = [{"type": "text", "text": out.text}]
                if out.shot is not None:
                    content.append({"type": "image", "data": base64.b64encode(out.shot.jpeg).decode("ascii"), "mimeType": "image/jpeg"})
                result = {"content": content, "isError": out.is_error}
            elif method in ("resources/list", "resources/templates/list"):
                result = {"resources": []} if method == "resources/list" else {"resourceTemplates": []}
            elif method == "prompts/list":
                result = {"prompts": []}
            else:
                return _rpc_error(id_, -32601, f"Method not found: {method}")
        except Exception as e:  # pragma: no cover - tools.call already catches its own errors
            log.exception("mcp %s failed", method)
            return _rpc_error(id_, -32603, f"Internal error: {e}")
        return {"jsonrpc": "2.0", "id": id_, "result": result}


def mount_mcp(app: FastAPI, handler: McpHandler, token: str, path: str = "/mcp") -> None:
    expected = f"Bearer {token}".encode()

    def authorized(request: Request) -> bool:
        if request.headers.get("origin") is not None:  # browsers always send Origin on cross-site requests
            return False
        given = request.headers.get("authorization", "").encode()
        return hmac.compare_digest(given, expected)

    @app.post(path)
    async def mcp_post(request: Request) -> Response:
        if not authorized(request):
            return Response("Unauthorized", status_code=401)
        try:
            body = json.loads(await request.body())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Response(json.dumps(_rpc_error(None, -32700, "Parse error")), media_type="application/json", status_code=400)
        if isinstance(body, list):
            replies = [r for r in [await handler.handle_message(m) for m in body] if r is not None]
            if not replies:
                return Response(status_code=202)
            return Response(json.dumps(replies), media_type="application/json")
        reply = await handler.handle_message(body)
        if reply is None:
            return Response(status_code=202)
        return Response(json.dumps(reply), media_type="application/json")

    @app.get(path)
    async def mcp_get(request: Request) -> Response:
        # No server-initiated SSE stream: allowed by the spec via 405.
        return Response("SSE stream not offered", status_code=405, headers={"Allow": "POST"})

    @app.delete(path)
    async def mcp_delete(request: Request) -> Response:
        return Response(status_code=405, headers={"Allow": "POST"})
