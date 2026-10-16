"""Speech-to-text with word timestamps: Groq hosted Whisper or local faster-whisper."""

import os
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from ..config import (FFMPEG, GROQ_WHISPER_MODEL, TRANSCRIBER, WHISPER_COMPUTE, WHISPER_DEVICE,
                      WHISPER_MODEL)
from ..schemas import Segment, Transcript, Word
from . import cancel, limits
from .media import run

# Groq's free tier caps uploads at 25 MB. 10 minutes of 16 kHz mono AAC at 32 kbps is ~2.4 MB,
# which leaves plenty of headroom while keeping progress updates frequent.
GROQ_CHUNK_SECONDS = 600


class TranscriptionError(RuntimeError):
    pass


def transcribe(audio: Path, duration: float, language: str | None, on_progress: Callable[[float], None]) -> Transcript:
    """Transcribe with the configured engine. When it hits a usage limit, limits.handle() waits
    or asks the user; after a switch, the rest of the audio is done by the chosen engine and
    what is already transcribed is kept."""
    engine = limits.override("transcribe") or _default_engine()
    done = _Done(language)
    while done.offset < duration:
        service, _, model = engine.partition(":")
        try:
            if service == "groq":
                _transcribe_groq(audio, duration, model, done, on_progress)
            elif service == "local":
                _transcribe_local(audio, duration, done, on_progress)
            else:
                raise TranscriptionError(f"Unknown SHORTCUT_TRANSCRIBER {TRANSCRIBER!r} (use 'groq' or 'local')")
        except limits.LimitReached as e:
            try:
                engine = limits.handle("transcribe", e.limit, transcription_options(engine)) or engine
            except limits.LimitReached as again:  # no project to ask
                raise TranscriptionError(str(again)) from again
    return Transcript(language=done.language or "unknown", duration=duration, segments=done.segments)


@dataclass
class _Done:
    """Transcription so far: everything before `offset` seconds is in `segments`."""

    language: str | None
    offset: float = 0.0
    segments: list[Segment] = field(default_factory=list)


def _default_engine() -> str:
    return f"groq:{GROQ_WHISPER_MODEL}" if TRANSCRIBER == "groq" else f"local:{WHISPER_MODEL}"


def transcription_options(current: str) -> list[limits.Option]:
    groq_key = bool(os.environ.get("GROQ_API_KEY"))
    reason = None if groq_key else "Add GROQ_API_KEY to backend/.env"
    options = [
        limits.Option("groq:whisper-large-v3-turbo", "Groq whisper-large-v3-turbo",
                      "Fast hosted Whisper. Each Groq model has its own limits.", groq_key, reason),
        limits.Option("groq:whisper-large-v3", "Groq whisper-large-v3",
                      "Slightly more accurate hosted Whisper. Each Groq model has its own limits.", groq_key, reason),
        limits.Option(f"local:{WHISPER_MODEL}", f"Whisper {WHISPER_MODEL} on this computer",
                      "Free, no limits; slower. The first use downloads the model.", True),
    ]
    return [o for o in options if o.id != current]


# --- Groq -----------------------------------------------------------------------


def _field(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _transcribe_groq(audio: Path, duration: float, model: str, done: _Done,
                     on_progress: Callable[[float], None]) -> None:
    import groq

    client = groq.Groq(max_retries=0)  # rate limits are handled by limits.handle()
    with tempfile.TemporaryDirectory() as tmp:
        while done.offset < duration:
            offset = done.offset
            length = min(GROQ_CHUNK_SECONDS, duration - offset)
            chunk = Path(tmp) / f"chunk_{int(offset)}.m4a"
            run([FFMPEG, "-y", "-v", "error", "-ss", f"{offset:.3f}", "-t", f"{length:.3f}", "-i", str(audio),
                 "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", "32k", str(chunk)])
            try:
                with chunk.open("rb") as f:
                    raw = client.audio.transcriptions.with_raw_response.create(
                        file=(chunk.name, f.read()),
                        model=model,
                        response_format="verbose_json",
                        timestamp_granularities=["word", "segment"],
                        language=done.language,
                        temperature=0.0,
                    )
            except groq.AuthenticationError as e:
                raise TranscriptionError("Groq API key is missing or invalid (set GROQ_API_KEY)") from e
            except groq.RateLimitError as e:
                body = e.body.get("error", e.body) if isinstance(e.body, dict) else None
                message = body.get("message", str(e)) if isinstance(body, dict) else str(e)
                raise limits.LimitReached(limits.from_groq("groq", model, message, e.response.headers)) from e
            except groq.APIStatusError as e:
                raise TranscriptionError(f"Groq transcription error {e.status_code}: {e.message}") from e
            except groq.APIConnectionError as e:
                raise TranscriptionError("Could not reach the Groq API") from e
            limits.record("groq", model, raw.headers)
            result = raw.parse()

            done.language = done.language or _field(result, "language")
            done.segments.extend(_groq_segments(result, offset))
            done.offset = offset + length
            on_progress(min(done.offset / duration, 1.0))


def _groq_segments(result: Any, offset: float) -> list[Segment]:
    raw_segments = _field(result, "segments") or []
    raw_words = _field(result, "words") or []
    if raw_segments and not raw_words:
        raise TranscriptionError("Groq response did not include word timestamps")

    words = [
        Word(start=float(_field(w, "start")) + offset, end=float(_field(w, "end")) + offset,
             text=str(_field(w, "word", "")).strip())
        for w in raw_words
        if str(_field(w, "word", "")).strip()
    ]
    segments = []
    for s in raw_segments:
        start, end = float(_field(s, "start")) + offset, float(_field(s, "end")) + offset
        # Word timestamps are reported separately; attach each word to the segment containing its midpoint.
        seg_words = [w for w in words if start <= (w.start + w.end) / 2 < end]
        segments.append(Segment(start=start, end=end, text=str(_field(s, "text", "")).strip(), words=seg_words))
    return segments


# --- Local faster-whisper ---------------------------------------------------------


@lru_cache(maxsize=1)
def _model():
    from faster_whisper import WhisperModel

    return WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)


def _transcribe_local(audio: Path, duration: float, done: _Done, on_progress: Callable[[float], None]) -> None:
    """faster-whisper on this computer, from done.offset to the end."""
    with tempfile.TemporaryDirectory() as tmp:
        source, offset = audio, done.offset
        if offset > 0:  # continue where another engine stopped
            source = Path(tmp) / "rest.wav"
            run([FFMPEG, "-y", "-v", "error", "-ss", f"{offset:.3f}", "-i", str(audio), "-ac", "1", "-ar", "16000",
                 str(source)])
        segments_iter, info = _model().transcribe(
            str(source), language=done.language, word_timestamps=True, vad_filter=True
        )
        for seg in segments_iter:
            cancel.check()
            words = [
                Word(start=w.start + offset, end=w.end + offset, text=w.word.strip())
                for w in (seg.words or [])
                if w.word.strip()
            ]
            done.segments.append(Segment(start=seg.start + offset, end=seg.end + offset, text=seg.text.strip(),
                                         words=words))
            if duration > 0:
                on_progress(min((seg.end + offset) / duration, 1.0))
    done.language = done.language or info.language
    done.offset = duration
