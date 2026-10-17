"""Make a video from a reviewed script: narration, caption timing, stock footage, final render."""

import hashlib
import json
import logging
import math
import random
import re
import shutil
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path

import requests

from .. import db, tts
from ..config import FFMPEG, FFPROBE, MUSIC_DIR, VIDEOS_DIR
from ..presets import CAPTION_STYLES, NO_CAPTIONS
from ..pipeline import cancel, limits, media, runner
from ..pipeline.render import Look, _write_overlay_track, output_size
from ..pipeline.transcribe import TranscriptionError, language_code, transcribe
from ..schemas import VideoRequest, Word
from . import footage
from .script import fallback_terms

log = logging.getLogger(__name__)

STAGES = {"voice": (0.0, 0.25), "captions": (0.25, 0.4), "footage": (0.4, 0.85), "render": (0.85, 1.0)}
FPS = 30
RATE = 48000
SCENE_GAP = 0.7         # pause between scenes; also ends a caption line
TAIL = 1.0              # silence after the last scene
FADE = 0.3              # fade between scenes
MEDIA_PREFIX = "/video-media/"
MUSIC_SUFFIXES = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}
PARALLEL_SHOTS = 3      # shots fetched and encoded at once; mostly waiting on the network
# Plain-background colours, one pair per scene in turn.
GRADIENTS = [("0x1e3a8a", "0x0f172a"), ("0x4c1d95", "0x111827"), ("0x065f46", "0x0f172a"),
             ("0x9a3412", "0x1c1917"), ("0x155e75", "0x0f172a"), ("0x831843", "0x18181b")]


def video_dir(vid: str) -> Path:
    return VIDEOS_DIR / vid


def media_url(path: Path) -> str:
    return MEDIA_PREFIX + path.relative_to(VIDEOS_DIR).as_posix()


def submit(vid: str) -> None:
    runner.submit_job(vid, lambda: _make(vid), STAGES, db.update_video)


@dataclass
class SceneSpan:
    start: float        # in the narration
    spoken: float       # seconds of speech
    end: float          # start of the next scene


@dataclass
class Shot:
    scene: int
    start: float
    frames: int
    fade_in: bool
    fade_out: bool


def _make(vid: str) -> None:
    record = db.get_video(vid)
    if record is None:
        return
    req = VideoRequest(**record["request"])
    work = video_dir(vid)
    work.mkdir(parents=True, exist_ok=True)
    size = output_size(req.aspect, req.resolution)

    runner.stage(vid, "voice")
    narration, spans = _narrate(req, work, lambda f: runner.stage(vid, "voice", f))
    total = spans[-1].end

    runner.stage(vid, "captions")
    overlay = None
    style = None if req.caption_style == NO_CAPTIONS else CAPTION_STYLES[req.caption_style]
    if style or req.title_overlay:
        words = _caption_words(req, narration, spans, lambda f: runner.stage(vid, "captions", 0.8 * f)) \
            if style else []
        look = Look(style, req.caption_position, req.highlight_color, req.title if req.title_overlay else None)
        shutil.rmtree(work / "overlay", ignore_errors=True)
        overlay = _write_overlay_track(words, 0.0, total, look, req.words_per_line, size, work / "overlay")

    runner.stage(vid, "footage")
    shots = _plan_shots(req, spans)
    pieces, credits = _shoot(vid, req, shots, size, work, lambda f: runner.stage(vid, "footage", f))

    runner.stage(vid, "render")
    version = int(time.time())
    out = work / f"video_{version}.mp4"
    _render(pieces, narration, overlay, _music(req.music), req.music_volume, total, out,
            lambda f: runner.stage(vid, "render", f))
    thumb = work / f"thumb_{version}.jpg"
    media.thumbnail(out, min(2.0, total / 2), thumb, width=640)
    old = db.get_video(vid) or {}
    cancel.check()
    db.update_video(vid, status="done", stage=None, progress=1.0, error=None, video=media_url(out),
                    thumbnail=media_url(thumb), duration=total, credits=credits)
    for url in (old.get("video"), old.get("thumbnail")):
        if url and url != media_url(out) and url != media_url(thumb):
            (VIDEOS_DIR / url.removeprefix(MEDIA_PREFIX)).unlink(missing_ok=True)
    shutil.rmtree(work / "shots", ignore_errors=True)
    shutil.rmtree(work / "overlay", ignore_errors=True)


# --- Narration ------------------------------------------------------------------------


def _narrate(req: VideoRequest, work: Path, on_progress) -> tuple[Path, list[SceneSpan]]:
    """One WAV of every scene read in turn, and where each scene starts. Scene audio is kept,
    keyed by its text and voice, so a retry or re-render does not pay for it twice."""
    voice_dir = work / "voice"
    voice_dir.mkdir(exist_ok=True)
    v = req.voice
    spans: list[SceneSpan] = []
    frames = bytearray()
    t = 0.0
    for i, scene in enumerate(req.scenes):
        key = json.dumps([v.provider, v.voice_id, v.model_id, v.settings.model_dump(), scene.text])
        mp3 = voice_dir / f"{hashlib.sha1(key.encode()).hexdigest()[:16]}.mp3"
        if not mp3.exists():
            cancel.check()
            if v.provider == "elevenlabs":
                audio = tts.elevenlabs.synthesize(scene.text, v.voice_id, v.model_id, v.settings.model_dump())
            else:
                audio = tts.local.synthesize(scene.text, v.voice_id, v.settings.speed)
            mp3.write_bytes(audio)
        wav = voice_dir / f"{mp3.stem}.wav"
        media.run([FFMPEG, "-y", "-v", "error", "-i", str(mp3), "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le",
                   str(wav)])
        with wave.open(str(wav)) as w:
            pcm = w.readframes(w.getnframes())
        wav.unlink()
        spoken = len(pcm) / 2 / RATE
        pause = TAIL if i == len(req.scenes) - 1 else SCENE_GAP
        frames += pcm + b"\0\0" * int(pause * RATE)
        spans.append(SceneSpan(t, spoken, t + spoken + pause))
        t = spans[-1].end
        on_progress((i + 1) / len(req.scenes))
    narration = work / "narration.wav"
    with wave.open(str(narration), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))
    spans[-1].end = len(frames) / 2 / RATE
    return narration, spans


# --- Caption timing ----------------------------------------------------------------------


def _caption_words(req: VideoRequest, narration: Path, spans: list[SceneSpan], on_progress) -> list[Word]:
    """The script's own words, timed by transcribing the narration and matching it to the script.
    If transcription fails, timings are estimated from each scene's length."""
    script = [(i, w) for i, s in enumerate(req.scenes) for w in s.text.split()]
    estimate = _estimated(script, req, spans)
    try:
        transcript = transcribe(narration, spans[-1].end, language_code(req.language), on_progress)
    except TranscriptionError as e:
        log.warning("caption timing estimated: %s", e)
        return estimate
    heard = [w for s in transcript.segments for w in s.words]
    words = align([w for _, w in script], heard, estimate)
    # Keep each scene's captions inside its speech, so no caption line runs across the pause into the next scene.
    out = []
    for (scene, _), w in zip(script, words):
        lo, hi = spans[scene].start, spans[scene].start + spans[scene].spoken + 0.05
        start = min(max(w.start, lo), hi - 0.02)
        out.append(Word(start=start, end=max(min(w.end, hi), start + 0.02), text=w.text))
    return out


def _estimated(script: list[tuple[int, str]], req: VideoRequest, spans: list[SceneSpan]) -> list[Word]:
    """Each scene's words spread over its speech in proportion to their length."""
    out: list[Word] = []
    for i, span in enumerate(spans):
        words = [w for n, w in script if n == i]
        weights = [len(w) + 1 for w in words]
        total, t = sum(weights) or 1, span.start
        for w, weight in zip(words, weights):
            d = span.spoken * weight / total
            out.append(Word(start=t, end=t + d, text=w))
            t += d
    return out


def _norm(text: str) -> str:
    return re.sub(r"[^\w]", "", text.lower())


def align(script: list[str], heard: list[Word], estimate: list[Word]) -> list[Word]:
    """Times for the script's words from what the transcriber heard. Words heard differently
    share the time of what was heard in their place; words not heard at all fall between their
    neighbours. With too few matches (e.g. the wrong language), the estimate is used."""
    a, b = [_norm(w) for w in script], [_norm(w.text) for w in heard]
    times: list[tuple[float, float] | None] = [None] * len(script)
    matched = 0
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            matched += i2 - i1
            for k in range(i2 - i1):
                times[i1 + k] = (heard[j1 + k].start, heard[j1 + k].end)
        elif tag == "replace":
            t0, t1 = heard[j1].start, heard[j2 - 1].end
            weights = [len(script[i]) + 1 for i in range(i1, i2)]
            total, t = sum(weights), t0
            for i, weight in zip(range(i1, i2), weights):
                d = (t1 - t0) * weight / total
                times[i] = (t, t + d)
                t += d
    if not script or matched < 0.3 * len(script):
        return estimate
    i = 0
    while i < len(times):  # runs the transcriber missed
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(times) and times[j] is None:
            j += 1
        lo = times[i - 1][1] if i > 0 else estimate[i].start
        hi = times[j][0] if j < len(times) else estimate[j - 1].end
        if hi <= lo:
            hi = lo + 0.05 * (j - i)
        step = (hi - lo) / (j - i)
        for k in range(i, j):
            times[k] = (lo + (k - i) * step, lo + (k - i + 1) * step)
        i = j
    return [Word(start=t[0], end=max(t[1], t[0] + 0.02), text=w) for w, t in zip(script, times)]


# --- Footage ------------------------------------------------------------------------------


def _plan_shots(req: VideoRequest, spans: list[SceneSpan]) -> list[Shot]:
    """Each scene cut into equal shots no longer than clip_seconds, in whole frames so the
    picture never drifts from the narration."""
    shots = []
    for i, span in enumerate(spans):
        length = span.end - span.start
        n = max(1, math.ceil(length / req.clip_seconds - 0.15))
        cuts = [span.start + length * k / n for k in range(n + 1)]
        for k in range(n):
            frames = round(cuts[k + 1] * FPS) - round(cuts[k] * FPS)
            if frames > 0:
                fade = req.transition == "fade"
                shots.append(Shot(i, cuts[k], frames, fade and k == 0, fade and k == n - 1 and i < len(spans) - 1))
    return shots


class _Picker:
    """Chooses a stock clip per shot: the scene's search terms in turn, clips not used yet first."""

    def __init__(self, providers: list[str], orientation: str, short_side: int):
        self.providers = providers
        self.orientation = orientation
        self.short_side = short_side
        self.results: dict[tuple[str, str, int], list[footage.StockClip]] = {}
        self.used: set[str] = set()

    def _search(self, provider: str, term: str, page: int) -> list[footage.StockClip]:
        key = (provider, term, page)
        if key not in self.results:
            while True:
                try:
                    self.results[key] = footage.search(provider, term, self.orientation, self.short_side, page)
                    break
                except limits.LimitReached as e:
                    if not self._on_limit(provider, e.limit):
                        return []
        return self.results[key]

    def _on_limit(self, provider: str, limit: limits.Limit) -> bool:
        """Wait or switch as the user decides; False if this provider is no longer used."""
        options = [limits.Option(f"{p}:stock", f"{limits.SERVICE_LABELS[p]} stock video",
                                 "Free stock video; has its own limits.", footage.available()[p],
                                 None if footage.available()[p] else
                                 f"Add {footage.KEY_NAME[p]} to backend/.env (free key at {footage.SIGNUP[p]})")
                   for p in ("pexels", "pixabay") if p != provider]
        options.append(limits.Option("none:plain", "Plain background",
                                     "Coloured background for the remaining shots; no limits.", True))
        try:
            choice = limits.handle("footage", limit, options)
        except limits.LimitReached as again:
            raise footage.FootageError(str(again)) from again
        if choice is None:
            return True
        service = choice.partition(":")[0]
        self.providers = [] if service == "none" else [service]
        return False

    def pick(self, terms: list[str], seconds: float, rotate: int) -> footage.StockClip | None:
        terms = terms[rotate % len(terms):] + terms[:rotate % len(terms)] if terms else []
        for page, reuse in ((1, False), (2, False), (1, True)):
            for term in terms:
                for provider in list(self.providers):
                    for clip in self._search(provider, term, page):
                        if clip.duration >= seconds + 0.3 and (reuse or clip.key not in self.used):
                            self.used.add(clip.key)
                            return clip
                    if not self.providers:
                        return None
        return None


@dataclass
class _ShotFile:
    out: Path
    shot: Shot
    clip: footage.StockClip | None
    offset: float
    used_clip: bool = False


def _shoot(vid: str, req: VideoRequest, shots: list[Shot], size: tuple[int, int], work: Path,
           on_progress) -> tuple[list[Path], list[dict]]:
    """Every shot as a small silent mp4 of exactly its frame count, and the credits for the clips used."""
    providers = footage.providers_for(req.footage)
    w, h = size
    orientation = "landscape" if w > h else "portrait" if h > w else "square"
    picker = _Picker(providers, orientation, min(w, h))
    rng = random.Random(vid)
    shots_dir = work / "shots"
    shots_dir.mkdir(exist_ok=True)

    files: list[_ShotFile] = []
    rotate: dict[int, int] = {}
    for n, shot in enumerate(shots):
        seconds = shot.frames / FPS
        scene = req.scenes[shot.scene]
        clip, offset = None, 0.0
        if picker.providers:
            terms = scene.search_terms or fallback_terms(scene.text)
            rotate[shot.scene] = rotate.get(shot.scene, -1) + 1
            clip = picker.pick(terms, seconds, rotate[shot.scene])
        if clip is not None:
            slack = clip.duration - seconds - 0.2
            offset = round(rng.uniform(0, min(slack, 4.0)), 2) if slack > 0 else 0.0
        name = f"{n:04d}-{clip.key if clip else 'plain'}-{offset:.2f}-{shot.frames}" \
               f"{'i' if shot.fade_in else ''}{'o' if shot.fade_out else ''}"
        files.append(_ShotFile(shots_dir / f"{name}.mp4", shot, clip, offset))
        on_progress(0.1 * (n + 1) / len(shots))  # choosing clips; encoding is the other 90%

    job = cancel.current()
    downloads = _Downloads(work / "downloads")
    done = 0

    def encode(f: _ShotFile) -> None:
        with cancel.bound(job) if job else nullcontext():
            if f.out.exists() and video_frames(f.out) == f.shot.frames:
                f.used_clip = f.clip is not None
                return
            partial = f.out.with_suffix(".part.mp4")
            if f.clip is not None:
                try:  # only the seconds needed are read from the site
                    _encode_shot(f, ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
                                     "-i", f.clip.link], size, partial)
                    f.used_clip = True
                except media.MediaError as e:
                    log.warning("shot %s: reading %s failed (%s); downloading it", f.out.name, f.clip.link,
                                str(e).splitlines()[-1])
                    try:
                        _encode_shot(f, ["-i", str(downloads.get(f.clip))], size, partial)
                        f.used_clip = True
                    except (media.MediaError, footage.FootageError) as e2:
                        log.warning("shot %s: using a plain background (%s)", f.out.name, str(e2).splitlines()[-1])
            if not f.used_clip:
                _encode_shot(replace(f, clip=None), [], size, partial)
            partial.rename(f.out)

    with ThreadPoolExecutor(max_workers=PARALLEL_SHOTS) as pool:
        for _ in pool.map(encode, files):
            done += 1
            on_progress(0.1 + 0.9 * done / len(files))
    shutil.rmtree(work / "downloads", ignore_errors=True)
    credits = {f.clip.key: {"provider": f.clip.provider, "author": f.clip.author, "page": f.clip.page}
               for f in files if f.used_clip and f.clip}
    return [f.out for f in files], list(credits.values())


def _encode_shot(f: _ShotFile, source: list[str], size: tuple[int, int], dest: Path) -> None:
    w, h = size
    seconds = f.shot.frames / FPS
    fades = [f"fade=t=in:st=0:d={FADE}"] if f.shot.fade_in else []
    if f.shot.fade_out:
        fades.append(f"fade=t=out:st={max(0.0, seconds - FADE):.3f}:d={FADE}")
    if f.clip is None:
        c0, c1 = GRADIENTS[f.shot.scene % len(GRADIENTS)]
        inputs = ["-f", "lavfi", "-i", f"gradients=s={w}x{h}:c0={c0}:c1={c1}:speed=0.004:rate={FPS}"]
        vf = ["setsar=1"]
    else:
        inputs = ["-ss", f"{f.offset:.2f}", "-t", f"{seconds + 1:.3f}", *source]
        vf = [f"scale={w}:{h}:force_original_aspect_ratio=increase", f"crop={w}:{h}", "setsar=1", f"fps={FPS}",
              "tpad=stop_mode=clone:stop_duration=5"]
    media.run([FFMPEG, "-y", "-v", "error", *inputs, "-vf", ",".join(vf + fades), "-frames:v", str(f.shot.frames),
               "-an", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
               "-f", "mp4", str(dest)])
    # A link that breaks off mid-read can still leave a playable but short file; joined, it would cut the video short.
    got = video_frames(dest)
    if got != f.shot.frames:
        raise media.MediaError(f"shot has {got} of {f.shot.frames} frames")


def video_frames(path: Path) -> int:
    out = media.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_packets",
                     "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)])
    try:
        return int(out.strip().split(",")[0])
    except ValueError:
        return 0


class _Downloads:
    """Whole-file downloads, for links ffmpeg cannot read in parts. One download per clip."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.locks: dict[str, threading.Lock] = {}
        self.lock = threading.Lock()

    def get(self, clip: footage.StockClip) -> Path:
        with self.lock:
            lock = self.locks.setdefault(clip.key, threading.Lock())
        with lock:
            path = self.folder / f"{clip.key}.mp4"
            if path.exists():
                return path
            self.folder.mkdir(parents=True, exist_ok=True)
            part = path.with_suffix(".part")
            try:
                with requests.get(clip.link, stream=True, timeout=60) as r:
                    r.raise_for_status()
                    with part.open("wb") as out:
                        for chunk in r.iter_content(1 << 20):
                            cancel.check()
                            out.write(chunk)
            except requests.RequestException as e:
                part.unlink(missing_ok=True)
                raise footage.FootageError(f"download failed: {e.__class__.__name__}") from e
            part.rename(path)
            return path


# --- Final render --------------------------------------------------------------------------


def _music(name: str | None) -> Path | None:
    if not name:
        return None
    path = MUSIC_DIR / name
    if path.parent != MUSIC_DIR or not path.is_file():
        raise media.MediaError(f"Background music {name!r} is no longer in the music library")
    return path


def _render(pieces: list[Path], narration: Path, overlay: Path | None, music: Path | None, volume: float,
            total: float, out: Path, on_progress) -> None:
    listing = out.with_name("shots.ffconcat")
    listing.write_text("ffconcat version 1.0\n" + "".join(f"file 'shots/{p.name}'\n" for p in pieces))
    cmd = [FFMPEG, "-y", "-v", "error", "-nostats", "-progress", "pipe:1",
           "-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(narration)]
    graph, n = [], 2
    video = "[0:v]"
    if overlay is not None:
        cmd += ["-f", "concat", "-safe", "0", "-i", str(overlay)]
        graph.append(f"[{n}:v]format=rgba[cap];[0:v][cap]overlay=0:0:eof_action=pass:format=auto[v]")
        video, n = "[v]", n + 1
    audio = "1:a"
    if music is not None:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        fade_at = max(0.0, total - 3)
        graph.append(f"[{n}:a]aformat=channel_layouts=stereo,volume={volume:.3f},"
                     f"afade=t=out:st={fade_at:.2f}:d=3[bg];[1:a]aformat=channel_layouts=stereo[vo];"
                     f"[vo][bg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]")
        audio = "[a]"
    if graph:
        cmd += ["-filter_complex", ";".join(graph)]
    cmd += ["-map", video if video != "[0:v]" else "0:v", "-map", audio, "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-movflags", "+faststart", str(out)]

    def on_line(line: str) -> None:
        if line.startswith("out_time_us=") and total > 0:
            try:
                on_progress(min(1.0, int(line.split("=", 1)[1]) / 1e6 / total))
            except ValueError:
                pass
    try:
        media.run(cmd, on_line)
    finally:
        listing.unlink(missing_ok=True)
    expected = round(total * FPS)
    if (got := video_frames(out)) < expected - 2:
        raise media.MediaError(f"The video came out {got / FPS:.1f}s long instead of {total:.1f}s; please retry")


# --- Music library -----------------------------------------------------------------------


def music_library() -> list[dict]:
    return [{"name": p.name, "url": media_url(p), "size": p.stat().st_size}
            for p in sorted(MUSIC_DIR.iterdir()) if p.suffix.lower() in MUSIC_SUFFIXES and p.is_file()]


def music_name(filename: str) -> str:
    stem, suffix = Path(filename).stem, Path(filename).suffix.lower()
    if suffix not in MUSIC_SUFFIXES:
        raise ValueError(f"Unsupported audio type {suffix or '(none)'}; use {', '.join(sorted(MUSIC_SUFFIXES))}")
    stem = re.sub(r"[^\w .()-]+", " ", stem).strip()[:80] or "track"
    return stem + suffix
