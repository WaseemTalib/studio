"""API and pipeline data contracts."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .presets import CAPTION_STYLES, GENRES, NO_CAPTIONS

Aspect = Literal["9:16", "1:1", "16:9"]
Position = Literal["top", "middle", "bottom"]
# auto: pick from detected faces. fill: centre crop. track: crop around the speaker.
# split: two side-by-side people stacked top/bottom. fit: whole frame over a blurred fill.
Layout = Literal["auto", "fill", "track", "split", "fit"]
Resolution = Literal["720p", "1080p", "4k"]
# clip: AI picks highlights. full: keep the whole video (or timeframe), only caption and reframe.
Mode = Literal["clip", "full"]
ClipModel = Literal["fast", "quality"]


class LookOptions(BaseModel):
    """How a clip looks. Set per project, copied to each clip, editable per clip."""

    aspect: Aspect = "9:16"
    layout: Layout = "auto"
    caption_style: str = "karaoke"
    caption_position: Position = "bottom"
    highlight_color: str = CAPTION_STYLES["karaoke"].default_highlight
    words_per_line: int = Field(default=3, ge=1, le=6)
    title_overlay: bool = False
    auto_zoom: bool = False          # alternate gentle punch-in zooms between sentences
    resolution: Resolution = "1080p"

    @field_validator("caption_style")
    @classmethod
    def _style(cls, v: str) -> str:
        if v != NO_CAPTIONS and v not in CAPTION_STYLES:
            raise ValueError(f"unknown caption style {v!r}")
        return v

    @field_validator("highlight_color")
    @classmethod
    def _color(cls, v: str) -> str:
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            raise ValueError("highlight_color must be #rrggbb")
        return v.lower()


class ProjectOptions(LookOptions):
    mode: Mode = "clip"
    clip_model: ClipModel = "quality"
    instructions: str = Field(default="", max_length=1000)  # "include specific moments" prompt
    clip_count: int = Field(default=6, ge=1, le=20)
    min_seconds: int = Field(default=15, ge=5, le=180)
    max_seconds: int = Field(default=90, ge=10, le=300)
    genre: str = "auto"
    topics: list[str] = Field(default_factory=list, max_length=10)
    range_start: float | None = Field(default=None, ge=0)
    range_end: float | None = Field(default=None, gt=0)
    language: str | None = None  # None lets Whisper detect it

    @field_validator("genre")
    @classmethod
    def _genre(cls, v: str) -> str:
        if v not in GENRES:
            raise ValueError(f"unknown genre {v!r}")
        return v

    @field_validator("topics")
    @classmethod
    def _topics(cls, v: list[str]) -> list[str]:
        return [t.strip()[:80] for t in v if t.strip()]

    @model_validator(mode="after")
    def _ordered(self) -> "ProjectOptions":
        if self.min_seconds >= self.max_seconds:
            raise ValueError("min_seconds must be less than max_seconds")
        if self.range_start is not None and self.range_end is not None and self.range_start >= self.range_end:
            raise ValueError("range_start must be before range_end")
        return self


class Word(BaseModel):
    start: float
    end: float
    text: str


class Segment(BaseModel):
    start: float
    end: float
    text: str
    words: list[Word]


class Transcript(BaseModel):
    language: str
    duration: float
    segments: list[Segment]


# --- LLM structured output ----------------------------------------------------
# extra="forbid" emits additionalProperties: false, which Groq strict mode requires.
# Clips are chosen as sentence index ranges, so cuts always land between sentences.


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_sentence: int = Field(description="Index of the first sentence of the clip (the S number)")
    end_sentence: int = Field(description="Index of the last sentence of the clip, inclusive")
    topic: str = Field(description="The topic of the clip in 2-5 words")
    title: str = Field(description="Short, punchy title for the clip (max ~60 characters)")
    hook: str = Field(description="The opening line that grabs attention in the first seconds")
    score: int = Field(description="Predicted virality from 0 to 100")
    reason: str = Field(description="One or two sentences on why this moment works as a standalone short")
    hashtags: list[str] = Field(description="3-5 relevant hashtags without the # symbol")


class CandidateSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clips: list[Candidate]


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clip: int = Field(description="The clip number being reviewed")
    keep: bool = Field(description="False if the clip cannot stand alone even after adjusting its boundaries")
    start_sentence: int = Field(description="Corrected first sentence index")
    end_sentence: int = Field(description="Corrected last sentence index, inclusive")
    note: str = Field(description="What was wrong with the boundaries, or 'ok'")


class ReviewSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviews: list[Review]


# --- API ------------------------------------------------------------------------

# Options that describe one particular source and must not be saved into templates.
SOURCE_SPECIFIC_OPTIONS = {"range_start", "range_end"}


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    options: ProjectOptions
    is_default: bool = False


class SourceInfoRequest(BaseModel):
    url: str


class ProjectUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ClipUpdate(BaseModel):
    title: str | None = None
    hashtags: list[str] | None = None


class ClipRender(BaseModel):
    """Editor request: new boundaries, look, caption text and title, then re-render."""

    start: float = Field(ge=0)
    end: float = Field(gt=0)
    look: LookOptions
    title: str = Field(min_length=1, max_length=120)
    words: list[Word] | None = None  # edited caption words; None regenerates from the transcript

    @model_validator(mode="after")
    def _ordered(self) -> "ClipRender":
        if self.end - self.start < 1:
            raise ValueError("clip must be at least 1 second long")
        if self.end - self.start > 4 * 3600:
            raise ValueError("clip must be at most 4 hours long")
        return self


# --- Voice Studio -----------------------------------------------------------------


class VoiceSettings(BaseModel):
    """ElevenLabs' voice controls; the local model uses only speed."""

    model_config = ConfigDict(extra="forbid")

    speed: float = Field(default=1.0, ge=0.7, le=1.2)
    stability: float = Field(default=0.5, ge=0.0, le=1.0)
    similarity: float = Field(default=0.75, ge=0.0, le=1.0)
    style: float = Field(default=0.0, ge=0.0, le=1.0)
    speaker_boost: bool = True


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["elevenlabs", "local"]
    voice_id: str = Field(min_length=1, max_length=100)
    voice_name: str = Field(min_length=1, max_length=200)
    model_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=50000)
    settings: VoiceSettings = VoiceSettings()


class AddLibraryVoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_owner_id: str = Field(min_length=1, max_length=200)
    voice_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
