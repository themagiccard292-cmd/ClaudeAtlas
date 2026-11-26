"""End-to-end: real HTTP/WebSocket server + fake `claude` CLI that calls the real MCP endpoint."""
from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request

import psutil
import pytest
import websockets
from websockets.asyncio.client import connect

from app.osagent_tools import TOOL_SPECS, allowed_tool_names


class Ws:
    def __init__(self, ws):
        self.ws = ws
        self.events: list[dict] = []
        self._task = asyncio.create_task(self._pump())

    async def _pump(self):
        try:
            async for raw in self.ws:
                self.events.append(json.loads(raw))
        except websockets.ConnectionClosed:
            pass

    async def send(self, **msg):
        await self.ws.send(json.dumps(msg))

    async def wait(self, pred, timeout=15.0, start=0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for i, e in enumerate(self.events[start:], start):
                if pred(e):
                    return e
            await asyncio.sleep(0.02)
        raise AssertionError(f"timed out; last events: {[ (e['type'], e.get('state') or e.get('status')) for e in self.events[-12:]]}")

    def of(self, type_):
        return [e for e in self.events if e["type"] == type_]

    async def close(self):
        self._task.cancel()
        await self.ws.close()


@pytest.fixture
async def client(live_backend):
    ws = Ws(await connect(live_backend.url, origin=None))
    await ws.send(type="hello")
    await ws.wait(lambda e: e["type"] == "auth.status")
    yield ws, live_backend
    await ws.close()


def items(ws: Ws, role=None):
    """Final state of every transcript item (item + item.update patches applied in order)."""
    out: dict[str, dict] = {}
    order: list[str] = []
    for e in ws.events:
        if e["type"] == "item":
            out[e["item"]["id"]] = dict(e["item"])
            order.append(e["item"]["id"])
        elif e["type"] == "item.update" and e["id"] in out:
            out[e["id"]].update(e["patch"])
    res = [out[i] for i in order]
    return [i for i in res if role is None or i["role"] == role]


def script(monkeypatch, steps, auth=None):
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", json.dumps(steps))
    if auth is not None:
        monkeypatch.setenv("FAKE_CLAUDE_AUTH", json.dumps(auth))


def tool_log(lb):
    p = lb.tmp / "tool-calls.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def cli_log(lb):
    p = lb.tmp / "claude-calls.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def mcp(lb, body, token="m" * 32, headers=None):
    h = {"Content-Type": "application/json", "Authorization": f"Bearer {token}", **(headers or {})}
    req = urllib.request.Request(f"http://127.0.0.1:{lb.port}/mcp", data=json.dumps(body).encode(), headers=h, method="POST")
    return urllib.request.urlopen(req, timeout=10)


# --------------------------------------------------------------------------- connection / security
async def test_websocket_rejects_bad_token_and_foreign_origin(live_backend):
    for url, origin in [(f"ws://127.0.0.1:{live_backend.port}/ws?token=wrong", None), (live_backend.url, "https://evil.example")]:
        try:
            async with connect(url, origin=origin) as ws:
                await ws.send(json.dumps({"type": "hello"}))
                await asyncio.wait_for(ws.recv(), 2)
            pytest.fail("connection should have been refused")
        except (websockets.ConnectionClosed, websockets.InvalidStatus, asyncio.TimeoutError):
            pass


async def test_hello_reports_subscription_login(client):
    ws, lb = client
    ready = ws.of("ready")[0]
    assert ready["screen"]["width"] == 1920 and ready["screen"]["shot_width"] == 1280 and ready["uia"] is True
    auth = ws.of("auth.status")[-1]
    assert auth["subscription_ok"] and auth["plan"] == "Pro" and auth["email"] == "me@example.com" and auth["cli_found"]
    assert ws.of("settings")[0]["settings"]["approval_mode"] == "smart"


async def test_mcp_endpoint_security_and_protocol(live_backend):
    lb = live_backend
    for kwargs in [dict(token="wrong"), dict(headers={"Origin": "https://evil.example"})]:
        with pytest.raises(urllib.error.HTTPError) as e:
            mcp(lb, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, **kwargs)
        assert e.value.code == 401
    init = json.loads(mcp(lb, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}).read())
    assert init["result"]["protocolVersion"] == "2025-06-18" and init["result"]["serverInfo"]["name"] == "osagent"
    assert mcp(lb, {"jsonrpc": "2.0", "method": "notifications/initialized"}).status == 202
    tools = json.loads(mcp(lb, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).read())["result"]["tools"]
    assert {t["name"] for t in tools} >= {"screenshot", "click", "powershell", "request_approval", "ui_snapshot"}
    assert all(t["inputSchema"]["type"] == "object" for t in tools)
    unknown = json.loads(mcp(lb, {"jsonrpc": "2.0", "id": 3, "method": "nope"}).read())
    assert unknown["error"]["code"] == -32601
    # tool calls are refused outside an agent run
    res = json.loads(mcp(lb, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "screenshot", "arguments": {}}}).read())["result"]
    assert res["isError"] and "No agent run is active" in res["content"][0]["text"]
    assert lb.drv.calls == []  # and nothing touched the (fake) desktop
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{lb.port}/mcp", headers={"Authorization": "Bearer " + "m" * 32}), timeout=5)
    assert e.value.code == 405


def test_allowed_tool_names_cover_every_tool():
    assert set(allowed_tool_names()) == {f"mcp__osagent__{t['name']}" for t in TOOL_SPECS}


# --------------------------------------------------------------------------- happy path
async def test_full_run_screenshot_click_powershell(client, monkeypatch):
    ws, lb = client
    script(monkeypatch, [
        {"say": "I'll take a look."},
        {"tool": "screenshot"},
        {"tool": "click", "args": {"x": 640, "y": 360}},          # model centre -> physical (960, 540)
        {"tool": "type_text", "args": {"text": "héllo"}},
        {"tool": "press_keys", "args": {"keys": "ctrl+s"}},
        {"tool": "ui_snapshot"},
        {"tool": "powershell", "args": {"command": "echo from-shell"}},
        {"say": "All done."},
    ])
    await ws.send(type="chat.send", text="do the thing")
    fin = await ws.wait(lambda e: e["type"] == "run.finished", 30)
    assert fin["status"] == "done"

    # OS side effects, with coordinate mapping from screenshot space to physical pixels
    assert ("click", 960, 540, "left", 1) in lb.drv.calls
    assert "".join(c[1] for c in lb.drv.of("type")) == "héllo"
    assert [c[1] for c in lb.drv.of("key_down")][-2:] == ["ctrl", "s"]

    # what Claude received back: text + a screenshot image after state-changing actions
    tl = tool_log(lb)
    assert [t["tool"] for t in tl] == ["screenshot", "click", "type_text", "press_keys", "ui_snapshot", "powershell"]
    assert tl[0]["images"] == 1 and tl[1]["images"] == 1 and "Screenshot 1280x720" in tl[1]["text"]
    assert tl[4]["images"] == 0 and "[1] Button" in tl[4]["text"]
    assert "from-shell" in tl[5]["text"] and "[exit code 0]" in tl[5]["text"]

    # transcript shown in the UI
    tr = items(ws)
    assert [i["role"] for i in tr][:2] == ["user", "assistant"] and tr[1]["text"] == "I'll take a look."
    actions = [i for i in tr if i["role"] == "action"]
    assert [a["summary"] for a in actions][:3] == ["Take screenshot", "Click (640, 360)", 'Type "héllo"']
    assert all(a["status"] == "done" for a in actions)
    assert actions[1]["coord"] == [640, 360] and actions[1]["shot_size"] == [1280, 720] and actions[1]["shot"]
    assert "from-shell" in actions[-1]["detail"]
    assert tr[-1]["role"] == "assistant" and tr[-1]["text"] == "All done."
    assert tr[1].get("streaming") is False

    # persisted + resumable session, screenshot thumbnails served with the token only
    chat_id = ws.of("chat.opened")[0]["chat"]["id"]
    saved = lb.svc.chats.get(chat_id)
    assert saved["session_id"].startswith("sess-") and saved["title"] == "do the thing"
    url = f"http://127.0.0.1:{lb.port}/shots/{actions[1]['shot']}"
    assert urllib.request.urlopen(url + f"?token={lb.ws_token}", timeout=5).headers["Content-Type"] == "image/jpeg"
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(url, timeout=5)
    assert e.value.code == 401
    assert lb.svc.runner.state == "idle" and not lb.svc.tools.active


async def test_follow_up_message_resumes_the_same_claude_session(client, monkeypatch):
    ws, lb = client
    script(monkeypatch, [{"say": "ok"}])
    await ws.send(type="chat.send", text="first")
    await ws.wait(lambda e: e["type"] == "run.finished")
    chat_id = ws.of("chat.opened")[0]["chat"]["id"]
    n = len(ws.events)
    await ws.send(type="chat.send", chat_id=chat_id, text="second")
    await ws.wait(lambda e: e["type"] == "run.finished", start=n)
    calls = cli_log(lb)
    assert calls[0]["resume"] is None and calls[1]["resume"] == lb.svc.chats.get(chat_id)["session_id"]
    assert "--resume" in calls[1]["argv"] and calls[1]["prompt"] == "second"


async def test_cli_invocation_is_subscription_safe(client, monkeypatch):
    ws, lb = client
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-never-reach-the-cli")
    script(monkeypatch, [{"say": "hi"}])
    await ws.send(type="chat.send", text="hello")
    await ws.wait(lambda e: e["type"] == "run.finished")
    call = cli_log(lb)[0]
    assert call["has_api_key"] is False  # stripped from the child's environment
    a = call["argv"]
    assert "-p" in a and a[a.index("--output-format") + 1] == "stream-json" and "--strict-mcp-config" in a
    assert not any("sk-ant" in x for x in a)
    allowed = a[a.index("--allowedTools") + 1].split(",")
    assert "mcp__osagent__click" in allowed and "WebSearch" in allowed and "Bash" not in allowed
    assert a[a.index("--tools") + 1] == "WebSearch,WebFetch" and a[a.index("--permission-mode") + 1] == "dontAsk"
    assert call["cwd"].endswith("workspace")
    cfg_path = lb.tmp / "data" / "runtime" / "mcp.json"
    cfg = json.loads(cfg_path.read_text())["mcpServers"]["osagent"]
    assert cfg["type"] == "http" and cfg["url"] == f"http://127.0.0.1:{lb.port}/mcp" and cfg["headers"]["Authorization"] == "Bearer " + "m" * 32
    assert oct(cfg_path.stat().st_mode & 0o777) == "0o600"  # private to the current user
    assert not any("m" * 32 in x for x in a)  # and never on the command line (visible in process lists)


# --------------------------------------------------------------------------- not signed in
async def test_run_is_refused_when_not_signed_in_with_a_subscription(client, monkeypatch):
    ws, lb = client
    script(monkeypatch, [{"say": "should not run"}], auth={"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty"})
    lb.svc.cli.invalidate()
    await ws.send(type="chat.send", text="hi")
    fin = await ws.wait(lambda e: e["type"] == "run.finished")
    assert fin["status"] == "error"
    notes = items(ws, "system")
    assert notes and "Pro" in notes[0]["text"] and notes[0]["level"] == "error"
    assert cli_log(lb) == []  # the CLI was never launched for a run
    assert ws.of("auth.status")[-1]["subscription_ok"] is False


async def test_login_flow_relays_url_and_refreshes_status(client, monkeypatch):
    ws, lb = client
    n = len(ws.events)
    await ws.send(type="auth.login")
    url = await ws.wait(lambda e: e["type"] == "auth.login.url", start=n)
    assert url["url"].startswith("https://claude.ai/oauth/authorize")
    done = await ws.wait(lambda e: e["type"] == "auth.login.done", start=n)
    assert done["ok"] is True
    await ws.wait(lambda e: e["type"] == "auth.status" and e is not None, start=n)


async def test_missing_cli_explains_how_to_install(live_backend):
    lb = live_backend
    lb.svc.settings.update({"claude_path": "/nonexistent/claude"})
    ws = Ws(await connect(lb.url, origin=None))
    await ws.send(type="hello")
    st = await ws.wait(lambda e: e["type"] == "auth.status")
    assert st["cli_found"] is False and "claude.ai/install.ps1" in st["install_command"]
    await ws.close()


# --------------------------------------------------------------------------- approvals
async def test_request_approval_approved_and_denied(client, monkeypatch):
    ws, lb = client
    script(monkeypatch, [{"tool": "request_approval", "args": {"action": "Delete the old reports", "category": "delete"}}, {"say": "finished"}])
    await ws.send(type="chat.send", text="clean up")
    req = await ws.wait(lambda e: e["type"] == "approval.request")
    assert req["kind"] == "delete" and "Delete the old reports" in req["summary"]
    await ws.send(type="approval.respond", id=req["id"], approved=True)
    await ws.wait(lambda e: e["type"] == "run.finished")
    assert tool_log(lb)[-1]["text"].startswith("APPROVED")

    (lb.tmp / "tool-calls.jsonl").unlink()
    n = len(ws.events)
    await ws.send(type="chat.send", chat_id=ws.of("chat.opened")[0]["chat"]["id"], text="again")
    req = await ws.wait(lambda e: e["type"] == "approval.request", start=n)
    await ws.send(type="approval.respond", id=req["id"], approved=False)
    await ws.wait(lambda e: e["type"] == "run.finished", start=n)
    assert tool_log(lb)[-1]["text"].startswith("DENIED")


async def test_risky_powershell_needs_approval_and_denial_prevents_execution(client, monkeypatch, tmp_path):
    ws, lb = client
    victim = tmp_path / "precious.txt"
    victim.write_text("keep me")
    script(monkeypatch, [{"tool": "powershell", "args": {"command": f"rm -f {victim}"}}, {"say": "tried"}])
    await ws.send(type="chat.send", text="delete my file")
    req = await ws.wait(lambda e: e["type"] == "approval.request")
    assert req["kind"] == "powershell" and "deletes files" in req["details"] and str(victim) in req["details"]
    await ws.send(type="approval.respond", id=req["id"], approved=False)
    await ws.wait(lambda e: e["type"] == "run.finished")
    assert victim.exists()  # NOT deleted
    assert "DENIED" in tool_log(lb)[-1]["text"] and tool_log(lb)[-1]["is_error"]

    # approved -> runs
    n = len(ws.events)
    await ws.send(type="chat.send", chat_id=ws.of("chat.opened")[0]["chat"]["id"], text="yes really")
    req = await ws.wait(lambda e: e["type"] == "approval.request", start=n)
    await ws.send(type="approval.respond", id=req["id"], approved=True)
    await ws.wait(lambda e: e["type"] == "run.finished", start=n)
    assert not victim.exists()


async def test_smart_mode_guards_destructive_gui_clicks(client, monkeypatch):
    ws, lb = client
    # fake UIA: a "Delete" button sits at physical (200..280, 100..130) -> model coords scaled by 2/3
    script(monkeypatch, [{"tool": "click", "args": {"x": 160, "y": 77}}, {"say": "x"}])
    await ws.send(type="chat.send", text="click it")
    req = await ws.wait(lambda e: e["type"] == "approval.request")
    assert req["kind"] == "gui" and "Delete" in req["summary"]
    assert lb.drv.of("click") == []  # nothing clicked yet
    await ws.send(type="approval.respond", id=req["id"], approved=False)
    await ws.wait(lambda e: e["type"] == "run.finished")
    assert lb.drv.of("click") == [] and "DENIED" in tool_log(lb)[-1]["text"]


async def test_never_mode_skips_prompts(client, monkeypatch):
    ws, lb = client
    await ws.send(type="settings.set", patch={"approval_mode": "never"})
    script(monkeypatch, [{"tool": "request_approval", "args": {"action": "x"}}])
    await ws.send(type="chat.send", text="go")
    await ws.wait(lambda e: e["type"] == "run.finished")
    assert ws.of("approval.request") == [] and "APPROVED" in tool_log(lb)[-1]["text"]


# --------------------------------------------------------------------------- stop / kill / interrupt
def fake_claude_processes():
    out = []
    for p in psutil.process_iter(["cmdline"]):
        try:
            if any("fake_claude.py" in (c or "") for c in (p.info["cmdline"] or [])):
                out.append(p)
        except psutil.Error:
            pass
    return out


async def test_stop_button_kills_the_cli_and_halts_input(client, monkeypatch):
    ws, lb = client
    script(monkeypatch, [{"say": "working"}, {"sleep": 60}])
    await ws.send(type="chat.send", text="long task")
    await ws.wait(lambda e: e["type"] == "state" and e["state"] == "running")
    await ws.wait(lambda e: e["type"] == "item" and e["item"]["role"] == "assistant")
    assert fake_claude_processes()
    t0 = time.time()
    await ws.send(type="agent.stop")
    kill = await ws.wait(lambda e: e["type"] == "kill")
    fin = await ws.wait(lambda e: e["type"] == "run.finished")
    assert fin["status"] == "stopped" and time.time() - t0 < 6 and "Stopped by you" in kill["reason"]
    await asyncio.sleep(0.3)
    assert not fake_claude_processes()
    assert any("Stopped" in i["text"] for i in items(ws, "system"))
    assert lb.svc.runner.state == "idle" and not lb.svc.kill.halted  # re-armed for the next run


async def test_hotkey_kill_switch_stops_run(client, monkeypatch):
    ws, lb = client
    script(monkeypatch, [{"say": "working"}, {"sleep": 60}])
    await ws.send(type="chat.send", text="long task")
    await ws.wait(lambda e: e["type"] == "item" and e["item"]["role"] == "assistant")
    lb.svc.kill.trigger("Esc Esc")  # what the global hotkey thread does
    fin = await ws.wait(lambda e: e["type"] == "run.finished")
    assert fin["status"] == "stopped" and ws.of("kill")[0]["reason"] == "Esc Esc"


async def test_voice_or_typed_interruption_resumes_session_with_new_instruction(client, monkeypatch):
    ws, lb = client
    script(monkeypatch, [{"say": "starting", "when": "fresh"}, {"sleep": 60, "when": "fresh"}, {"say": "switching plans", "when": "resumed"}])
    await ws.send(type="chat.send", text="open notepad")
    await ws.wait(lambda e: e["type"] == "item" and e["item"]["role"] == "assistant")
    await ws.send(type="voice.speech_start")  # user starts talking: execution pauses immediately
    await ws.wait(lambda e: e["type"] == "state" and e["state"] == "paused")
    n = len(ws.events)
    await ws.send(type="chat.send", text="actually open the calculator")  # same path a transcribed utterance takes
    fin = await ws.wait(lambda e: e["type"] == "run.finished", start=n)
    assert fin["status"] == "done"
    calls = cli_log(lb)
    assert len(calls) == 2
    assert calls[1]["resume"] and calls[1]["resume"] == calls[0].get("resume") or calls[1]["resume"].startswith("sess-")
    assert "actually open the calculator" in calls[1]["prompt"] and "interrupted" in calls[1]["prompt"].lower()
    tr = items(ws)
    assert [i["text"] for i in tr if i["role"] == "user"] == ["open notepad", "actually open the calculator"]
    assert tr[-1]["text"] == "switching plans"
    assert not fake_claude_processes()


async def test_speech_cancel_resumes_a_paused_run(client, monkeypatch):
    ws, lb = client
    script(monkeypatch, [{"say": "a"}, {"sleep": 1.5}, {"say": "b"}])
    await ws.send(type="chat.send", text="go")
    await ws.wait(lambda e: e["type"] == "item" and e["item"]["role"] == "assistant")
    await ws.send(type="voice.speech_start")
    await ws.wait(lambda e: e["type"] == "state" and e["state"] == "paused")
    n = len(ws.events)
    await ws.send(type="voice.speech_cancel")  # it was just a cough
    await ws.wait(lambda e: e["type"] == "state" and e["state"] == "running", start=n)  # resumed, same process
    fin = await ws.wait(lambda e: e["type"] == "run.finished", start=n)
    assert fin["status"] == "done" and len(cli_log(lb)) == 1


async def test_paused_run_blocks_os_actions_until_resumed(client, monkeypatch):
    ws, lb = client
    script(monkeypatch, [{"sleep": 0.6}, {"tool": "click", "args": {"x": 10, "y": 10}}])
    await ws.send(type="chat.send", text="click soon")
    await ws.wait(lambda e: e["type"] == "state" and e["state"] == "running")
    await ws.send(type="agent.pause")
    await ws.wait(lambda e: e["type"] == "state" and e["state"] == "paused")
    await asyncio.sleep(1.5)  # the CLI has reached its click by now, but the bridge holds it
    assert lb.drv.of("click") == []
    await ws.send(type="agent.resume")
    await ws.wait(lambda e: e["type"] == "run.finished")
    assert len(lb.drv.of("click")) == 1


async def test_action_budget_stops_runaway_agents(client, monkeypatch):
    ws, lb = client
    await ws.send(type="settings.set", patch={"max_actions": 5})
    await ws.wait(lambda e: e["type"] == "settings" and e["settings"]["max_actions"] == 5)
    script(monkeypatch, [{"tool": "cursor_position"}] * 12)
    await ws.send(type="chat.send", text="loop forever")
    fin = await ws.wait(lambda e: e["type"] == "run.finished", 20)
    assert fin["status"] == "stopped"
    tl = tool_log(lb)
    # exactly 5 actions executed; everything beyond the budget was refused (the fake CLI has no
    # model latency, so it can race ahead of the process kill - a real model cannot)
    assert [t["is_error"] for t in tl[:5]] == [False] * 5
    assert len(tl) > 5 and all(t["is_error"] and "Action budget" in t["text"] for t in tl[5:])


# --------------------------------------------------------------------------- persistence APIs
async def test_settings_validation_and_persistence(client):
    ws, lb = client
    await ws.send(type="settings.set", patch={"screenshot_max_edge": 99999})
    err = await ws.wait(lambda e: e["type"] == "error")
    assert "Invalid settings" in err["message"]
    await ws.send(type="settings.set", patch={"theme": "dark", "model": "sonnet", "bogus": 1})
    await ws.wait(lambda e: e["type"] == "settings" and e["settings"]["theme"] == "dark")
    saved = json.loads((lb.tmp / "data" / "settings.json").read_text())
    assert saved["theme"] == "dark" and saved["model"] == "sonnet" and "bogus" not in saved
    assert "api_key" not in json.dumps(saved).lower()  # no credentials anywhere in app storage


async def test_chat_and_workflow_crud(client, monkeypatch):
    ws, lb = client
    script(monkeypatch, [{"say": "ok"}])
    await ws.send(type="workflows.save", name="Morning routine", prompt="Open my calendar and read today's events")
    wfs = (await ws.wait(lambda e: e["type"] == "workflows" and e["workflows"]))["workflows"]
    assert wfs[0]["name"] == "Morning routine"
    await ws.send(type="workflows.run", id=wfs[0]["id"])
    await ws.wait(lambda e: e["type"] == "run.finished")
    chats = ws.of("chats")[-1]["chats"]
    assert chats[0]["title"] == "Morning routine" or "calendar" in chats[0]["title"].lower()
    assert cli_log(lb)[0]["prompt"] == "Open my calendar and read today's events"
    cid = chats[0]["id"]
    await ws.send(type="chat.load", id=cid)
    opened = await ws.wait(lambda e: e["type"] == "chat.opened" and e["chat"]["id"] == cid and len(e["chat"]["items"]) >= 2)
    assert opened["chat"]["items"][0]["role"] == "user"
    await ws.send(type="chat.rename", id=cid, title="Renamed")
    await ws.wait(lambda e: e["type"] == "chats" and e["chats"] and e["chats"][0]["title"] == "Renamed")
    n = len(ws.events)  # only look at events caused by the delete (the hello handshake also sent empty lists)
    await ws.send(type="chat.delete", id=cid)
    await ws.wait(lambda e: e["type"] == "chats" and not e["chats"], start=n)
    assert lb.svc.chats.get(cid) is None
    n = len(ws.events)
    await ws.send(type="workflows.delete", id=wfs[0]["id"])
    await ws.wait(lambda e: e["type"] == "workflows" and not e["workflows"], start=n)
    assert lb.svc.workflows.list() == []


async def test_voice_without_stt_engine_reports_cleanly(client):
    ws, lb = client
    import base64
    await ws.send(type="voice.utterance", audio_b64=base64.b64encode(b"RIFFxxxx").decode(), mode="ptt")
    err = await ws.wait(lambda e: e["type"] == "voice.error")
    assert "faster-whisper" in err["message"]


async def test_tts_chunks_arrive_over_websocket(client, monkeypatch):
    ws, lb = client
    import app.server as server

    async def fake_chunks(text, voice, rate, synth=None):
        for i, s in enumerate(["One.", "Two."]):
            yield i, s.encode(), i == 1

    monkeypatch.setattr(server, "tts_chunks", fake_chunks)
    await ws.send(type="tts.speak", text="One. Two.")
    last = await ws.wait(lambda e: e["type"] == "tts.chunk" and e["last"])
    assert [c["seq"] for c in ws.of("tts.chunk")] == [0, 1] and last["mime"] == "audio/mpeg"


async def test_one_click_install_streams_progress_then_detects_cli(live_backend, tmp_path, fake_claude_path):
    lb = live_backend
    lb.svc.settings.update({"claude_path": ""})
    import shutil, sys
    real = shutil.which
    # not installed at first...
    import app.claude_cli as cc
    state = {"installed": False}
    cc_find = cc.find_claude
    cc.find_claude = lambda custom="": fake_claude_path if state["installed"] else None
    lb.svc.cli.install_argv = [sys.executable, "-c", "print('Downloading Claude Code...'); print('Installed.')"]
    orig_start = lb.svc.cli.start_install

    def start(on_line, on_done):
        def done(ok, d):
            on_done(ok, d)
        def flip_line(l):
            state["installed"] = True  # the installer 'put the binary on disk'
            on_line(l)
        return orig_start(flip_line, done)

    lb.svc.cli.start_install = start
    ws = Ws(await connect(lb.url, origin=None))
    try:
        await ws.send(type="hello")
        st = await ws.wait(lambda e: e["type"] == "auth.status")
        assert st["cli_found"] is False
        n = len(ws.events)
        await ws.send(type="auth.install")
        line = await ws.wait(lambda e: e["type"] == "auth.install.line", start=n)
        assert "Downloading" in line["line"]
        done = await ws.wait(lambda e: e["type"] == "auth.install.done", start=n)
        assert done["ok"] is True
        after = await ws.wait(lambda e: e["type"] == "auth.status" and e["cli_found"], start=n)
        assert after["subscription_ok"]
    finally:
        cc.find_claude = cc_find
        await ws.close()
