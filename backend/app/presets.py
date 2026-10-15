"""Selectable presets. The backend owns these; the frontend reads them from /api/presets."""

from dataclasses import asdict, dataclass
from pathlib import Path

APP_FONT_DIR = str(Path(__file__).resolve().parent / "fonts")  # bundled OFL fonts, see fonts/licenses
FONT_DIR = "/System/Library/Fonts/Supplemental"
SYSTEM_FONT_DIR = "/System/Library/Fonts"

# Special colour value: use the highlight colour the user picked.
HIGHLIGHT = "highlight"


@dataclass(frozen=True)
class CaptionStyle:
    id: str
    label: str
    font: str                    # font file in APP_FONT_DIR, FONT_DIR or SYSTEM_FONT_DIR; "File.ttc:N" picks face N
    size: float                  # font size as a fraction of frame width
    uppercase: bool
    fill: str                    # text colour (#rrggbb or #rrggbbaa)
    stroke: str | None           # outline colour
    stroke_ratio: float          # outline width as a fraction of font size
    active: str                  # marker on emphasised words: "color" | "box" | "underline" | "none"
    bar: str | None = None       # RGBA background bar behind the whole line, e.g. "#000000b0"
    shadow: str = "none"         # "soft" (blurred-looking offset) | "hard" (solid offset copy)
    # Per word (the word being spoken enters): pop | bounce | fade | shake | blur | drop | grow.
    # Per line (each caption enters): rise | glitch | line-pop | line-scale.
    animation: str = "none"
    upcoming: str | None = None  # colour of words not spoken yet (default: fill)
    spoken: str | None = None    # colour of words already spoken (default: fill); may be HIGHLIGHT
    active_scale: float = 1.0    # the spoken word stays this much larger
    reveal: bool = False         # words appear only once they are spoken
    glow: bool = False           # neon halo in the highlight colour
    tilt: float = 0.0            # degrees the caption is rotated (counter-clockwise)
    # Added for the OpusClip-style presets; the defaults keep the older styles unchanged.
    italic: bool = False         # slanted, for fonts without an italic face
    shadow_spec: tuple[str, float, float, float] | None = None  # colour, dx, dy, blur (fractions of font size)
    max_width: float = 0.88      # caption width as a fraction of the frame width
    max_lines: int = 1           # words wrap onto up to this many lines before the font shrinks
    emphasis: str = "active"     # which words get the marker: "active" (being spoken) | "keywords" (key words)
    default_highlight: str = "#facc15"  # highlight colour the style is designed for
    highlight2: str | None = None  # alternate captions use this colour while the default highlight is in use
    box_color: str | None = None   # with active="box": box colour; the word is then drawn in the highlight
    loop: str = "none"           # continuous motion while a caption shows: zoom | quake | hover | breathe
    group: str = "more"          # picker section: "opus" | "classic" | "more"


# OpusClip numbers its font sizes, outlines and shadows in pixels of its own; a font of size n
# renders at about OPUS_PX * n pixels on a 1080-pixel-wide frame (measured from its previews).
OPUS_PX = 1.75


def _opus(id: str, label: str, font: str, n: float, *, fill: str = "#ffffff",
          stroke: tuple[str, float] | None = None, shadow: tuple[str, float, float, float] | None = None,
          highlight: str = "#facc15", upper: bool = True, active: str = "none", **kw) -> CaptionStyle:
    """A preset from OpusClip's caption picker, in OpusClip's own units (see OPUS_PX)."""
    px = n * OPUS_PX
    stroke_color, stroke_width = stroke or (None, 0)
    shadow_spec = None
    if shadow:
        color, dx, dy, blur = shadow
        shadow_spec = (color, dx / px, dy / px, blur / px)
    return CaptionStyle(id, label, font, px / 1080, upper, fill, stroke_color, stroke_width / 2 / px, active,
                        shadow_spec=shadow_spec, default_highlight=highlight, group="opus", **kw)


GREEN, YELLOW = "#04f827", "#fffd03"
MULTILINE = {"max_lines": 3, "max_width": 0.62}
TWO_LINES = {"max_lines": 2, "max_width": 0.62}

CAPTION_STYLES: dict[str, CaptionStyle] = {s.id: s for s in [
    # OpusClip's caption presets, rebuilt from their settings and preview animations. Fonts that are
    # not freely licensed are replaced: Komika Axis -> Bangers, Bernina Sans -> Oswald, Thabit -> Courier Prime.
    _opus("karaoke", "Karaoke", "Montserrat-900.ttf", 40, stroke=("#000000", 8), shadow=("#000000", 2, 2, 2),
          active="color", active_scale=1.12, highlight=GREEN, highlight2=YELLOW, **MULTILINE),
    _opus("deep-diver", "Deep Diver", "Poppins-700.ttf", 50, fill="#c6c6c6", upper=False, active="color",
          spoken=HIGHLIGHT, highlight="#000000", bar="#e4e4e4"),
    _opus("youshaei", "Youshaei", "Roboto-900.ttf", 60, fill="#817b81", shadow=("#000000", 2, 2, 1),
          active="color", spoken=HIGHLIGHT, highlight="#6bf5c7"),
    _opus("pod-p", "Pod P", "Oswald-500.ttf", 85, fill="#f43fe0", stroke=("#000000", 8),
          shadow=("#000000", 1, 1, 4), animation="line-pop", highlight="#08feb6"),
    _opus("mozi", "Mozi", "Montserrat-900.ttf", 50, stroke=("#000000", 10), shadow=("#000000", 2, 2, 4),
          active="color", emphasis="keywords", highlight=GREEN, highlight2=YELLOW, animation="line-scale", **MULTILINE),
    _opus("beasty", "Beasty", "Bangers-400.ttf", 50, stroke=("#000000", 16), shadow=("#ffffff", 2, 2, 16),
          active="color", emphasis="keywords", highlight=GREEN, highlight2=YELLOW, animation="line-pop"),
    _opus("simple", "Simple", "BebasNeue-400.ttf", 80, stroke=("#000000", 10), animation="line-pop"),
    _opus("popline", "Popline", "SplineSans-700.ttf", 40, stroke=("#000000", 8), shadow=("#000000", 2, 2, 2),
          active="underline", active_scale=1.15, highlight="#8f2eed"),
    _opus("think-media", "Think Media", "Oswald-600.ttf", 55, italic=True, stroke=("#000000", 6),
          shadow=("#000000", 4, 2, 1), active="color", emphasis="keywords", highlight=GREEN, highlight2=YELLOW,
          animation="line-pop", **MULTILINE),
    _opus("glitch-infinite", "Glitch Infinite", "Poppins-600.ttf", 48, fill="#ffb800", upper=False,
          shadow=("#f01313", 6, 6, 0), animation="glitch", loop="zoom"),
    _opus("seamless-bounce", "Seamless Bounce", "Poppins-600.ttf", 40, upper=False, stroke=("#357847", 20),
          active="color", highlight="#d7ff37", animation="bounce"),
    _opus("baby-earthquake", "Baby Earthquake", "Optima.ttc:1", 48, upper=False, shadow=("#0000001a", 4, 4, 8),
          active="color", highlight="#ffd363", loop="quake"),
    _opus("blur-switch", "Blur Switch", "Roboto-600.ttf", 48, active="color", highlight="#5dfbff", animation="blur"),
    _opus("highlighter-box", "Highlighter Box", "Anton-400.ttf", 48, upper=False, stroke=("#f48bc3", 15),
          active="box", box_color="#f48bc3", highlight="#1c4ebf", **TWO_LINES),
    _opus("focus", "Focus", "CourierPrime-700.ttf", 43, stroke=("#000000", 10), shadow=("#000000", 1, 1, 0),
          active="color", active_scale=1.2, highlight="#f4ff5b", **TWO_LINES),
    _opus("blur-in", "Blur In", "PoltawskiNowy-400.ttf", 46, upper=False, stroke=("#000000", 10),
          shadow=("#000000", 1, 1, 0), active="color", emphasis="keywords", highlight="#fb7bff",
          animation="blur", reveal=True, **TWO_LINES),
    _opus("with-backdrop", "With Backdrop", "Lemon-400.ttf", 40, fill="#f4ff5b", upper=False,
          shadow=("#000000", 1, 1, 0), active="color", emphasis="keywords", highlight="#ffffff",
          animation="pop", reveal=True, bar="#0000004d", **TWO_LINES),
    _opus("soft-landing", "Soft Landing", "Archivo-700.ttf", 44, upper=False, shadow=("#000000", 1, 1, 0),
          active="color", emphasis="keywords", highlight="#61ffff", animation="drop", reveal=True),
    _opus("baby-steps", "Baby Steps", "Heebo-700.ttf", 42, stroke=("#ff00a1", 10), shadow=("#000000", 0, 4, 4),
          highlight="#61ffff", loop="hover", **TWO_LINES),
    _opus("grow", "Grow", "Kanit-700.ttf", 46, stroke=("#8c00ff", 10), shadow=("#8c00ff", 3, 3, 10),
          active="color", emphasis="keywords", highlight="#fc99ff", animation="grow", reveal=True, **TWO_LINES),
    _opus("breathe", "Breathe", "LilitaOne-400.ttf", 44, stroke=("#000000", 10), shadow=("#8c00ff", 3, 3, 4),
          active="color", emphasis="keywords", active_scale=1.25, highlight="#05f827", loop="breathe", tilt=4.0,
          **TWO_LINES),
    # Classic styles.
    CaptionStyle("highlight-box", "Highlight box", "Arial Rounded Bold.ttf", 0.068, True, "#ffffff", "#000000", 0.08, "box",
                 group="classic"),
    CaptionStyle("impact", "Impact", "Impact.ttf", 0.085, True, "#ffffff", "#000000", 0.14, "color", group="classic"),
    CaptionStyle("condensed", "Condensed", "DIN Condensed Bold.ttf", 0.09, True, "#ffffff", "#000000", 0.10, "color",
                 group="classic"),
    CaptionStyle("minimal", "Minimal", "Arial Bold.ttf", 0.052, False, "#ffffff", None, 0.0, "none", shadow="soft",
                 group="classic"),
    CaptionStyle("subtitle-bar", "Subtitle bar", "Trebuchet MS Bold.ttf", 0.048, False, "#ffffff", None, 0.0, "none",
                 bar="#000000b4", group="classic"),
    CaptionStyle("pop", "Pop", "Arial Black.ttf", 0.072, True, "#ffffff", "#000000", 0.12, "color", animation="pop",
                 group="classic"),
    CaptionStyle("pop-box", "Pop box", "Arial Rounded Bold.ttf", 0.068, True, "#ffffff", "#000000", 0.08, "box",
                 animation="pop", group="classic"),
    CaptionStyle("rise", "Rise", "Arial Bold.ttf", 0.06, False, "#ffffff", "#000000", 0.08, "color", shadow="soft",
                 animation="rise", group="classic"),
    CaptionStyle("headline", "Headline", "DIN Condensed Bold.ttf", 0.1, True, "#ffffff", None, 0.0, "color",
                 bar="#000000c8", animation="pop", group="classic"),
    # More animated styles.
    CaptionStyle("reveal", "Reveal", "HelveticaNeue.ttc:1", 0.064, False, "#ffffff", "#000000", 0.07, "color",
                 shadow="soft", animation="fade", reveal=True),
    CaptionStyle("glitch", "Glitch", "Arial Black.ttf", 0.074, True, "#ffffff", "#000000", 0.1, "color",
                 animation="glitch"),
    CaptionStyle("bounce", "Bounce", "Arial Rounded Bold.ttf", 0.074, True, "#ffffff", "#000000", 0.12, "color",
                 animation="bounce"),
    CaptionStyle("earthquake", "Earthquake", "Impact.ttf", 0.088, True, "#ffffff", "#000000", 0.13, "color",
                 animation="shake"),
    CaptionStyle("neon", "Neon", "Avenir Next.ttc:8", 0.07, True, "#ffffff", None, 0.0, "none",
                 glow=True, animation="pop", active_scale=1.15),
    CaptionStyle("tilt", "Tilt", "Futura.ttc:4", 0.09, True, "#ffffff", "#000000", 0.12, "box",
                 shadow="hard", tilt=4.0, animation="pop"),
    CaptionStyle("comic", "Comic", "MarkerFelt.ttc:1", 0.082, True, "#ffffff", "#000000", 0.14, "color",
                 shadow="hard", animation="pop", active_scale=1.08),
    CaptionStyle("typewriter", "Typewriter", "Courier New Bold.ttf", 0.06, False, "#ffffff", None, 0.0, "none",
                 bar="#000000c0", reveal=True),
]}
NO_CAPTIONS = "none"


@dataclass(frozen=True)
class Genre:
    id: str
    label: str
    guidance: str


GENRES: dict[str, Genre] = {g.id: g for g in [
    Genre("auto", "Auto-detect", "Infer the content type from the transcript and judge clips by what works for it."),
    Genre("podcast", "Podcast / interview",
          "Favour complete question-and-answer exchanges, strong opinions, personal stories and surprising facts. "
          "Include the question when the answer needs it."),
    Genre("education", "Tutorial / education",
          "Favour self-contained explanations, tips and 'aha' moments. A clip must include the full explanation, "
          "not just the setup."),
    Genre("commentary", "Commentary / opinion", "Favour bold takes with their reasoning and a clear conclusion."),
    Genre("motivational", "Motivational / speech",
          "Favour quotable passages and emotional peaks that end on the key line."),
    Genre("comedy", "Comedy / entertainment",
          "Favour bits with setup and punchline. Always end right after the punchline lands (include the laugh)."),
    Genre("vlog", "Vlog / story", "Favour mini-stories with a beginning, a turn and an ending."),
    Genre("news", "News / business", "Favour newsworthy statements, numbers and announcements with their context."),
    Genre("sports-gaming", "Sports / gaming", "Favour reactions, highlights and play-by-play peaks with their build-up."),
]}


@dataclass(frozen=True)
class ClipLength:
    id: str
    label: str
    min_seconds: int
    max_seconds: int


CLIP_LENGTHS = [
    ClipLength("auto", "Auto (15–90s)", 15, 90),
    ClipLength("short", "Under 30s", 10, 30),
    ClipLength("medium", "30–60s", 30, 60),
    ClipLength("long", "60–90s", 60, 90),
    ClipLength("extended", "90s–3m", 90, 180),
]


LANGUAGES = [
    ("", "Auto-detect"), ("en", "English"), ("es", "Spanish"), ("fr", "French"), ("de", "German"),
    ("it", "Italian"), ("pt", "Portuguese"), ("nl", "Dutch"), ("ru", "Russian"), ("tr", "Turkish"),
    ("ar", "Arabic"), ("hi", "Hindi"), ("ur", "Urdu"), ("bn", "Bengali"), ("id", "Indonesian"),
    ("ja", "Japanese"), ("ko", "Korean"), ("zh", "Chinese"), ("vi", "Vietnamese"), ("pl", "Polish"),
]


def as_json() -> dict:
    return {
        "caption_styles": [{"id": s.id, "label": s.label, "animated": s.animation != "none" or s.loop != "none",
                            "group": s.group, "highlight": s.default_highlight}
                           for s in CAPTION_STYLES.values()],
        "languages": [{"id": code, "label": label} for code, label in LANGUAGES],
        "genres": [{"id": g.id, "label": g.label} for g in GENRES.values()],
        "clip_lengths": [asdict(c) for c in CLIP_LENGTHS],
    }
