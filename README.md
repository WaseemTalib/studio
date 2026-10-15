# Shortcut Studio

Turn long videos into short, captioned clips.

Pipeline: **import** (upload or link via yt-dlp) → **transcribe** (faster-whisper, word timestamps) →
**pick highlights** (Claude, structured output) → **render** (ffmpeg cut + reframe to 9:16 / 1:1 / 16:9,
burned-in captions) → clip library with virality score, hook, reasoning, hashtags, and download.

## Requirements

- Python 3.10+
- Node 20+
- `ffmpeg`, `ffprobe` and `yt-dlp` on `PATH`
- A Groq API key (`GROQ_API_KEY`, from console.groq.com) **or** an Anthropic API key (`ANTHROPIC_API_KEY`)

## Run

```sh
# Backend (http://127.0.0.1:8000)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export GROQ_API_KEY=gsk_...          # or ANTHROPIC_API_KEY=sk-ant-...
.venv/bin/uvicorn app.main:app --reload

# Frontend (http://localhost:5173), in another terminal
cd frontend
npm install
npm run dev
```

With `GROQ_API_KEY` set, both transcription (hosted Whisper) and highlight picking run on Groq.
Without it, transcription runs locally with faster-whisper (first run downloads the `small` model,
~500 MB) and highlights use Claude.

Groq's free tier has per-minute and per-day token limits for each model. Long transcripts are split to fit the per-minute limit. When the daily limit of the highlight model runs out, the other Groq model (`SHORTCUT_GROQ_FAST_MODEL` or `SHORTCUT_GROQ_LLM_MODEL`) is used instead.

## Configuration (environment)

| Variable | Default | Purpose |
|---|---|---|
| `SHORTCUT_DATA_DIR` | `backend/data` | SQLite DB and project media |
| `SHORTCUT_LLM_PROVIDER` | `groq` if `GROQ_API_KEY` is set, else `anthropic` | Highlight picker |
| `SHORTCUT_GROQ_LLM_MODEL` | `openai/gpt-oss-120b` | Groq model (must support strict JSON schema) |
| `SHORTCUT_GROQ_TPM` | `8000` | Your Groq tier's tokens-per-minute limit; long transcripts are split to fit (`0` = never split) |
| `SHORTCUT_CLAUDE_MODEL` | `claude-opus-5-5` | Claude model |
| `SHORTCUT_TRANSCRIBER` | `groq` if `GROQ_API_KEY` is set, else `local` | Speech-to-text |
| `SHORTCUT_GROQ_WHISPER_MODEL` | `whisper-large-v3-turbo` | Groq Whisper model |
| `SHORTCUT_WHISPER_MODEL` | `small` | Local model: `tiny`, `base`, `small`, `medium`, `large-v3` |
| `SHORTCUT_WHISPER_DEVICE` | `auto` | `cpu`, `cuda`, or `auto` |
| `SHORTCUT_WHISPER_COMPUTE` | `int8` | faster-whisper compute type |
| `SHORTCUT_FFMPEG` / `SHORTCUT_FFPROBE` | on `PATH` | Tool paths |
| `SHORTCUT_YT_DLP` | the venv's `yt-dlp` | Downloader; upgrade with `.venv/bin/pip install -U "yt-dlp[default]"` if YouTube returns 403 |

## API

| Method | Path | |
|---|---|---|
| `GET` | `/api/projects` | List projects |
| `POST` | `/api/projects` | Multipart: `url` **or** `file`, plus `options` JSON |
| `GET` | `/api/projects/{id}` | Project status and progress |
| `DELETE` | `/api/projects/{id}` | Delete a finished project and its media |
| `GET` | `/api/projects/{id}/clips` | Clips, best score first |
| `GET` | `/api/projects/{id}/transcript` | Word-level transcript |
| `PATCH` | `/api/clips/{id}` | Edit `title` / `hashtags` |

`options`: `clip_count` (1–20), `min_seconds`, `max_seconds`, `aspect` (`9:16` | `1:1` | `16:9`),
`captions` (bool), `language` (ISO code or `null` to auto-detect).

## Notes

- Jobs run one at a time in-process; a server restart marks unfinished jobs as failed.
- Captions are drawn with Pillow and overlaid by ffmpeg, so no libass/freetype ffmpeg build is needed.
- Reframing is a center crop. Speaker/face tracking is not implemented yet.
- Only download videos you have the rights to use.
