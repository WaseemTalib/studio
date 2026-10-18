"""Make a video from a reviewed script: narration, caption timing, stock footage, final render."""

import hashlib
import json
import logging
import re
import shutil
import time
import wave
from difflib import SequenceMatcher
from pathlib import Path

from .. import db, tts
from ..config import FFMPEG, VIDEOS_DIR
from ..presets import CAPTION_STYLES, NO_CAPTIONS
from ..pipeline import cancel, media, runner
from ..pipeline.render import Look, _write_overlay_track, output_size
from ..pipeline.transcribe import TranscriptionError, language_code, transcribe
from ..schemas import VideoRequest, Word, footage_sources
from . import footage, music, shots

log = logging.getLogger(__name__)

STAGES = {"voice": (0.0, 0.25), "captions": (0.25, 0.4), "footage": (0.4, 0.85), "render": (0.85, 1.0)}
RATE = 48000
SCENE_GAP = 0.7         # pause between scenes; also ends a caption line
TAIL = 1.0              # silence after the last scene
MEDIA_PREFIX = "/video-media/"


def video_dir(vid: str) -> Path:
    return VIDEOS_DIR / vid


def media_url(path: Path) -> str:
    return MEDIA_PREFIX + path.relative_to(VIDEOS_DIR).as_posix()


def submit(vid: str) -> None:
    runner.submit_job(vid, lambda: _make(vid), STAGES, db.update_video)


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
    plan = shots.plan(req.scenes, spans, req.clip_seconds, req.cards, req.transition == "fade")
    cards = [(s.start, s.start + s.seconds) for s in plan if s.kind != "footage"]

    runner.stage(vid, "captions")
    overlay = None
    style = None if req.caption_style == NO_CAPTIONS else CAPTION_STYLES[req.caption_style]
    if style or req.title_overlay:
        words = _caption_words(req, narration, spans, lambda f: runner.stage(vid, "captions", 0.8 * f)) \
            if style else []
        # No captions over fact and quote cards: the card is the text on screen.
        words = [w for w in words if not any(a <= (w.start + w.end) / 2 < b for a, b in cards)]
        look = Look(style, req.caption_position, req.highlight_color, req.title if req.title_overlay else None,
                    req.caption_size / 100, req.caption_offset / 100)
        shutil.rmtree(work / "overlay", ignore_errors=True)
        overlay = _write_overlay_track(words, 0.0, total, look, req.words_per_line, size, work / "overlay")

    runner.stage(vid, "footage")
    picker = shots.Picker(footage.providers_for(footage_sources(req)), footage.orientation_of(req.aspect), min(size),
                          req.photos, req.ai_service)
    chosen = shots.choose(req.scenes, plan, picker)
    pieces, credits = shots.shoot(vid, req.scenes, plan, chosen, size, work, req.highlight_color,
                                  lambda f: runner.stage(vid, "footage", f))

    runner.stage(vid, "render")
    version = int(time.time())
    out = work / f"video_{version}.mp4"
    track, track_credit = _music(req, total)
    if track_credit:
        credits.append({k: v for k, v in track_credit.items() if k in ("provider", "author", "page", "license", "title")})
    _render(pieces, narration, overlay, track, req.music_volume, total, out,
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


def _narrate(req: VideoRequest, work: Path, on_progress) -> tuple[Path, list[shots.Span]]:
    """One WAV of every scene read in turn, and where each scene starts. Scene audio is kept,
    keyed by its text and voice, so a retry or re-render does not pay for it twice."""
    voice_dir = work / "voice"
    voice_dir.mkdir(exist_ok=True)
    v = req.voice
    spans: list[shots.Span] = []
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
        spans.append(shots.Span(t, spoken, t + spoken + pause))
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


def _caption_words(req: VideoRequest, narration: Path, spans: list[shots.Span], on_progress) -> list[Word]:
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


def _estimated(script: list[tuple[int, str]], req: VideoRequest, spans: list[shots.Span]) -> list[Word]:
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


# --- Final render --------------------------------------------------------------------------


def _music(req: VideoRequest, total: float) -> tuple[Path | None, dict | None]:
    """The background track and its credit: the chosen one, a random one from the library, or one
    found to match the mood."""
    try:
        if req.music_mode == "random":
            name = music.pick_random()
        elif req.music_mode == "auto":
            name = music.auto(req.music_mood or req.title, total)
        else:
            name = req.music
        if not name:
            return None, None
        return music.path_of(name), music.credit_of(name)
    except music.MusicError as e:
        raise media.MediaError(str(e)) from e


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
    expected = round(total * shots.FPS)
    if (got := shots.video_frames(out)) < expected - 2:
        raise media.MediaError(f"The video came out {got / shots.FPS:.1f}s long instead of {total:.1f}s; please retry")
