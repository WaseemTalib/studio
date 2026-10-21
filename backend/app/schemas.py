"""API and pipeline data contracts."""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

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
# An AI service and model, "service:model" (e.g. "gemini:auto"), or "auto" for the automatic chain
# (Claude → DeepSeek → Groq); None uses the configured default.
AIService = Annotated[str, StringConstraints(
    pattern=r"^(auto|(groq|claude|deepseek|deepseek-api|gemini|openrouter|sambanova):\S+)$", max_length=200)]


class LookOptions(BaseModel):
    """How a clip looks. Set per project, copied to each clip, editable per clip."""

    aspect: Aspect = "9:16"
    layout: Layout = "auto"
    caption_style: str = "karaoke"
    caption_position: Position = "bottom"
    highlight_color: str = CAPTION_STYLES["karaoke"].default_highlight
    words_per_line: int = Field(default=3, ge=1, le=6)
    caption_size: int = Field(default=50, ge=0, le=150)       # percent of the style's font size
    caption_offset: int = Field(default=12, ge=-100, le=100)  # percent of available travel; negative moves up
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
    ai_service: AIService | None = None  # finds the highlights; None uses the default (and clip_model)

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


class LimitChoice(BaseModel):
    """What to do when a project waits on a usage limit."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["wait", "switch"]
    option: str | None = Field(default=None, max_length=200)  # with "switch": the service to use, "service:model"


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


# --- Video maker --------------------------------------------------------------------
# A long video made from a prompt, description, script or news article: a narrated script,
# stock footage matched to each scene, captions and optional background music.

SourceKind = Literal["prompt", "description", "script", "news"]
Footage = Literal["pexels", "pixabay", "both", "none"]  # before several sources could be chosen
FootageSource = Literal["openverse", "web", "youtube", "pixabay", "pexels", "coverr", "commons", "unsplash"]


SEARCH_TERMS_HELP = ("4-6 English stock-footage searches of 2-3 words, in the order the narration mentions "
                     "those things, ending with one broader search")
CARD_HELP = ("A striking number or fact from this scene to show full screen, max 6 words, e.g. "
             "'158 YEARS IN PRISON'; empty for most scenes")
QUOTE_HELP = "An exact quote from the source by a named person, max 25 words; empty unless the source quotes someone"
QUOTE_BY_HELP = "Who said the quote, e.g. 'Debra Robinson, mother'; empty when there is no quote"
MUSIC_MOOD_HELP = ("2-4 English words to search for instrumental background music that fits the video, "
                   "e.g. 'calm ambient piano', 'dark suspense', 'upbeat corporate'")
PLACE_HELP = ("A real, named place this scene is about, for a photo, e.g. 'Ensenada, Baja California'; "
              "empty if none")


class ScriptScene(BaseModel):
    """One scene for the script writer: narration, stock-footage searches and optional cards."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="The narration for this scene: 2-4 sentences, spoken word only")
    search_terms: list[str] = Field(description=SEARCH_TERMS_HELP)
    card_text: str = Field(description=CARD_HELP)
    quote: str = Field(description=QUOTE_HELP)
    quote_by: str = Field(description=QUOTE_BY_HELP)
    place: str = Field(description=PLACE_HELP)


class ScriptDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="Video title, max ~70 characters")
    description: str = Field(description="One or two sentence video description for YouTube")
    hashtags: list[str] = Field(description="3-6 relevant hashtags without the # symbol")
    music_mood: str = Field(description=MUSIC_MOOD_HELP)
    scenes: list[ScriptScene]


class DraftRequest(BaseModel):
    """Write (or, for kind="script", split and tag) a script."""

    model_config = ConfigDict(extra="forbid")

    kind: SourceKind
    text: str = Field(default="", max_length=60000)   # the prompt, description, script or article text
    url: str | None = Field(default=None, max_length=2000)  # news: article link to read
    minutes: float = Field(default=3, ge=0.5, le=20)  # target narration length
    language: str = Field(default="English", min_length=2, max_length=40)
    tone: str = Field(default="", max_length=200)
    service: AIService | None = None  # writes the script; None uses the default

    @model_validator(mode="after")
    def _has_input(self) -> "DraftRequest":
        if not self.text.strip() and not (self.kind == "news" and self.url):
            raise ValueError("Write a prompt or paste text first" if self.kind != "news"
                             else "Paste an article link or its text")
        return self


class StockMedia(BaseModel):
    """A stock clip or photo chosen in the footage review."""

    model_config = ConfigDict(extra="ignore")

    provider: Literal["openverse", "web", "searxng", "duckduckgo", "youtube", "pexels", "pixabay", "coverr",
                      "commons", "wikimedia", "unsplash", "upload"]
    id: str = Field(max_length=300)
    link: str = Field(max_length=2000)
    width: int
    height: int
    duration: float
    page: str = Field(default="", max_length=2000)
    author: str = Field(default="", max_length=300)
    thumb: str = Field(default="", max_length=2000)
    preview: str = Field(default="", max_length=2000)
    text: str = Field(default="", max_length=2000)
    kind: Literal["video", "photo"] = "video"
    license: str = Field(default="", max_length=100)
    score: float = 0.0
    ping: str = Field(default="", max_length=2000)


class Scene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=5000)
    search_terms: list[str] = Field(default_factory=list, max_length=8)
    card_text: str = Field(default="", max_length=80)      # shown full screen at the start of the scene
    quote: str = Field(default="", max_length=400)         # shown as a quote card
    quote_by: str = Field(default="", max_length=120)
    place: str = Field(default="", max_length=120)         # a real place, for a Wikimedia Commons photo
    clips: list[StockMedia] = Field(default_factory=list, max_length=30)  # chosen in the footage review, in order


class VideoVoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["elevenlabs", "local"] = "local"
    voice_id: str = Field(default="af_heart", min_length=1, max_length=100)
    voice_name: str = Field(default="Heart", min_length=1, max_length=200)
    model_id: str = Field(default="kokoro-v1.0", min_length=1, max_length=100)
    settings: VoiceSettings = VoiceSettings()


class VideoRequest(BaseModel):
    """Everything needed to make the video, after the script has been reviewed."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    hashtags: list[str] = Field(default_factory=list, max_length=20)
    scenes: list[Scene] = Field(min_length=1, max_length=200)
    language: str | None = Field(default=None, max_length=10)  # Whisper code for caption timing
    voice: VideoVoice = VideoVoice()
    aspect: Aspect = "16:9"
    resolution: Literal["720p", "1080p"] = "1080p"
    footage: Footage = "pexels"
    sources: list[FootageSource] | None = None  # where footage comes from; None: from `footage`; []: plain
    clip_seconds: float = Field(default=5, ge=2, le=15)  # longest single stock shot
    transition: Literal["cut", "fade"] = "fade"
    caption_style: str = "simple"
    caption_position: Position = "bottom"
    highlight_color: str = CAPTION_STYLES["simple"].default_highlight
    words_per_line: int = Field(default=4, ge=1, le=8)
    caption_size: int = Field(default=50, ge=0, le=150)
    caption_offset: int = Field(default=12, ge=-100, le=100)
    title_overlay: bool = False
    music: str | None = Field(default=None, max_length=200)  # file name in the music library
    # track: `music` (or none); random: any track in the library; auto: a free track matching music_mood
    music_mode: Literal["track", "random", "auto"] = "track"
    music_mood: str = Field(default="", max_length=100)
    music_volume: float = Field(default=0.12, ge=0, le=1)
    cards: bool = True    # fact and quote cards
    photos: bool = True   # photos of named places from Wikimedia Commons
    ai_service: AIService | None = None  # checks the footage against the narration; None uses the default

    _style = field_validator("caption_style")(LookOptions._style.__func__)
    _color = field_validator("highlight_color")(LookOptions._color.__func__)


class FootagePlanRequest(BaseModel):
    """Find footage for every scene before rendering, for review."""

    model_config = ConfigDict(extra="forbid")

    scenes: list[Scene] = Field(min_length=1, max_length=200)
    footage: Footage = "pixabay"
    sources: list[FootageSource] | None = None
    aspect: Aspect = "16:9"
    resolution: Literal["720p", "1080p"] = "1080p"
    clip_seconds: float = Field(default=5, ge=2, le=15)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    cards: bool = True
    photos: bool = True
    ai_service: AIService | None = None
    only: int | None = Field(default=None, ge=0)  # just this scene (refresh one)


class MusicTrack(BaseModel):
    """A track found by a music search, to add to the library."""

    model_config = ConfigDict(extra="ignore")

    provider: Literal["jamendo", "commons"]
    id: str = Field(max_length=300)
    title: str = Field(default="", max_length=300)
    author: str = Field(default="", max_length=300)
    download: str = Field(max_length=2000)
    page: str = Field(default="", max_length=2000)
    license: str = Field(default="", max_length=200)
    license_url: str = Field(default="", max_length=500)
    ext: str = Field(default=".mp3", max_length=8)


def footage_sources(req: "VideoRequest | FootagePlanRequest") -> list[str]:
    """The footage sources a request asks for (older requests name one in `footage`)."""
    if req.sources is not None:
        return list(dict.fromkeys(req.sources))
    return {"none": [], "both": ["pexels", "pixabay"]}.get(req.footage, [req.footage])
