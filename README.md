# Shortcut Studio

Turn long videos into short, captioned clips.

Pipeline: **import** (upload or link via yt-dlp) → **transcribe** (faster-whisper, word timestamps) →
**pick highlights** (Groq or another free LLM platform, structured output) → **render** (ffmpeg cut + reframe to 9:16 / 1:1 / 16:9,
burned-in captions) → clip library with virality score, hook, reasoning, hashtags, and download.

## Requirements

- Python 3.10+
- Node 20+
- `ffmpeg`, `ffprobe` and `yt-dlp` on `PATH`
- A free Groq API key (`GROQ_API_KEY`, from console.groq.com)

## Run

```sh
# Backend (http://127.0.0.1:8000)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                 # then add GROQ_API_KEY to .env
.venv/bin/uvicorn app.main:app --reload

# Frontend (http://localhost:5173), in another terminal
cd frontend
npm install
npm run dev
```

With `GROQ_API_KEY` set, both transcription (hosted Whisper) and highlight picking run on Groq.
Without it, transcription runs locally with faster-whisper (first run downloads the `small` model,
~500 MB); highlight picking needs Groq or one of the other free platforms below.

Groq's free tier has per-minute and per-day token limits for each model. Long transcripts are split to fit the per-minute limit. When the daily limit of the highlight model runs out, the other Groq model (`SHORTCUT_GROQ_FAST_MODEL` or `SHORTCUT_GROQ_LLM_MODEL`) is used instead.

## Usage limits

Hosted services have usage limits (Groq's free tier: tokens per minute and per day for highlights,
audio seconds per hour and per day for transcription). When one is reached:

- **Per-minute limits** are waited out automatically; the progress bar shows the countdown and the
  tokens left.
- **Longer limits** pause the project and open a dialog with the exact numbers (allowance, used,
  remaining, what the request needs, and when it frees up). You choose: **wait** (it continues by itself
  when the limit frees up), **switch** to another option, or cancel. Nothing switches on its own, and
  finished work is kept: after switching, transcription continues where it stopped.

Options you can switch to, once set up in `backend/.env`:

| For | Option | Setup |
|---|---|---|
| Transcription | Groq's other Whisper model | `GROQ_API_KEY` (each model has its own limits) |
| Transcription | Whisper on this computer | none; free, slower, downloads the model on first use |
| Highlights | Groq's other model | `GROQ_API_KEY` |
| Highlights | Google Gemini Flash | free `GEMINI_API_KEY` from aistudio.google.com |
| Highlights | OpenRouter `gpt-oss-120b:free` | free `OPENROUTER_API_KEY` from openrouter.ai |
| Highlights | SambaNova `gpt-oss-120b` | free `SAMBANOVA_API_KEY` from cloud.sambanova.ai |

## Voice Studio

**Voice Studio** (top navigation, `/voice-studio`) turns text into speech, laid out like ElevenLabs' text-to-speech
page: a text editor, voice and model settings, a history of everything you generated, and a player.

- **ElevenLabs**: set `ELEVENLABS_API_KEY`. You get ElevenLabs' models, their default voices, your own voices and
  the community voice library (filters for language, accent, gender, age, use case, and the licensed "Iconic"
  voices). Using a library voice first adds it to your ElevenLabs account; that happens only when you click
  **Add & use**. Generations count against your ElevenLabs plan.
- **Local (Kokoro)**: free and offline. 49 voices in English (US/UK), Spanish, French, Hindi, Italian, Portuguese
  and Chinese. The model (about 350 MB) is downloaded on first use into `backend/data/models/kokoro`.

Generated audio is saved in `backend/data/voice/`.

## Video Maker

**Video Maker** (top navigation, `/video-maker`) makes long narrated videos, inspired by
[MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) and built only from free services:

1. **Source**: a prompt, a description, your own script (used word for word; blank lines separate scenes), or a
   news article link or text (the script sticks to the article's facts). Pick the length (1–20 minutes), language
   and tone.
2. **Script**: an LLM on a free tier (Groq by default, or Gemini, OpenRouter or SambaNova) writes it as scenes, each
   with English stock-footage searches. Edit, reorder, add or delete scenes and searches before making the video.
   If the service hits a usage limit, you choose to wait (it retries when the limit frees up) or switch.
3. **Voice**: Kokoro on this computer (free) or ElevenLabs. Narration is kept per scene, so a retry or remake does
   not record it again.
4. **Footage**: clips from Pexels and/or Pixabay (free keys), matched to each scene; ffmpeg reads only the seconds
   it needs. Without a key, choose a plain coloured background. Credits for the clips used are listed per video.
5. **Captions**: every caption style from the clipper, timed by Whisper on the narration and showing the script's own
   words. **Music**: upload tracks you have the rights to; they loop under the narration and fade out.

Videos are saved in `backend/data/videos/`, uploaded music in `backend/data/videos/music/`.

## Configuration (environment)

Put keys and settings in `backend/.env` (start from `backend/.env.example`; the file is ignored by git), or
export them in your shell, which takes precedence. Restart the backend after changing them.

| Variable | Default | Purpose |
|---|---|---|
| `SHORTCUT_DATA_DIR` | `backend/data` | SQLite DB and project media |
| `SHORTCUT_LLM_PROVIDER` | `groq` | Highlight picker: `groq`, `gemini`, `openrouter` or `sambanova` |
| `SHORTCUT_GROQ_LLM_MODEL` | `openai/gpt-oss-120b` | Groq model (must support strict JSON schema) |
| `SHORTCUT_GROQ_TPM` | `8000` | Your Groq tier's tokens-per-minute limit; long transcripts are split to fit (`0` = never split) |
| `SHORTCUT_TRANSCRIBER` | `groq` if `GROQ_API_KEY` is set, else `local` | Speech-to-text |
| `SHORTCUT_GROQ_WHISPER_MODEL` | `whisper-large-v3-turbo` | Groq Whisper model |
| `ELEVENLABS_API_KEY` | unset | Enables ElevenLabs in Voice Studio |
| `PEXELS_API_KEY` | unset | Pexels stock footage in Video Maker (free at pexels.com/api) |
| `PIXABAY_API_KEY` | unset | Pixabay stock footage in Video Maker (free at pixabay.com/api/docs) |
| `SHORTCUT_KOKORO_MODEL` | `kokoro-v1.0.onnx` | Local voice model file (`kokoro-v1.0.int8.onnx` is smaller but slower on Apple Silicon) |
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
