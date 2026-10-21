"""Runtime settings, read once from the environment and backend/.env."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
# Keys and settings can live in backend/.env (see .env.example). Variables already set in the
# shell take precedence; the Groq client reads its key from the environment too.
load_dotenv(BACKEND_DIR / ".env", override=False)
DATA_DIR = Path(os.environ.get("SHORTCUT_DATA_DIR", BACKEND_DIR / "data")).resolve()
DB_PATH = DATA_DIR / "studio.db"
PROJECTS_DIR = DATA_DIR / "projects"

_HAS_GROQ = bool(os.environ.get("GROQ_API_KEY"))

# Claude and DeepSeek through AgentRouter (agentrouter.org), OpenAI-compatible. Requests identify as Shortcut
# Studio; AGENTROUTER_HEADERS (JSON) adds any client headers AgentRouter asked you to send.
AGENTROUTER_API_KEY = os.environ.get("AGENTROUTER_API_KEY", "")  # one key for both, if you have only one
AGENTROUTER_CLAUDE_KEY = os.environ.get("AGENTROUTER_CLAUDE_KEY", "") or AGENTROUTER_API_KEY
AGENTROUTER_DEEPSEEK_KEY = os.environ.get("AGENTROUTER_DEEPSEEK_KEY", "") or AGENTROUTER_API_KEY
AGENTROUTER_BASE_URL = os.environ.get("AGENTROUTER_BASE_URL", "https://agentrouter.org/v1").rstrip("/")
AGENTROUTER_HEADERS = os.environ.get("AGENTROUTER_HEADERS", "")
CLAUDE_MODEL = os.environ.get("SHORTCUT_CLAUDE_MODEL", "")      # empty: the newest Claude Sonnet offered
DEEPSEEK_MODEL = os.environ.get("SHORTCUT_DEEPSEEK_MODEL", "")  # empty: the newest DeepSeek chat model offered
# Your own DeepSeek key (platform.deepseek.com, pay as you go), separate from AgentRouter.
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_BASE = os.environ.get("SHORTCUT_DEEPSEEK_API_BASE", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_API_MODEL = os.environ.get("SHORTCUT_DEEPSEEK_API_MODEL", "deepseek-chat")

# The AI for highlights, scripts and plans. "auto" tries SHORTCUT_AI_CHAIN in order (default Claude →
# DeepSeek → Groq), skipping a service that is down and rechecking it later; or name one service: "groq",
# "gemini", "openrouter", "sambanova", "claude" or "deepseek". A named service that hits a usage limit pauses
# and asks whether to wait or switch.
LLM_PROVIDER = os.environ.get("SHORTCUT_LLM_PROVIDER",
                              "auto" if AGENTROUTER_CLAUDE_KEY or AGENTROUTER_DEEPSEEK_KEY else "groq")
AI_CHAIN = [s.strip() for s in os.environ.get("SHORTCUT_AI_CHAIN", "claude,deepseek,groq").split(",") if s.strip()]
# Minutes before a service that failed is tried again (Claude is often limited to certain hours).
RECHECK_MINUTES = {"claude": float(os.environ.get("SHORTCUT_CLAUDE_RECHECK_MINUTES", "10")),
                   "deepseek": float(os.environ.get("SHORTCUT_DEEPSEEK_RECHECK_MINUTES", "3")),
                   "deepseek-api": float(os.environ.get("SHORTCUT_DEEPSEEK_RECHECK_MINUTES", "3"))}
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("SHORTCUT_GEMINI_MODEL", "")  # empty: the newest Flash model on the account
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("SHORTCUT_OPENROUTER_MODEL", "openai/gpt-oss-120b:free")
SAMBANOVA_API_KEY = os.environ.get("SAMBANOVA_API_KEY", "")
SAMBANOVA_MODEL = os.environ.get("SHORTCUT_SAMBANOVA_MODEL", "gpt-oss-120b")
GROQ_LLM_MODEL = os.environ.get("SHORTCUT_GROQ_LLM_MODEL", "openai/gpt-oss-120b")
GROQ_FAST_MODEL = os.environ.get("SHORTCUT_GROQ_FAST_MODEL", "openai/gpt-oss-20b")
# Tokens-per-minute limit of your Groq tier (on_demand/free is 8000). Long transcripts are split
# so each request fits; raise this on a paid tier for fewer, larger requests. 0 disables splitting.
GROQ_TPM = int(os.environ.get("SHORTCUT_GROQ_TPM", "8000"))

# Transcription: "groq" (hosted Whisper) or "local" (faster-whisper on this machine).
TRANSCRIBER = os.environ.get("SHORTCUT_TRANSCRIBER", "groq" if _HAS_GROQ else "local")
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

# Video maker: long videos from a prompt, script or article, with free stock footage.
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "")
COVERR_API_KEY = os.environ.get("COVERR_API_KEY", "")            # stock video; ask team@coverr.co for a key
UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "")  # photos; unsplash.com/developers
# Optional private search endpoint. Live web-image search falls back to DuckDuckGo when this is unset or unavailable.
SEARXNG_URL = os.environ.get("SEARXNG_URL", "").strip().rstrip("/")
JAMENDO_CLIENT_ID = os.environ.get("JAMENDO_CLIENT_ID", "")  # free music search (devportal.jamendo.com)
# Your email or website, sent to Wikimedia Commons with each request as its User-Agent policy asks;
# without it Commons may refuse downloads of whole music files.
CONTACT = os.environ.get("SHORTCUT_CONTACT", "").strip()
VIDEOS_DIR = DATA_DIR / "videos"
MUSIC_DIR = VIDEOS_DIR / "music"  # served with the videos

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
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
MUSIC_DIR.mkdir(parents=True, exist_ok=True)
