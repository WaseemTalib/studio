"""Shared director data for setup, structured AI answers, and saved plans."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..schemas import AIService
from .styles import STYLES

MAX_SECONDS = 300
MIN_CLIP_WORDS = 15
MAX_CLIP_WORDS = 30
STAGES_5 = ["Golden Hook", "Disrupting Assumptions", "Unveiling Insider Secrets", "Ultimate Truth Revelation",
            "Elevation & Discussion"]
STAGES_3 = ["Setup", "Escalation", "Ironic Payoff"]  # Style 3 story rhythm


class DirectorSettings(BaseModel):
    """Configuration collected before a director plan starts."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["stickman", "generic"] = "stickman"
    source_kind: Literal["topic", "script", "article"] = "topic"
    source: str = Field(default="", max_length=40000)
    url: str | None = Field(default=None, max_length=2000)   # an article to read instead of / with the text
    aspect: Literal["16:9", "9:16"]
    seconds: int = Field(default=60, ge=10, le=MAX_SECONDS)
    style: str
    faceless: bool = False            # Style 1: no line expressions at all
    language: str = Field(default="English", min_length=2, max_length=40)  # planning + reference translation
    voice: Literal["suggest", "male", "female", "custom"] = "suggest"
    voice_custom: str = Field(default="", max_length=300)
    tone: str = Field(default="", max_length=200)
    ai_service: AIService | None = None

    @field_validator("seconds")
    @classmethod
    def _tens(cls, v: int) -> int:  # the skill rounds to the nearest ten-second multiple
        return max(10, min(MAX_SECONDS, int(round(v / 10)) * 10))

    @model_validator(mode="after")
    def _has_source(self) -> "DirectorSettings":
        if not self.source.strip() and not self.url:
            raise ValueError("Give a topic, script or article (or an article link)")
        style = STYLES.get(self.style)
        if style is None or style.generic != (self.mode == "generic"):
            raise ValueError(f"unknown {self.mode} style {self.style!r}")
        return self

    @property
    def clips(self) -> int:
        return self.seconds // 10


# --- AI answers: Phase A -----------------------------------------------------------------------
# extra="forbid" emits additionalProperties: false, which strict JSON-schema mode requires.


class PaletteItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    color: str = Field(description="An ordinary color name, never hex/RGB, e.g. 'vivid red'")
    meaning: str = Field(description="What it represents in the story")


class CastCard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(description="Short unique role ID reused exactly in storyboard roles, e.g. 'Founder' or 'Hero'")
    anchor: str = Field(description="Complete immutable English identity description repeated verbatim in every prompt: "
                                    "visual medium, approximate age, build/proportions, face or head geometry, hair, "
                                    "wardrobe silhouette and colors, footwear, one recognizable accessory, and owned props "
                                    "as applicable. "
                                    "Exclude temporary pose, expression, camera, lighting and location details")
    summary: str = Field(description="One-line summary in the planning language")


class ClipOutline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clip: int = Field(description="Clip number starting at 1")
    stage: str = Field(description="Narrative stage of this clip")
    purpose: str = Field(description="This clip's narrative job, in the planning language")
    vo: str = Field(description="Exact English voiceover for this clip, about 18-25 words, natural sentence ends")
    location: str = Field(description="The recognizable setting of this clip (empty if the style uses one canvas)")


class Overlay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clip: int
    text: str = Field(description="Two to five English words for post-production, never in the generated video")
    placement: str


class ProposalHeader(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title_en: str
    title_ref: str = Field(description="The title in the planning language (empty when it is English)")
    core_message: str = Field(description="Core message, in the planning language")
    hook: str = Field(description="The opening hook, in the planning language")
    voice_summary: str = Field(description="The narrator's voice profile summarized in the planning language")
    voice_block: str = Field(description="Complete self-contained English voice block: identity/age, accent, timbre, "
                                         "pitch/register, resonance, texture, delivery, pace, recording treatment, "
                                         "that the voice never changes across clips, and uninterrupted delivery from "
                                         "the first through final frame")
    palette: list[PaletteItem]
    environment_mood: str = Field(description="Palette/environment mood in the planning language")
    music: str = Field(description="Frozen English description of the score, e.g. 'quiet, thoughtful piano and "
                                   "subtle soft ambient synth'; repeated verbatim in every clip")
    bgm_direction: str = Field(description="BGM direction and emotional turn, in the planning language")
    emotional_arc: str = Field(description="Emotional arc, in the planning language, e.g. 'tired → fear → relief'")
    cast: list[CastCard] = Field(description="Characters with full anchors; empty when the style's default figure is enough")
    clips: list[ClipOutline]
    overlays: list[Overlay] = Field(description="Optional post-production text overlays; may be empty")


class StoryRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clip: int
    scene: str = Field(description="The scene, in the planning language")
    beats: list[str] = Field(min_length=3, max_length=3,
                             description="Exactly three beats for 0-3s, 3-7s and 7-10s, in the planning language")
    devices: list[str] = Field(description="Visual devices used (at least four): action, transformation, metaphor, "
                                           "icon, particles/light, camera move, foreground wipe, match cut...")
    camera: str = Field(description="Motion, camera and transition, in the planning language")
    vo_translation: str = Field(description="The clip's English VO translated into the planning language (empty if English)")
    bgm_sfx: str = Field(description="BGM and SFX for this clip, in the planning language")
    end_state: str = Field(description="The ending state the next clip inherits (pose, prop, direction, or a named "
                                       "location/time cut), in the planning language")
    roles: list[str] = Field(description="Cast roles visible in this clip (empty for a single default figure)")

    @field_validator("beats", mode="before")
    @classmethod
    def _three_beats(cls, value: list[str]) -> list[str]:
        beats = [str(beat).strip() for beat in value if str(beat).strip()]
        if len(beats) < 3:
            raise ValueError("every storyboard row needs three non-empty timed beats")
        return beats[:3]

    @field_validator("end_state")
    @classmethod
    def _ending(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("every storyboard row needs a non-empty ending state")
        return value.strip()


class StoryRows(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[StoryRow]


class ClipRevision(BaseModel):
    """One clip of the proposal redone: its outline and storyboard row."""

    model_config = ConfigDict(extra="forbid")
    outline: ClipOutline
    row: StoryRow


# --- AI answers: Phase B -----------------------------------------------------------------------


class Beat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vo: str = Field(description="The words of the clip's VO spoken during this beat, copied exactly")
    action: str = Field(description="Concrete English character action and visual events in this beat")


class ClipPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clip: int
    title: str = Field(description="Short English clip title")
    environment: str = Field(description="English description of this clip's setting/background")
    palette: str = Field(description="English palette and accent roles for this clip, ordinary color names only")
    composition: str = Field(description="English composition for the aspect ratio in this clip")
    first_frame: str = Field(description="English first-frame state inherited from the previous clip")
    beats: list[Beat] = Field(description="Exactly three beats: 0-3s, 3-7s, 7-10s")
    sfx: str = Field(description="English synchronized sound effects on physical actions")
    final_frame: str = Field(description="English final-frame state the next clip inherits")
    stitch: str = Field(description="How this clip joins the next one: continuing action/prop/direction or the "
                                    "planned location/time cut, plus any trim or crossfade note")


class ClipPrompts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clips: list[ClipPrompt]


# --- API ------------------------------------------------------------------------------------------


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feedback: str = Field(default="", max_length=4000)
    clip: int | None = Field(default=None, ge=1)               # revise only this clip
    settings: DirectorSettings | None = None                  # a global change: ratio, duration, style...

    @model_validator(mode="after")
    def _something(self) -> "Revision":
        if not self.feedback.strip() and self.settings is None:
            raise ValueError("Say what to change")
        return self
