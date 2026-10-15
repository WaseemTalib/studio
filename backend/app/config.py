"""Runtime settings, read once from the environment."""

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SHORTCUT_DATA_DIR", BACKEND_DIR / "data")).resolve()
DB_PATH = DATA_DIR / "studio.db"
PROJECTS_DIR = DATA_DIR / "projects"

_DEFAULT_PROVIDER = "groq" if os.environ.get("GROQ_API_KEY") else "anthropic"

# Highlight picking: "groq" or "anthropic".
LLM_PROVIDER = os.environ.get("SHORTCUT_LLM_PROVIDER", _DEFAULT_PROVIDER)
CLAUDE_MODEL = os.environ.get("SHORTCUT_CLAUDE_MODEL", "claude-opus-5-5")
CLAUDE_FAST_MODEL = os.environ.get("SHORTCUT_CLAUDE_FAST_MODEL", "claude-haiku-5-5")
GROQ_LLM_MODEL = os.environ.get("SHORTCUT_GROQ_LLM_MODEL", "openai/gpt-oss-120b")
GROQ_FAST_MODEL = os.environ.get("SHORTCUT_GROQ_FAST_MODEL", "openai/gpt-oss-20b")
# Tokens-per-minute limit of your Groq tier (on_demand/free is 8000). Long transcripts are split
# so each request fits; raise this on a paid tier for fewer, larger requests. 0 disables splitting.
GROQ_TPM = int(os.environ.get("SHORTCUT_GROQ_TPM", "8000"))

# Transcription: "groq" (hosted Whisper) or "local" (faster-whisper on this machine).
TRANSCRIBER = os.environ.get("SHORTCUT_TRANSCRIBER", "groq" if _DEFAULT_PROVIDER == "groq" else "local")
GROQ_WHISPER_MODEL = os.environ.get("SHORTCUT_GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")
WHISPER_MODEL = os.environ.get("SHORTCUT_WHISPER_MODEL", "small")
WHISPER_DEVICE = os.environ.get("SHORTCUT_WHISPER_DEVICE", "auto")
WHISPER_COMPUTE = os.environ.get("SHORTCUT_WHISPER_COMPUTE", "int8")

# Voice Studio (text to speech): ElevenLabs when ELEVENLABS_API_KEY is set, plus a local Kokoro model.
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_API = os.environ.get("SHORTCUT_ELEVENLABS_API", "https://api.elevenlabs.io")
VOICE_DIR = DATA_DIR / "voice"
MODELS_DIR = DATA_DIR / "models"
# Kokoro model file: kokoro-v1.0.onnx (full), kokoro-v1.0.fp16.onnx or kokoro-v1.0.int8.onnx (smallest).
KOKORO_MODEL = os.environ.get("SHORTCUT_KOKORO_MODEL", "kokoro-v1.0.onnx")

FFMPEG = os.environ.get("SHORTCUT_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("SHORTCUT_FFPROBE", "ffprobe")
# Prefer the yt-dlp installed with the backend (kept current via requirements) over an
# older system copy; YouTube changes often break outdated versions with HTTP 403.
_BUNDLED_YT_DLP = Path(sys.executable).with_name("yt-dlp")
YT_DLP = os.environ.get("SHORTCUT_YT_DLP", str(_BUNDLED_YT_DLP) if _BUNDLED_YT_DLP.exists() else "yt-dlp")

# Output short side per export resolution; the long side follows the aspect ratio.
SHORT_SIDE = {"720p": 720, "1080p": 1080, "4k": 2160}

PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
VOICE_DIR.mkdir(parents=True, exist_ok=True)
