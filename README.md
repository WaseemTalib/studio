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
.venv/bin/uvicorn app.main:app --reload --timeout-graceful-shutdown 3

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
  tokens left. Click **Switch model now…** at any point to continue immediately with another available model.
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

## AI model

Highlights (New project → **AI model**), Video Maker scripts and footage checks, and Stickman Director plans can
use Claude or DeepSeek through AgentRouter (`AGENTROUTER_CLAUDE_KEY`, `AGENTROUTER_DEEPSEEK_KEY`), DeepSeek with your
personal key (`DEEPSEEK_API_KEY`), Groq (gpt-oss-120b or the faster 20b), Google
Gemini Flash, OpenRouter or SambaNova. Services without a key in `backend/.env` are greyed out. The last choice is
remembered in the browser and shared by the pages; **Default** uses `SHORTCUT_LLM_PROVIDER`.

**Automatic** (the default once an AgentRouter key is set) tries **Claude → DeepSeek → Groq**
(`SHORTCUT_AI_CHAIN`). A service that is outside its hours, out of quota, rate-limited or unreachable is skipped,
with no further requests, until its recheck time (Claude 10 min, DeepSeek 3 min, or the rate limit's reset); the
first request after that rechecks it, so work returns to Claude as soon as it answers. The picker shows where each
service stands. Only when every service in the chain is down does the wait-or-switch dialog appear. Transcription
stays on Groq Whisper (or local Whisper), since Claude and DeepSeek do not transcribe audio.

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
4. **Footage**: use live Openverse research, web image search through SearXNG or DuckDuckGo, YouTube search,
   Pixabay, Pexels, Coverr (free keys), Wikimedia Commons real-world and news video (no key; graphic titles are
   filtered out), and Unsplash photos (free key, shown with a slow zoom); ffmpeg reads
   only the seconds it needs. The footage review can also use **your own clips and images** (My uploads). A clip is used
   only when its site's description matches the scene's search (animation, cartoons, green screen and AI clips are
   left out), and an AI check of those descriptions against the narration drops off-topic ones. Searches are taken
   in the order the narration mentions things, so each shot matches what is being said. **Find footage** shows
   every scene's shots before rendering: hover to preview, click to swap, search the live research sources, stock
   sites or photos, add or remove shots. Web and YouTube results retain their source links and are marked for rights
   review before publishing. Scenes can also open with a **fact card** or **quote card** (quotes are kept only when they appear
   word for word in your source) and show a free-licensed **photo of a named place** from Wikimedia Commons.
   Without a stock key, choose a plain coloured background. Credits (and photo licences) are listed per video.
5. **Captions**: every caption style from the clipper, timed by Whisper on the narration and showing the script's own
   words.
6. **Music**: no music, a track from your library, a random library track, or **Match the script** (the AI
   suggests a mood and a matching track is found when the video is made). **Find free music** searches Jamendo
   (`JAMENDO_CLIENT_ID`) or Wikimedia Commons for Creative Commons tracks that allow commercial use; preview them
   and **Add & use**. Their artist and licence go into the video's credits. You can also upload tracks you have
   the rights to. Music loops under the narration and fades out. Commons asks apps for a contact before allowing
   whole-file downloads: set `SHORTCUT_CONTACT` (your email or website).

Videos are saved in `backend/data/videos/`, uploaded music in `backend/data/videos/music/`.

## Video Director

**Video Director** (top navigation, `/director`) turns a topic, script or article into a reviewed production plan for
photorealistic videos with real people and real-world scenes. It uses the same proposal → approval → production-prompt
workflow as Stickman Director, while keeping its projects and prompt rules separate.

Choose a real-world visual mode based on the story's intent: **Logical Explainer**, **Modern Tech**, **Emotional Story**,
**Educational Documentary**, **Real-World Satire** or **Cozy Lifestyle**. The generated plan preserves recurring people's
identity, appearance, wardrobe and props across ten-second clips, provides three timed visual beats per clip, and exports
ready-to-paste Google Flow prompts, a reusable character-reference-sheet prompt, a Markdown plan and English subtitles.
Each clip repeats the complete cast bible and an immutable identity skill; only the roles named for that clip are rendered.
The same narrator profile is repeated verbatim, speech begins on the first frame and picture, narration and background
audio continue through every beat and clip boundary without blank or frozen sections.

## Stickman Director

**Stickman Director** (top navigation, `/stickman`) brings the
[Stickman Video Director](https://github.com/kaomei/stickman-video-director) skill (MIT; licence in
`backend/app/director/LICENSE`) into the app. Give a topic, script or article (or link), an aspect ratio
(16:9 / 9:16), a length in ten-second clips (30 s to 5 min) and one of six styles: Classic Minimalist light/dark,
Modern Studio Tech, Cinematic Story, Prehistoric Dark Comedy or Cozy Storytelling.

1. **Director's proposal**: titles, hook, a complete narrator voice profile, palette, music, emotional arc, cast
   cards (prehistoric style) and a storyboard of N ten-second clips following the 5-stage "heartbeat" structure,
   each with three timed beats, visual devices, camera/transition, English VO plus a reference translation in your
   language, BGM/SFX and the state handed to the next clip. Revise the whole proposal, one clip, or the global
   settings (which recomposes it and clears approval).
2. **Production prompts** after approval: a global continuity block, N standalone Google Flow (Omni Flash) prompts
   in the skill's 12-part order, a stitching guide and voice/music notes. The character anchors, voice block, music
   lock and negative constraints are inserted verbatim by code, so every clip carries identical locks. A character
   consistency skill also creates a reusable model-sheet prompt and repeats the full immutable character bible in every
   clip. A no-gap continuity skill locks one narrator voice and continuous picture, narration, music and room tone across
   every clip. Rewrite any single prompt with feedback. The skill's checklists run on both phases.
3. **Export**: the whole plan as Markdown and English subtitles as `.srt` for editing.

Generating the clips happens in Google Flow; the app plans and writes the prompts.

## Configuration (environment)

Put keys and settings in `backend/.env` (start from `backend/.env.example`; the file is ignored by git), or
export them in your shell, which takes precedence. Restart the backend after changing them.

| Variable | Default | Purpose |
|---|---|---|
| `SHORTCUT_DATA_DIR` | `backend/data` | SQLite DB and project media |
| `AGENTROUTER_CLAUDE_KEY` / `AGENTROUTER_DEEPSEEK_KEY` | unset | Claude and DeepSeek through AgentRouter (or `AGENTROUTER_API_KEY` for both); enables the automatic chain |
| `DEEPSEEK_API_KEY` | unset | Your personal DeepSeek key (api.deepseek.com); a separate choice, not in the chain by default |
| `AGENTROUTER_HEADERS` | unset | Extra client headers for AgentRouter, as a JSON object |
| `SHORTCUT_LLM_PROVIDER` | `auto` with AgentRouter, else `groq` | `auto` (the chain) or one service: `claude`, `deepseek`, `groq`, `gemini`, `openrouter`, `sambanova` |
| `SHORTCUT_AI_CHAIN` | `claude,deepseek,groq` | Order of the automatic chain |
| `SHORTCUT_GROQ_LLM_MODEL` | `openai/gpt-oss-120b` | Groq model (must support strict JSON schema) |
| `SHORTCUT_GROQ_TPM` | `8000` | Your Groq tier's tokens-per-minute limit; long transcripts are split to fit (`0` = never split) |
| `SHORTCUT_TRANSCRIBER` | `groq` if `GROQ_API_KEY` is set, else `local` | Speech-to-text |
| `SHORTCUT_GROQ_WHISPER_MODEL` | `whisper-large-v3-turbo` | Groq Whisper model |
| `ELEVENLABS_API_KEY` | unset | Enables ElevenLabs in Voice Studio |
| `PEXELS_API_KEY` | unset | Pexels stock footage in Video Maker (free at pexels.com/api) |
| `PIXABAY_API_KEY` | unset | Pixabay stock footage in Video Maker (free at pixabay.com/api/docs) |
| `COVERR_API_KEY` | unset | Coverr stock video in Video Maker (email team@coverr.co for a key) |
| `UNSPLASH_ACCESS_KEY` | unset | Unsplash photos in Video Maker (unsplash.com/developers) |
| `SEARXNG_URL` | unset | Optional private SearXNG endpoint for live web-image research; DuckDuckGo is the fallback |
| `JAMENDO_CLIENT_ID` | unset | Jamendo music search in Video Maker (free at devportal.jamendo.com) |
| `SHORTCUT_CONTACT` | unset | Your email or website, sent to Wikimedia Commons (and stock sites) as their API policy asks |
| `SHORTCUT_KOKORO_MODEL` | `kokoro-v1.0.onnx` | Local voice model file (`kokoro-v1.0.int8.onnx` is smaller but slower on Apple Silicon) |
| `SHORTCUT_WHISPER_MODEL` | `small` | Local model: `tiny`, `base`, `small`, `medium`, `large-v3` |
| `SHORTCUT_WHISPER_DEVICE` | `auto` | `cpu`, `cuda`, or `auto` |
| `SHORTCUT_WHISPER_COMPUTE` | `int8` | faster-whisper compute type |
| `SHORTCUT_FFMPEG` / `SHORTCUT_FFPROBE` | on `PATH` | Tool paths |
| `SHORTCUT_YT_DLP` | the venv's `yt-dlp` | Downloader; upgrade with `.venv/bin/pip install -U "yt-dlp[default]"` if YouTube returns 403 |

The live research and candidate-search workflow is adapted from the MIT-licensed
[ClipCut](https://github.com/adeebahmad01/clipcut); its license notice is kept in
`backend/app/maker/CLIPCUT_LICENSE`.

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
