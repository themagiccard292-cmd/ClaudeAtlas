#!/usr/bin/env python3
"""A stand-in for the `claude` CLI used by the test-suite.

It honours the same flags the app passes, talks to the app's MCP endpoint over real HTTP
(exactly what Claude Code does), and prints stream-json lines. Behaviour is scripted through
environment variables:

  FAKE_CLAUDE_AUTH    JSON for `auth status` (default: signed in with claude.ai)
  FAKE_CLAUDE_SCRIPT  JSON list of steps: {"say": str} | {"tool": name, "args": {...}} | {"sleep": seconds}
  FAKE_CLAUDE_LOG     file to append one JSON record per invocation (argv, prompt, env flags)
"""
import json
import os
import sys
import time
import urllib.request

HELP = """Usage: claude [options] [command] [prompt]
  -p, --print
  --output-format <format>
  --include-partial-messages
  --verbose
  --mcp-config <configs...>
  --strict-mcp-config
  --allowedTools, --allowed-tools <tools...>
  --tools <tools...>
  --permission-mode <mode>   (choices: "acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan")
  --permission-prompts <target>
  --disable-slash-commands
  --append-system-prompt <prompt>
  --append-system-prompt-file <file>
  --model <model>
  --effort <level>
  -r, --resume [value]
"""


def log(rec):
    p = os.environ.get("FAKE_CLAUDE_LOG")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def rpc(url, token, method, params=None, id_=1):
    body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if id_ is not None:
        body["id"] = id_
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
        return json.loads(data) if data else None


def main(argv):
    if argv[:1] == ["--version"]:
        print("9.9.9 (Fake Claude Code)")
        return 0
    if argv[:1] == ["--help"]:
        print(HELP)
        return 0
    if argv[:2] == ["auth", "status"]:
        print(os.environ.get("FAKE_CLAUDE_AUTH") or json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "email": "me@example.com", "subscriptionType": "pro"}))
        return 0
    if argv[:2] == ["auth", "login"]:
        print("Opening browser to sign in...")
        print("If the browser did not open, visit: https://claude.ai/oauth/authorize?code=true&client_id=fake")
        sys.stdout.flush()
        time.sleep(0.4)
        return 0
    if argv[:2] == ["auth", "logout"]:
        return 0

    prompt = sys.stdin.read()
    resume = argv[argv.index("--resume") + 1] if "--resume" in argv else None
    log({"argv": argv, "prompt": prompt, "resume": resume, "has_api_key": "ANTHROPIC_API_KEY" in os.environ, "cwd": os.getcwd()})
    cfg = json.load(open(argv[argv.index("--mcp-config") + 1], encoding="utf-8"))
    srv = cfg["mcpServers"]["osagent"]
    expand = lambda s: s.replace("${OSAGENT_MCP_URL}", os.environ.get("OSAGENT_MCP_URL", "")).replace("${OSAGENT_MCP_TOKEN}", os.environ.get("OSAGENT_MCP_TOKEN", ""))  # noqa: E731
    url = expand(srv["url"])
    token = expand(srv["headers"]["Authorization"]).split(" ", 1)[1]
    session_id = resume or "sess-" + str(int(time.time() * 1000))

    status = "connected"
    try:
        rpc(url, token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fake-claude", "version": "0"}})
        rpc(url, token, "notifications/initialized", id_=None)
        tools = rpc(url, token, "tools/list", id_=2)["result"]["tools"]
    except Exception as e:  # noqa: BLE001
        status = f"failed: {e}"
        tools = []
    emit({"type": "system", "subtype": "init", "session_id": session_id, "model": "fake", "mcp_servers": [{"name": "osagent", "status": status}], "tools": [t["name"] for t in tools]})

    steps = json.loads(os.environ.get("FAKE_CLAUDE_SCRIPT") or "[]")
    n = 0
    last_text = ""
    for step in steps:
        if (step.get("when") == "fresh" and resume) or (step.get("when") == "resumed" and not resume):
            continue
        if "say" in step:
            last_text = step["say"]
            emit({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": step["say"]}}})
            emit({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": step["say"]}]}, "session_id": session_id, "parent_tool_use_id": None})
        elif "sleep" in step:
            time.sleep(step["sleep"])
        elif "tool" in step:
            n += 1
            tid = f"toolu_{n}"
            name = step["tool"]
            full = name if "__" in name else f"mcp__osagent__{name}"
            emit({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": tid, "name": full, "input": step.get("args", {})}]}, "session_id": session_id, "parent_tool_use_id": None})
            res = rpc(url, token, "tools/call", {"name": name, "arguments": step.get("args", {})}, id_=100 + n)
            r = res["result"]
            emit({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "is_error": r.get("isError", False), "content": r["content"]}]}, "session_id": session_id, "parent_tool_use_id": None})
            (os.environ.get("FAKE_CLAUDE_TOOLLOG") and open(os.environ["FAKE_CLAUDE_TOOLLOG"], "a").write(json.dumps({"tool": name, "text": r["content"][0]["text"], "is_error": r.get("isError", False), "images": sum(1 for c in r["content"] if c["type"] == "image")}) + "\n"))
    emit({"type": "result", "subtype": "success", "is_error": False, "result": last_text, "session_id": session_id, "num_turns": n + 1, "duration_ms": 123})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
