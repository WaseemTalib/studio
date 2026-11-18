# Shortcut Studio

Turn long videos into short, captioned clips.

Pipeline: **import** (upload or link via yt-dlp) → **transcribe** (faster-whisper, word timestamps) →
**pick highlights** (Groq or another free LLM platform, structured output) → **render** (ffmpeg cut + reframe to 9:16 / 1:1 / 16:9,
burned-in captions) → clip library with virality score, hook, reasoning, hashtags, and download.

## Set up on a new machine

### 1. Install the tools

| Tool | Version | macOS (Homebrew) | Ubuntu / Debian | Windows |
|---|---|---|---|---|
| Git | any | `brew install git` | `sudo apt install git` | [git-scm.com](https://git-scm.com) |
| Python | 3.11 or newer (3.10 works, but yt-dlp is dropping it) | `brew install python@3.12` | `sudo apt install python3 python3-venv` | [python.org](https://www.python.org/downloads/) (tick "Add to PATH") |
| Node.js | 20 or newer | `brew install node` | [nodejs.org](https://nodejs.org) or `nvm` | [nodejs.org](https://nodejs.org) |
| ffmpeg + ffprobe | any recent | `brew install ffmpeg` | `sudo apt install ffmpeg` | `winget install Gyan.FFmpeg` |

yt-dlp is installed with the backend's Python packages; you do not install it separately. Check the tools with:

```sh
git --version && python3 --version && node --version && ffmpeg -version | head -1
```

### 2. Get the code

```sh
git clone https://github.com/WaseemTalib/studio.git
cd studio
```

### 3. Backend (http://127.0.0.1:8000)

```sh
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Open `backend/.env` and add at least one AI key. The free `GROQ_API_KEY` (console.groq.com) is enough for
transcription and highlights; every other key is optional (see [Configuration](#configuration-environment)).
Then start it:

```sh
.venv/bin/uvicorn app.main:app --reload --timeout-graceful-shutdown 3
```

Leave this terminal open.

On **Windows** (PowerShell), the venv's programs are in `.venv\Scripts`:

```powershell
cd backend
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
.venv\Scripts\uvicorn app.main:app --reload --timeout-graceful-shutdown 3
```

### 4. Frontend (http://localhost:5173), in a second terminal

```sh
cd frontend
npm install
npm run dev
```

### 5. Open the app and create the admin

Go to **http://localhost:5173**. On a fresh machine there are no users yet, so the first page asks you to create the
admin account. You can also set `SHORTCUT_AUTH_USER` and `SHORTCUT_AUTH_PASSWORD` in `backend/.env` before the
first start; then that account is created and you just log in. Add more users under **Settings**.

### Bring your data from another machine (optional)

Users, projects, clips, voices, videos and downloaded models all live in `backend/data/`, and keys in
`backend/.env`. Neither is in git. To move everything, stop the backend on both machines and copy those two over:

```sh
# on the old machine, from the studio folder
tar czf studio-data.tgz backend/data backend/.env
# copy studio-data.tgz to the new machine, then in its studio folder:
tar xzf studio-data.tgz
```

Skip `backend/data/` to start fresh. To keep users and projects but not the large media, copy just
`backend/data/studio.db` (projects whose media is missing will show without videos).

### Updating

```sh
git pull
backend/.venv/bin/pip install -r backend/requirements.txt
(cd frontend && npm install)
```

Then restart the backend. On a server, `./studio.sh update` does all of this.

### Common problems

| Problem | Fix |
|---|---|
| `ffmpeg not found` | Install ffmpeg (step 1), or set `SHORTCUT_FFMPEG` / `SHORTCUT_FFPROBE` in `backend/.env` to its full path |
| `pip install` fails building a package | Use Python 3.11 or 3.12; very new Python versions sometimes lack ready-made packages |
| Frontend shows "Failed to fetch" or a blank page | The backend is not running, or runs on another port; Vite expects `http://127.0.0.1:8000` (override with `SHORTCUT_API` in `frontend/.env`) |
| Port 8000 or 5173 already in use | Stop the other program, or start uvicorn with `--port 8001` and set `SHORTCUT_API=http://127.0.0.1:8001` in `frontend/.env` |
| Forgot the admin password | Stop the backend, run `backend/.venv/bin/python -c "import sqlite3; c = sqlite3.connect('backend/data/studio.db'); c.execute('DELETE FROM sessions'); c.execute('DELETE FROM users'); c.commit()"` from the studio folder, then start it and create the admin again. Projects are kept |
| YouTube: "Sign in to confirm you're not a bot" | Happens on servers, not home connections; see [YouTube on a server](#youtube-on-a-server) |

## Live server

On a server one process runs everything: after `npm run build` the backend also serves the frontend.

```sh
cd frontend && npm ci && npm run build && cd ..
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && cd ..
cp backend/.env.example backend/.env   # add keys, plus SHORTCUT_PORT (a free port) and SHORTCUT_AUTH_PASSWORD
./studio.sh start                      # also: stop | restart | status | update (git pull + rebuild + restart)
./studio.sh searxng                    # optional: SearXNG image and video search for Video Maker
```

Point the domain at it with a reverse proxy to `http://127.0.0.1:<SHORTCUT_PORT>` (for Apache, in the site's
`.htaccess`: `RewriteEngine On` and `RewriteRule ^(.*)$ http://127.0.0.1:8000/$1 [P,L]`). Add
`* * * * * /path/to/studio.sh start >/dev/null 2>&1` to the crontab to bring it back after a crash or reboot.
Set `SHORTCUT_AUTH_PASSWORD` on any public server: on first start it creates the admin account
(`SHORTCUT_AUTH_USER`, default `admin`). Without it, the first visitor is asked to create the admin, so do that
before the site is public.

### YouTube on a server

On your own computer YouTube links just work. On a server they often fail with "Sign in to confirm you're not a
bot": YouTube distrusts data-centre IP addresses, whatever the code does. Give yt-dlp cookies from a logged-in
YouTube account (a spare Google account, not your main one):

1. Close all private windows, open a new private/incognito window and log in to youtube.com.
2. In the same tab open `https://www.youtube.com/robots.txt`, then export the cookies in Netscape `cookies.txt`
   format (for example with the "Get cookies.txt LOCALLY" extension, allowed in private windows).
3. Close the private window straight away, without logging out (logging out cancels the cookies).
4. Upload the file to the server as `backend/data/youtube-cookies.txt` (or set `SHORTCUT_YT_COOKIES`). It is
   used from the next download; no restart needed, and git ignores it.

Check it on the server with:

```sh
backend/.venv/bin/yt-dlp --cookies backend/data/youtube-cookies.txt --skip-download --print title "https://youtu.be/jNQXAC9IVRw"
```

YouTube cancels exported sessions now and then; when the error returns, export again. If even fresh cookies fail,
YouTube is blocking the server's address: upload the video file instead of pasting the link. yt-dlp also needs a
JavaScript runtime for YouTube; it uses deno if installed, otherwise node.

## Users and access

Everyone logs in. Admins add users under **Settings**, pick each user's tools (Turn to Shorts, Voice Studio,
Video Maker, Video Director, Stickman Director), reset passwords, disable or delete accounts. Admins can use every
tool. Users who are not admins only see their own tools, and the API refuses the others. **Log out** is in the
top bar. A session ends after 24 hours without activity (`SHORTCUT_SESSION_IDLE_HOURS`); a tab left open but
unused does not keep it alive. Projects are shared: everyone with access to a tool sees all of its work.

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
4. **Footage**: use live Openverse research, web image search through SearXNG or DuckDuckGo, SearXNG image and
   video metasearch with engines you choose, YouTube search,
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

### SearXNG metasearch (optional)

Point `SEARXNG_URL` at your own [SearXNG](https://github.com/searxng/searxng) instance to unlock two footage
sources: **SearXNG images** and **SearXNG video** (Vimeo, Dailymotion, PeerTube, YouTube, Bing/Google Videos and
whatever else the instance enables). Video results are fetched with yt-dlp, a segment at a time, like YouTube;
YouTube results join the YouTube source. When either is ticked, Video Maker shows whether the instance answers
and lets you limit the search to chosen engines (none ticked: the instance's defaults). SearXNG runs as its own
service (it is AGPL-licensed, so its code is not copied into this one). On a server, one command installs it in
`backend/data/searxng` (no sudo or Docker; if the server's Python is older than 3.11 it downloads its own):

```bash
./studio.sh searxng
```

This sets `SEARXNG_URL` in `backend/.env` and restarts the app. From then on `start` (and so the cron line),
`stop`, `restart`, `status` and `update` look after SearXNG too. To use an instance you already run instead, set
`SEARXNG_URL` yourself; it must allow JSON output (`search: formats: [html, json]` in its `settings.yml`) and,
being private, should have `server: limiter: false`.

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
| `SEARXNG_URL` | unset | Your SearXNG instance: the SearXNG images and video sources, and the Web source's first choice (DuckDuckGo is the fallback); needs JSON output on (see SearXNG metasearch) |
| `JAMENDO_CLIENT_ID` | unset | Jamendo music search in Video Maker (free at devportal.jamendo.com) |
| `SHORTCUT_CONTACT` | unset | Your email or website, sent to Wikimedia Commons (and stock sites) as their API policy asks |
| `SHORTCUT_KOKORO_MODEL` | `kokoro-v1.0.onnx` | Local voice model file (`kokoro-v1.0.int8.onnx` is smaller but slower on Apple Silicon) |
| `SHORTCUT_WHISPER_MODEL` | `small` | Local model: `tiny`, `base`, `small`, `medium`, `large-v3` |
| `SHORTCUT_WHISPER_DEVICE` | `auto` | `cpu`, `cuda`, or `auto` |
| `SHORTCUT_WHISPER_COMPUTE` | `int8` | faster-whisper compute type |
| `SHORTCUT_FFMPEG` / `SHORTCUT_FFPROBE` | on `PATH` | Tool paths; when missing, `./studio.sh update` downloads a standalone Linux build into `backend/data/bin`, which the app also checks |
| `SHORTCUT_AUTH_USER` / `SHORTCUT_AUTH_PASSWORD` | `admin` / unset | First admin, created on start when there are no users yet |
| `SHORTCUT_SESSION_IDLE_HOURS` | `24` | Hours without activity before a login ends |
| `SHORTCUT_HOST` / `SHORTCUT_PORT` | `127.0.0.1` / `8000` | Where `python -m app` and `./studio.sh` listen |
| `SHORTCUT_YT_COOKIES` | `backend/data/youtube-cookies.txt` | YouTube cookies for servers YouTube blocks (used when the file exists) |
| `SHORTCUT_YT_JS_RUNTIMES` | deno if installed, else node | JavaScript runtime yt-dlp uses for YouTube |
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
