"""Speech-to-text with word timestamps: Groq hosted Whisper or local faster-whisper."""

import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from ..config import (FFMPEG, GROQ_WHISPER_MODEL, TRANSCRIBER, WHISPER_COMPUTE, WHISPER_DEVICE,
                      WHISPER_MODEL)
from ..schemas import Segment, Transcript, Word
from .media import run

# Groq's free tier caps uploads at 25 MB. 10 minutes of 16 kHz mono AAC at 32 kbps is ~2.4 MB,
# which leaves plenty of headroom while keeping progress updates frequent.
GROQ_CHUNK_SECONDS = 600


class TranscriptionError(RuntimeError):
    pass


def transcribe(audio: Path, duration: float, language: str | None, on_progress: Callable[[float], None]) -> Transcript:
    if TRANSCRIBER == "groq":
        return _transcribe_groq(audio, duration, language, on_progress)
    if TRANSCRIBER == "local":
        return _transcribe_local(audio, duration, language, on_progress)
    raise TranscriptionError(f"Unknown SHORTCUT_TRANSCRIBER {TRANSCRIBER!r} (use 'groq' or 'local')")


# --- Groq -----------------------------------------------------------------------


def _field(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _transcribe_groq(audio: Path, duration: float, language: str | None,
                     on_progress: Callable[[float], None]) -> Transcript:
    import groq

    client = groq.Groq()
    segments: list[Segment] = []
    detected = language
    with tempfile.TemporaryDirectory() as tmp:
        offset = 0.0
        while offset < duration:
            length = min(GROQ_CHUNK_SECONDS, duration - offset)
            chunk = Path(tmp) / f"chunk_{int(offset)}.m4a"
            run([FFMPEG, "-y", "-v", "error", "-ss", f"{offset:.3f}", "-t", f"{length:.3f}", "-i", str(audio),
                 "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", "32k", str(chunk)])
            try:
                with chunk.open("rb") as f:
                    result = client.audio.transcriptions.create(
                        file=(chunk.name, f.read()),
                        model=GROQ_WHISPER_MODEL,
                        response_format="verbose_json",
                        timestamp_granularities=["word", "segment"],
                        language=language,
                        temperature=0.0,
                    )
            except groq.AuthenticationError as e:
                raise TranscriptionError("Groq API key is missing or invalid (set GROQ_API_KEY)") from e
            except groq.RateLimitError as e:
                raise TranscriptionError("Groq transcription rate limit reached; retry later") from e
            except groq.APIStatusError as e:
                raise TranscriptionError(f"Groq transcription error {e.status_code}: {e.message}") from e
            except groq.APIConnectionError as e:
                raise TranscriptionError("Could not reach the Groq API") from e

            detected = detected or _field(result, "language")
            segments.extend(_groq_segments(result, offset))
            offset += length
            on_progress(min(offset / duration, 1.0))

    return Transcript(language=detected or "unknown", duration=duration, segments=segments)


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


def _transcribe_local(audio: Path, duration: float, language: str | None,
                      on_progress: Callable[[float], None]) -> Transcript:
    segments_iter, info = _model().transcribe(
        str(audio), language=language, word_timestamps=True, vad_filter=True
    )
    segments: list[Segment] = []
    for seg in segments_iter:
        words = [
            Word(start=w.start, end=w.end, text=w.word.strip())
            for w in (seg.words or [])
            if w.word.strip()
        ]
        segments.append(Segment(start=seg.start, end=seg.end, text=seg.text.strip(), words=words))
        if duration > 0:
            on_progress(min(seg.end / duration, 1.0))
    return Transcript(language=info.language, duration=duration, segments=segments)
