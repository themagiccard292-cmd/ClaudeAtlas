"""100% keyless voice: faster-whisper (local STT) and edge-tts (TTS)."""
from __future__ import annotations

import asyncio
import io
import logging
import re
import threading
from pathlib import Path
from typing import AsyncIterator, Callable

log = logging.getLogger("voice")

_HALLUCINATIONS = {
    "you", "thank you", "thanks", "thanks for watching", "thank you for watching", "bye", "bye bye", "so", "okay", "uh", "um", "hmm", "mm",
}


def clean_transcript(text: str) -> str:
    """Drop Whisper's classic silence hallucinations ('Thank you.', 'you')."""
    t = " ".join((text or "").split())
    if re.sub(r"[^a-z ]", "", t.lower()).strip() in _HALLUCINATIONS:
        return ""
    return t


def strip_wake_word(text: str, wake: str) -> str | None:
    """'Hey Claude, open notepad' -> 'open notepad'. Returns None if the wake phrase is absent."""
    norm = lambda s: re.sub(r"[^a-z0-9 ]", "", s.lower()).split()  # noqa: E731
    want = norm(wake)
    if not want:
        return None
    words = text.split()
    i = matched = 0
    while i < len(words) and matched < len(want):
        toks = norm(words[i])  # a raw word may normalise to nothing ("-") or to one token
        if toks:
            if toks != want[matched : matched + len(toks)]:
                return None
            matched += len(toks)
        i += 1
    if matched < len(want):
        return None
    return " ".join(words[i:]).strip(" ,.!?:;-")


class SpeechToText:
    def __init__(self, models_dir: Path, model: Callable[[], str], language: Callable[[], str]):
        self.models_dir, self._model_name, self._language = models_dir, model, language
        self._model = None
        self._loaded_name = None
        self._lock = threading.Lock()

    @staticmethod
    def available() -> tuple[bool, str]:
        try:
            import faster_whisper  # noqa: F401

            return True, ""
        except Exception as e:  # noqa: BLE001
            return False, f"faster-whisper is not available: {e}"

    def _load(self):
        name = self._model_name()
        with self._lock:
            if self._model is None or self._loaded_name != name:
                from faster_whisper import WhisperModel

                log.info("loading whisper model %s (first use downloads it once)", name)
                self._model = WhisperModel(name, device="cpu", compute_type="int8", download_root=str(self.models_dir))
                self._loaded_name = name
            return self._model

    def transcribe(self, wav: bytes) -> str:
        model = self._load()
        lang = self._language().strip() or None
        segments, _info = model.transcribe(io.BytesIO(wav), language=lang, vad_filter=True, beam_size=1, condition_on_previous_text=False)
        parts = [s.text.strip() for s in segments if getattr(s, "no_speech_prob", 0.0) < 0.6]
        return clean_transcript(" ".join(parts))


# ----------------------------------------------------------------------------------
# TTS
# ----------------------------------------------------------------------------------
def speakable(text: str, limit: int = 1200) -> str:
    t = re.sub(r"```.*?```", " (code omitted) ", text, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"https?://\S+", "a link", t)
    t = re.sub(r"[#*_>|~]+", " ", t)
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.M)
    t = " ".join(t.split())
    return t[:limit]


def split_sentences(text: str, max_len: int = 240) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    out: list[str] = []
    for p in parts:
        while len(p) > max_len:
            cut = p.rfind(" ", 0, max_len)
            cut = cut if cut > 40 else max_len
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            out.append(p)
    return out


async def synthesize(text: str, voice: str, rate: str) -> bytes:
    import edge_tts

    buf = bytearray()
    async for chunk in edge_tts.Communicate(text, voice, rate=rate).stream():
        if chunk["type"] == "audio":
            buf += chunk["data"]
    return bytes(buf)


async def tts_chunks(text: str, voice: str, rate: str, synth=synthesize) -> AsyncIterator[tuple[int, bytes, bool]]:
    """Yield (sequence, mp3 bytes, is_last) sentence by sentence so playback can start immediately."""
    sentences = split_sentences(speakable(text))
    for i, s in enumerate(sentences):
        audio = await synth(s, voice, rate)
        yield i, audio, i == len(sentences) - 1
