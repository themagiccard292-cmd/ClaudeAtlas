"""Paths + persisted settings. No secrets are stored anywhere: authentication is handled
entirely by the user's own `claude` CLI (Claude Pro login); this app never sees a token."""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from . import APP_DIR_NAME


def default_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_DIR_NAME
    return Path.home() / ".local" / "share" / APP_DIR_NAME


class Settings(BaseModel):
    # --- appearance
    theme: Literal["light", "dark", "system"] = "system"
    # --- Claude Code (Pro login)
    claude_path: str = ""  # empty = auto-detect
    model: str = ""  # "", "opus", "sonnet", "haiku" or a full model name
    effort: Literal["", "low", "medium", "high", "xhigh", "max"] = ""
    # --- OS control
    approval_mode: Literal["smart", "all", "never"] = "smart"
    screenshot_max_edge: int = Field(1280, ge=640, le=1568)
    jpeg_quality: int = Field(72, ge=40, le=95)
    auto_screenshot: bool = True  # return a fresh screenshot after every mutating action
    settle_ms: int = Field(350, ge=0, le=3000)
    max_actions: int = Field(150, ge=5, le=1000)
    human_mouse: bool = True
    draw_cursor: bool = True
    hud_enabled: bool = True
    # --- live view (v1.1)
    live_view: Literal["floating", "panel", "off"] = "floating"
    live_fps: int = Field(10, ge=2, le=20)
    live_width: int = Field(960, ge=480, le=1920)
    live_quality: int = Field(55, ge=30, le=90)
    screen_glow: bool = True  # orange glow around the screen while Claude is working
    agent_cursor: bool = True  # draw Claude's cursor + click ripples on the real desktop
    # --- voice (100% local / keyless)
    speak_responses: bool = False
    stt_model: str = "base.en"
    stt_language: str = "en"
    tts_voice: str = "en-US-AriaNeural"
    tts_rate: str = "+0%"
    hands_free: bool = False
    wake_word_enabled: bool = False
    wake_word: str = "hey claude"


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.value = Settings()
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.value = Settings.model_validate(raw)
        except (FileNotFoundError, json.JSONDecodeError, ValidationError):
            self.value = Settings()

    def update(self, patch: dict) -> Settings:
        """Merge ``patch`` into the settings; unknown/invalid keys are rejected as a whole."""
        with self._lock:
            merged = {**self.value.model_dump(), **{k: v for k, v in patch.items() if k in Settings.model_fields}}
            self.value = Settings.model_validate(merged)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.value.model_dump(), indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
            return self.value


class Paths:
    """All on-disk locations under the AppData data dir."""

    def __init__(self, root: Path):
        self.root = root
        self.settings = root / "settings.json"
        self.chats = root / "chats"
        self.shots = root / "shots"
        self.workflows = root / "workflows.json"
        self.runtime = root / "runtime"
        self.workspace = root / "workspace"  # neutral cwd for the claude CLI
        self.models = root / "models"  # faster-whisper model cache
        self.logs = root / "logs"

    def ensure(self) -> "Paths":
        for p in (self.root, self.chats, self.shots, self.runtime, self.workspace, self.models, self.logs):
            p.mkdir(parents=True, exist_ok=True)
        return self
