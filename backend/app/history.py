"""Chat transcripts, thumbnails and saved automation workflows (JSON files in AppData)."""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def safe_id(value: str) -> str:
    if not _ID_RE.match(value or ""):
        raise ValueError("invalid id")
    return value


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class ChatStore:
    def __init__(self, chats_dir: Path, shots_dir: Path):
        self.dir = chats_dir
        self.shots_dir = shots_dir
        self._lock = threading.Lock()

    def _path(self, chat_id: str) -> Path:
        return self.dir / f"{safe_id(chat_id)}.json"

    def create(self, title: str = "New chat") -> dict:
        now = time.time()
        chat = {"id": new_id(), "title": title, "created": now, "updated": now, "session_id": None, "items": []}
        self.save(chat)
        return chat

    def get(self, chat_id: str) -> dict | None:
        try:
            return json.loads(self._path(chat_id).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            return None

    def save(self, chat: dict) -> None:
        with self._lock:
            chat["updated"] = time.time()
            _atomic_write(self._path(chat["id"]), json.dumps(chat))

    def list(self) -> list[dict]:
        out = []
        for p in self.dir.glob("*.json"):
            try:
                c = json.loads(p.read_text(encoding="utf-8"))
                out.append({"id": c["id"], "title": c.get("title", "Chat"), "updated": c.get("updated", 0), "created": c.get("created", 0)})
            except (json.JSONDecodeError, KeyError, OSError):
                continue
        out.sort(key=lambda c: c["updated"], reverse=True)
        return out

    def delete(self, chat_id: str) -> None:
        with self._lock:
            self._path(chat_id).unlink(missing_ok=True)
            shutil.rmtree(self.shots_dir / safe_id(chat_id), ignore_errors=True)

    def rename(self, chat_id: str, title: str) -> None:
        chat = self.get(chat_id)
        if chat:
            chat["title"] = title.strip()[:80] or chat["title"]
            self.save(chat)

    # ---- thumbnails -------------------------------------------------------------
    def save_shot(self, chat_id: str, jpeg: bytes) -> str:
        d = self.shots_dir / safe_id(chat_id)
        d.mkdir(parents=True, exist_ok=True)
        name = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}.jpg"
        (d / name).write_bytes(jpeg)
        return f"{chat_id}/{name}"

    def shot_path(self, chat_id: str, name: str) -> Path | None:
        if not _ID_RE.match(chat_id) or not re.match(r"^[A-Za-z0-9_.-]{1,80}$", name) or ".." in name:
            return None
        p = self.shots_dir / chat_id / name
        return p if p.is_file() else None


class WorkflowStore:
    """Saved automation workflows: a named prompt the user can re-run with one click."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def list(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def save(self, name: str, prompt: str, wf_id: str | None = None) -> dict:
        with self._lock:
            items = self.list()
            name = name.strip()[:80] or "Untitled workflow"
            wf = next((w for w in items if w["id"] == wf_id), None) if wf_id else None
            if wf:
                wf.update(name=name, prompt=prompt)
            else:
                wf = {"id": new_id(), "name": name, "prompt": prompt, "created": time.time(), "last_run": None}
                items.append(wf)
            _atomic_write(self.path, json.dumps(items, indent=2))
            return wf

    def delete(self, wf_id: str) -> None:
        with self._lock:
            items = [w for w in self.list() if w["id"] != wf_id]
            _atomic_write(self.path, json.dumps(items, indent=2))

    def get(self, wf_id: str) -> dict | None:
        return next((w for w in self.list() if w["id"] == wf_id), None)

    def touch(self, wf_id: str) -> None:
        with self._lock:
            items = self.list()
            for w in items:
                if w["id"] == wf_id:
                    w["last_run"] = time.time()
            _atomic_write(self.path, json.dumps(items, indent=2))
