"""Local text to speech with Kokoro (82M parameters, Apache-2.0), run through onnxruntime.

The model and voice files are downloaded on first use into MODELS_DIR/kokoro.
"""

import tempfile
import threading
import wave
from functools import lru_cache
from pathlib import Path

import requests

from ..config import FFMPEG, KOKORO_MODEL, MODELS_DIR
from ..pipeline.media import run
from .common import TTSError

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
VOICES_FILE = "voices-v1.0.bin"
MODEL_DIR = MODELS_DIR / "kokoro"
MAX_CHARS = 20000

# Voice id prefix: language, then f/m for female/male. Value: (espeak language, label).
LANGUAGES = {
    "a": ("en-us", "English (American)"), "b": ("en-gb", "English (British)"), "e": ("es", "Spanish"),
    "f": ("fr-fr", "French"), "h": ("hi", "Hindi"), "i": ("it", "Italian"),
    "p": ("pt-br", "Portuguese (Brazilian)"), "z": ("cmn", "Chinese (Mandarin)"),
}
# Kokoro's Japanese voices (jf_*, jm_*) are left out: espeak cannot read kanji or katakana and
# says "Chinese letter" instead; they would need the misaki[ja] phonemizer.
# Kokoro v1.0 voices, the most natural-sounding first (per Kokoro's own voice ratings).
VOICES = [
    "af_heart", "af_bella", "af_nicole", "bf_emma", "am_michael", "am_fenrir", "am_puck", "af_aoede", "af_kore",
    "af_sarah", "bm_george", "bm_fable", "bf_isabella", "af_nova", "af_alloy", "ff_siwis", "zf_xiaobei",
    "hf_alpha", "hf_beta", "if_sara", "im_nicola", "af_sky", "af_jessica", "af_river", "am_adam", "am_echo",
    "am_eric", "am_liam", "am_onyx", "am_santa", "bf_alice", "bf_lily", "bm_daniel", "bm_lewis", "ef_dora",
    "em_alex", "em_santa", "hm_omega", "hm_psi", "pf_dora", "pm_alex", "pm_santa", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi", "zm_yunjian", "zm_yunxi",
    "zm_yunxia", "zm_yunyang",
]
SAMPLES = {
    "en-us": "Hi, I'm {name}. This is what I sound like reading your script.",
    "en-gb": "Hello, I'm {name}. This is what I sound like reading your script.",
    "es": "¡Hola! Esta es una muestra de mi voz.",
    "fr-fr": "Bonjour ! Voici un extrait de ma voix.",
    "hi": "नमस्ते! यह मेरी आवाज़ का एक नमूना है।",
    "it": "Ciao! Questo è un esempio della mia voce.",
    "pt-br": "Olá! Esta é uma amostra da minha voz.",
    "cmn": "你好！这是我的声音样本。",
}

_download_lock = threading.Lock()
_engine_lock = threading.Lock()  # one synthesis at a time; it already uses every CPU core
_download = {"state": "idle", "done": 0, "total": 0, "error": None}  # state: idle | downloading | failed


def _files() -> list[str]:
    return [KOKORO_MODEL, VOICES_FILE]


def installed() -> bool:
    return all((MODEL_DIR / f).exists() for f in _files())


def status() -> dict:
    state = "ready" if installed() else _download["state"]
    return {"state": state, "done": _download["done"], "total": _download["total"], "error": _download["error"]}


def start_download() -> dict:
    """Fetch the model in the background; poll status() for progress."""
    with _download_lock:
        if not installed() and _download["state"] != "downloading":
            _download.update(state="downloading", done=0, total=0, error=None)
            threading.Thread(target=_fetch, daemon=True, name="kokoro-download").start()
    return status()


def _fetch() -> None:
    try:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        missing = [f for f in _files() if not (MODEL_DIR / f).exists()]
        sizes = {f: int(requests.head(f"{RELEASE}/{f}", allow_redirects=True, timeout=30).headers.get("content-length", 0))
                 for f in missing}
        _download["total"] = sum(sizes.values())
        for name in missing:
            part = MODEL_DIR / f"{name}.part"
            with requests.get(f"{RELEASE}/{name}", stream=True, timeout=60) as r:
                r.raise_for_status()
                with part.open("wb") as out:
                    for chunk in r.iter_content(1 << 20):
                        out.write(chunk)
                        _download["done"] += len(chunk)
            part.rename(MODEL_DIR / name)
        _download["state"] = "idle"
    except Exception as e:  # reported through status(); the user can retry
        _download.update(state="failed", error=f"Downloading the voice model failed: {e}")


@lru_cache(maxsize=1)
def _engine():
    from kokoro_onnx import Kokoro

    return Kokoro(str(MODEL_DIR / KOKORO_MODEL), str(MODEL_DIR / VOICES_FILE))


def _name(voice_id: str) -> str:
    return voice_id.split("_", 1)[1].replace("_", " ").title()


def voices() -> list[dict]:
    out = []
    for vid in VOICES:
        lang_code, language = LANGUAGES[vid[0]]
        language, _, accent = language.partition(" (")
        out.append({
            "provider": "local", "id": vid, "name": _name(vid),
            "description": "",  # language, accent and gender are shown as tags
            "gender": "female" if vid[1] == "f" else "male", "age": None,
            "accent": accent.rstrip(")") or None, "language": language, "use_case": None, "category": "local",
            "preview_url": f"/api/tts/voices/local/{vid}/preview", "public_owner_id": None, "in_library": True,
            "popularity": None,
        })
    return out


def models() -> list[dict]:
    return [{"id": "kokoro-v1.0", "name": "Kokoro v1.0 (local)",
             "description": "Runs on this computer. Free and private; English is the strongest language.",
             "max_chars": MAX_CHARS, "languages": sorted({l for _, l in LANGUAGES.values()}), "controls": ["speed"]}]


def synthesize(text: str, voice_id: str, speed: float) -> bytes:
    """MP3 audio of `text` in one of the VOICES."""
    if voice_id not in VOICES:
        raise TTSError(f"Unknown local voice {voice_id!r}")
    if not installed():
        raise TTSError("The local voice model is not downloaded yet")
    import numpy as np

    with _engine_lock:
        samples, rate = _engine().create(text, voice=voice_id, speed=min(max(speed, 0.5), 2.0),
                                         lang=LANGUAGES[voice_id[0]][0])
    with tempfile.TemporaryDirectory() as tmp:
        wav, mp3 = Path(tmp) / "speech.wav", Path(tmp) / "speech.mp3"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
        run([FFMPEG, "-y", "-v", "error", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "128k", str(mp3)])
        return mp3.read_bytes()


def sample(voice_id: str, cache_dir: Path) -> Path:
    """A short greeting in the voice, generated once and kept."""
    path = cache_dir / f"{voice_id}.mp3"
    if not path.exists():
        lang = LANGUAGES[voice_id[0]][0]
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(synthesize(SAMPLES[lang].format(name=_name(voice_id)), voice_id, 1.0))
    return path
