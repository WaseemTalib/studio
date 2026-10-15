"""Voice Studio: text to speech through ElevenLabs or the local Kokoro model, with a history."""

import json
import subprocess
import time
from pathlib import Path

from .. import db
from ..config import FFPROBE, VOICE_DIR
from . import elevenlabs, local
from .common import TTSError

PROVIDERS = ("elevenlabs", "local")
MEDIA_PREFIX = "/voice-media/"
SAMPLE_DIR = VOICE_DIR / "samples"

__all__ = ["TTSError", "providers", "models", "voices", "generate", "history", "delete", "elevenlabs", "local"]


def providers() -> list[dict]:
    return [
        {"id": "elevenlabs", "label": "ElevenLabs", "available": elevenlabs.available(),
         "note": None if elevenlabs.available() else "Set ELEVENLABS_API_KEY and restart the backend"},
        {"id": "local", "label": "Local (Kokoro)", "available": True, "model": local.status()},
    ]


def models(provider: str) -> list[dict]:
    return elevenlabs.models() if provider == "elevenlabs" else local.models()


def voices(provider: str, source: str, filters: dict, page: str | None) -> dict:
    if provider == "local":
        found = local.voices()
        search = (filters.get("search") or "").lower()
        for key in ("gender", "language"):
            if filters.get(key):
                found = [v for v in found if (v[key] or "").lower() == filters[key].lower()]
        if search:
            # Every search word must start one of the voice's words ("brit" finds British; "male" not female).
            words = lambda v: f"{v['name']} {v['accent'] or ''} {v['language']} {v['gender']}".lower().split()
            found = [v for v in found if all(any(w.startswith(q) for w in words(v)) for q in search.split())]
        return {"voices": found, "next": None}
    if source == "library":
        return elevenlabs.library_voices(filters, int(page or 0))
    return elevenlabs.my_voices(source, filters.get("search"), page)


def generate(provider: str, voice_id: str, voice_name: str, model_id: str, text: str, settings: dict) -> dict:
    text = text.strip()
    if not text:
        raise TTSError("Type some text first")
    if provider == "elevenlabs":
        audio = elevenlabs.synthesize(text, voice_id, model_id, settings)
    else:
        if len(text) > local.MAX_CHARS:
            raise TTSError(f"Local generation takes up to {local.MAX_CHARS} characters")
        audio = local.synthesize(text, voice_id, settings["speed"])
    gid = db.new_id()
    path = VOICE_DIR / f"{gid}.mp3"
    path.write_bytes(audio)
    record = {"id": gid, "provider": provider, "voice_id": voice_id, "voice_name": voice_name,
              "model_id": model_id, "text": text, "settings": settings, "audio": MEDIA_PREFIX + path.name,
              "duration": _duration(path), "created_at": time.time()}
    db.add_generation(record)
    return record


def _duration(path: Path) -> float | None:
    try:
        out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                             capture_output=True, text=True, timeout=30).stdout
        return float(json.loads(out)["format"]["duration"])
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
        return None


def history() -> list[dict]:
    return db.list_generations()


def delete(gid: str) -> bool:
    g = db.get_generation(gid)
    if g is None:
        return False
    (VOICE_DIR / g["audio"].removeprefix(MEDIA_PREFIX)).unlink(missing_ok=True)
    return db.delete_generation(gid)


def local_sample(voice_id: str) -> Path:
    return local.sample(voice_id, SAMPLE_DIR)
