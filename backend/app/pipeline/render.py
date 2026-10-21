"""Cut, reframe and caption clips.

Captions and the title overlay are drawn with Pillow into transparent PNG frames and
laid over the video through ffmpeg's concat demuxer, so no libass/freetype build of
ffmpeg is needed. Each frame is shown for exactly the interval it applies to.
"""

import bisect
import math
import random
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..config import FFMPEG, SHORT_SIDE
from ..presets import APP_FONT_DIR, CAPTION_STYLES, FONT_DIR, HIGHLIGHT, NO_CAPTIONS, SYSTEM_FONT_DIR, CaptionStyle
from ..schemas import Aspect, LookOptions, Word
from .cancel import check
from .faces import Face, Shot, Subjects, find_shots
from .media import run
from .sentences import split_words

FALLBACK_FONTS = [f"{FONT_DIR}/Arial Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
MAX_CAPTION_GAP = 0.6
TITLE_SECONDS = 3.0
POSITION_Y = {"top": 0.20, "middle": 0.50, "bottom": 0.74}

# Animations are drawn as PHASES short frames at the start of a word or line.
ANIM_STEP = 0.045
PHASES = 3
POP_SCALES = (1.24, 1.12, 1.04)
GROW_SCALES = (0.45, 0.72, 0.9)
RISE_OFFSETS = (0.55, 0.25, 0.08)     # fraction of the font size below the resting position
DROP_OFFSETS = (-0.6, -0.28, -0.08)   # fraction of the font size above the resting position
BOUNCE_OFFSETS = (-0.32, 0.12, -0.04)  # fraction of the font size; negative is up
FADE_ALPHAS = (0.3, 0.6, 0.85)
BLUR_RADII = (0.14, 0.07, 0.025)      # fraction of the font size
SHAKES = ((0.05, -0.03, -1.6), (-0.04, 0.03, 1.3), (0.02, -0.01, -0.5))  # dx, dy (font sizes), degrees
GLITCH_SHIFTS = (0.09, -0.06, 0.03)   # colour-split offset, fraction of the font size
PER_WORD_ANIMATIONS = {"pop", "bounce", "fade", "shake", "blur", "drop", "grow"}
PER_LINE_ANIMATIONS = {"rise", "glitch", "line-pop", "line-scale"}
SLANT = 0.2                           # horizontal shear of styles drawn slanted

# Continuous motion (CaptionStyle.loop) is drawn as one frame per LOOP_STEP while a caption shows.
LOOP_STEP = 1 / 12

# Small words never chosen as a caption's key word.
STOPWORDS = frozenset("""
a an and are as at be been but by can could did do does for from had has have he her his i if in into is it
its it's just like me my no not of on or our she so than that that's the their them then there they this to
too up us was we were what when which who will with would you your i'm you're we're they're don't can't
""".split())

MAX_SCALED_LINES = 3     # lines a caption may wrap onto when its size is turned up
MAX_CAPTION_HEIGHT = 0.6   # share of the frame height a caption may cover

ZOOM_LEVEL = 1.14
MAX_ZOOM_INTERVALS = 60


def output_size(aspect: Aspect, resolution: str = "1080p") -> tuple[int, int]:
    s = SHORT_SIDE[resolution]
    return {"9:16": (s, s * 16 // 9), "1:1": (s, s), "16:9": (s * 16 // 9 // 2 * 2, s)}[aspect]


@lru_cache(maxsize=256)
def _font(spec: str, size: int) -> ImageFont.FreeTypeFont:
    name, _, index = spec.partition(":")
    for path in [f"{APP_FONT_DIR}/{name}", f"{FONT_DIR}/{name}", f"{SYSTEM_FONT_DIR}/{name}"]:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size, index=int(index or 0))
            except OSError:
                break
    for path in FALLBACK_FONTS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def _rgba(hex_color: str) -> tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + ((int(h[6:8], 16),) if len(h) == 8 else (255,))


def _ink_for(bg: str) -> tuple[int, int, int, int]:
    r, g, b, _ = _rgba(bg)
    return (17, 17, 17, 255) if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else (255, 255, 255, 255)


def _faded(color: tuple[int, int, int, int], alpha: float) -> tuple[int, int, int, int]:
    return color[:3] + (int(color[3] * alpha),)


def keywords(words: list[str]) -> set[int]:
    """The key word of a caption: its longest word that is not a small word. Stands in for the
    AI keyword pick of the "keywords" styles."""
    best, best_len = None, 0
    for i, w in enumerate(words):
        bare = re.sub(r"[^\w']", "", w.lower())
        size = len(bare) + (3 if bare.isdigit() else 0)
        if bare and bare not in STOPWORDS and size >= 4 and size >= best_len:
            best, best_len = i, size
    return set() if best is None else {best}


# --- Drawing --------------------------------------------------------------------


@dataclass(frozen=True)
class Look:
    style: CaptionStyle | None
    position: str
    highlight: str
    title: str | None
    scale: float = 1.0    # caption font size relative to the style's own
    offset: float = 0.0   # signed share of the available vertical travel; -1 is top and 1 is bottom

    def highlight_for(self, alt: bool) -> str:
        """Alternate captions switch to the style's second colour, unless the user picked their own."""
        s = self.style
        if alt and s and s.highlight2 and self.highlight.lower() == s.default_highlight.lower():
            return s.highlight2
        return self.highlight

    def color(self, value: str) -> tuple[int, int, int, int]:
        return _rgba(self.highlight if value == HIGHLIGHT else value)


def draw_frame(size: tuple[int, int], look: Look, words: tuple[str, ...] | None, active: int | None,
               show_title: bool, phase: int | None = None, title_phase: int | None = None,
               tick: int | None = None, alt: bool = False) -> Image.Image:
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    if show_title and look.title:
        img = Image.alpha_composite(img, _title_layer(size, look, title_phase))
    if words and look.style:
        img = Image.alpha_composite(img, _caption_layer(size, list(words), active, look, phase, tick, alt))
    return img


def _paste(layer: Image.Image, img: Image.Image, x: int, y: int) -> None:
    """alpha_composite that accepts positions partly outside the layer."""
    sx, sy = max(0, -x), max(0, -y)
    if sx >= img.width or sy >= img.height or x >= layer.width or y >= layer.height:
        return
    layer.alpha_composite(img, (max(0, x), max(0, y)), (sx, sy))


def _text_image(xy: tuple[float, float], text: str, font: ImageFont.FreeTypeFont, fill: tuple[int, int, int, int],
                stroke: int = 0, stroke_fill: tuple[int, int, int, int] | None = None,
                margin: int = 0) -> tuple[Image.Image, int, int]:
    """Text on its own small transparent image; returns it with its top-left position on the frame."""
    left, top, right, bottom = font.getbbox(text, stroke_width=stroke)
    x0, y0 = int(xy[0] + left) - margin - 1, int(xy[1] + top) - margin - 1
    img = Image.new("RGBA", (int(right - left) + 2 * margin + 3, int(bottom - top) + 2 * margin + 3), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((xy[0] - x0, xy[1] - y0), text, font=font, fill=fill, stroke_width=stroke,
                             stroke_fill=stroke_fill)
    return img, x0, y0


def _with_alpha(img: Image.Image, alpha: float) -> Image.Image:
    if alpha < 1:
        img.putalpha(img.getchannel("A").point(lambda v: int(v * alpha)))
    return img


def _draw_text(layer: Image.Image, xy: tuple[float, float], text: str, font: ImageFont.FreeTypeFont,
               fill: tuple[int, int, int, int], style: CaptionStyle, outline: bool, alpha: float = 1.0,
               blur: float = 0.0) -> None:
    """One run of text with the style's shadow and outline."""
    alpha *= fill[3] / 255
    stroke = int(font.size * style.stroke_ratio) if style.stroke and outline else 0
    if alpha < 1 or blur:
        # ImageDraw blends translucent ink with the transparent black canvas, which darkens it.
        # Draw opaque on a scratch image and fade (and blur) that instead.
        margin = stroke + int(font.size * 0.3) + int(blur * 3)
        scratch, x0, y0 = _text_image(xy, text, font, (0, 0, 0, 0), stroke, margin=margin)
        _draw_text(scratch, (xy[0] - x0, xy[1] - y0), text, font, fill[:3] + (255,), style, outline)
        if blur:
            scratch = scratch.filter(ImageFilter.GaussianBlur(blur))
        _paste(layer, _with_alpha(scratch, alpha), x0, y0)
        return
    draw = ImageDraw.Draw(layer)
    x, y = xy
    if style.shadow_spec:
        color, dx, dy, spread = style.shadow_spec
        rgba = _rgba(color)
        radius = spread * font.size / 2
        shadow, x0, y0 = _text_image((x + dx * font.size, y + dy * font.size), text, font, rgba[:3] + (255,),
                                     stroke, rgba[:3] + (255,), margin=int(radius * 3) + 2)
        if radius >= 0.5:
            shadow = shadow.filter(ImageFilter.GaussianBlur(radius))
        _paste(layer, _with_alpha(shadow, rgba[3] / 255), x0, y0)
    elif style.shadow == "soft":
        off = max(2, font.size // 18)
        draw.text((x + off, y + off), text, font=font, fill=(0, 0, 0, 160))
    elif style.shadow == "hard":
        off = max(3, font.size // 11)
        draw.text((x + off, y + off), text, font=font, fill=(0, 0, 0, 255), stroke_width=stroke,
                  stroke_fill=(0, 0, 0, 255))
    draw.text((x, y), text, font=font, fill=fill, stroke_width=stroke,
              stroke_fill=_rgba(style.stroke) if stroke else None)


def _wrap_indices(widths: list[float], space: float, max_width: float) -> list[list[int]]:
    lines: list[list[int]] = [[]]
    used = 0.0
    for i, w in enumerate(widths):
        extra = w if not lines[-1] else space + w
        if lines[-1] and used + extra > max_width:
            lines.append([i])
            used = w
        else:
            lines[-1].append(i)
            used += extra
    return lines


def _caption_layer(size: tuple[int, int], words: list[str], active: int | None, look: Look,
                   phase: int | None, tick: int | None = None, alt: bool = False) -> Image.Image:
    style = look.style
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    if look.scale <= 0:
        return layer
    draw = ImageDraw.Draw(layer)
    if style.uppercase:
        words = [w.upper() for w in words]
    highlight = look.highlight_for(alt)
    if style.emphasis == "keywords":
        marked = keywords(words)
        enlarged = marked
    else:
        marked = {active} if active is not None and style.active != "none" else set()
        enlarged = {active} if active is not None else set()

    def scale_of(i: int) -> float:
        return style.active_scale if i in enlarged else 1.0

    def fit(size_px: int, max_lines: int):
        """Wrap onto up to max_lines lines of max_width; shrink the font until it fits."""
        while True:
            font = _font(style.font, size_px)
            fonts = [_font(style.font, int(size_px * scale_of(i))) for i in range(len(words))]
            # Condensed fonts have tiny spaces; boxes and shadows need room between words.
            space = max(draw.textlength(" ", font=font), size_px * 0.26)
            if style.active == "box":
                space += size_px * 0.2
            if style.shadow == "hard":
                space += size_px * 0.08
            space += size_px * (style.active_scale - 1)
            widths = [draw.textlength(w, font=f) for w, f in zip(words, fonts)]
            lines = _wrap_indices(widths, space, max_width)
            totals = [sum(widths[i] for i in line) + space * (len(line) - 1) for line in lines]
            ascent, descent = font.getmetrics()
            tall = (ascent + descent) * len(lines) > height * MAX_CAPTION_HEIGHT
            if (len(lines) <= max_lines and max(totals) <= max_width and not tall) or size_px <= 24:
                return font, fonts, space, widths, lines, totals
            size_px -= 4

    max_width = width * style.max_width
    font, fonts, space, widths, lines, totals = fit(int(width * style.size), style.max_lines)
    if look.scale != 1.0:
        # The chosen size scales the size the style would show; larger text may wrap onto extra lines.
        font, fonts, space, widths, lines, totals = fit(max(1, int(font.size * look.scale)),
                                                        max(style.max_lines, MAX_SCALED_LINES))

    ascent, descent = font.getmetrics()
    line_h = int((ascent + descent) * 0.95)
    block_h = line_h * (len(lines) - 1) + ascent + descent
    min_top, max_top = int(height * 0.03), int(height * 0.97) - block_h
    base_top = int(height * POSITION_Y[look.position]) - (line_h * (len(lines) - 1) + ascent) // 2
    if look.offset < 0:
        top = round(base_top + (base_top - min_top) * look.offset)
    else:
        top = round(base_top + (max_top - base_top) * look.offset)
    top = max(min_top, min(top, max_top))
    if phase is not None and style.animation == "rise":
        top += int(font.size * RISE_OFFSETS[phase])
    pad = font.size // 5

    if style.bar:
        block_w = max(totals)
        draw.rounded_rectangle(((width - block_w) / 2 - pad * 2, top - pad,
                                (width + block_w) / 2 + pad * 2, top + line_h * (len(lines) - 1) + ascent + descent + pad),
                               radius=pad, fill=_rgba(style.bar))

    for n, (line, total) in enumerate(zip(lines, totals)):
        x = (width - total) / 2
        y = top + n * line_h
        for i in line:
            word, w, wfont = words[i], widths[i], fonts[i]
            is_active = i == active
            if style.reveal and active is not None and i > active:
                x += w + space
                continue
            animating = is_active and phase is not None and style.animation in PER_WORD_ANIMATIONS
            anim = style.animation if animating else None
            wx, ww = x, w
            scale = POP_SCALES[phase] if anim == "pop" else GROW_SCALES[phase] if anim == "grow" else 1.0
            if scale != 1.0:
                # A popping or growing word is drawn around the centre of its resting slot.
                wfont = _font(style.font, max(4, int(wfont.size * scale)))
                ww = draw.textlength(word, font=wfont)
                wx = x + (w - ww) / 2
            wasc, wdesc = wfont.getmetrics()
            wy = y + (ascent - wasc) / 2
            if anim == "bounce":
                wy += font.size * BOUNCE_OFFSETS[phase]
            elif anim == "drop":
                wy += font.size * DROP_OFFSETS[phase]
            alpha = FADE_ALPHAS[phase] if anim in ("fade", "drop", "grow") else 1.0
            blur = font.size * BLUR_RADII[phase] if anim == "blur" else 0.0

            fill = _rgba(style.fill)
            if active is not None and i < active and style.spoken:
                fill = _rgba(highlight if style.spoken == HIGHLIGHT else style.spoken)
            elif active is not None and i > active and style.upcoming:
                fill = _rgba(highlight if style.upcoming == HIGHLIGHT else style.upcoming)
            outline = True
            if i in marked:
                if style.active == "color":
                    fill = _rgba(highlight)
                elif style.active == "box":
                    box = _rgba(style.box_color) if style.box_color else _rgba(highlight)
                    draw.rounded_rectangle((wx - pad, wy - pad // 2, wx + ww + pad, wy + wasc + wdesc + pad // 2),
                                           radius=pad, fill=_faded(box, alpha))
                    fill = _rgba(highlight) if style.box_color else _ink_for(highlight)
                    outline = False
                elif style.active == "underline":
                    thick = max(4, wfont.size // 9)
                    bottom = wy + wasc + max(2, wdesc // 3)
                    draw.rounded_rectangle((wx, bottom, wx + ww, bottom + thick), radius=thick // 2,
                                           fill=_faded(_rgba(highlight), alpha))
            _draw_text(layer, (wx, wy), word, wfont, fill, style, outline, alpha, blur)
            x += w + space

    center = (width / 2, top + (line_h * (len(lines) - 1) + ascent + descent) / 2)
    if style.italic:
        layer = layer.transform(layer.size, Image.AFFINE, (1, SLANT, -SLANT * center[1], 0, 1, 0), Image.BICUBIC)
    return _effects(layer, style, look, font.size, phase, center, whole=False, tick=tick)


def _title_layer(size: tuple[int, int], look: Look, phase: int | None) -> Image.Image:
    """The clip title, drawn in the caption style and highlight colour."""
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    style = look.style
    if style is None:
        _draw_plain_title(draw, look.title, size, look.position)
        return layer

    text = look.title.upper() if style.uppercase else look.title
    size_px = int(width * min(style.size, 0.075) * 0.82)
    while True:
        font = _font(style.font, size_px)
        lines = _wrap(draw, text, font, width * 0.84)
        if len(lines) <= 2 or size_px <= 20:
            break
        size_px -= 3
    lines = lines[:2]
    ascent, descent = font.getmetrics()
    pad, gap = font.size // 4, font.size // 3
    line_h = ascent + descent + gap
    top = int(height * (0.08 if look.position != "top" else 0.04))
    if look.position == "top":
        anchor = POSITION_Y["top"]
        edge = 0.03 if look.offset < 0 else 0.97
        caption_center = anchor + (edge - anchor) * abs(look.offset)
        top = int(height * caption_center + font.size * 1.4)  # below the captions
        top = max(int(height * 0.03), min(top, int(height * 0.97) - line_h * len(lines)))

    for n, line in enumerate(lines):
        tw = draw.textlength(line, font=font)
        lx, ly = (width - tw) / 2, top + n * line_h
        box = (lx - pad * 2, ly - pad, lx + tw + pad * 2, ly + ascent + descent + pad)
        fill, outline = _rgba(style.fill), True
        if style.bar:
            draw.rounded_rectangle(box, radius=pad * 2, fill=_rgba(style.bar))
        if style.active == "box":
            if style.box_color:
                draw.rounded_rectangle(box, radius=pad * 2, fill=_rgba(style.box_color))
                fill, outline = _rgba(look.highlight), False
            else:
                draw.rounded_rectangle(box, radius=pad * 2, fill=_rgba(look.highlight))
                fill, outline = _ink_for(look.highlight), False
        elif style.active in ("color", "underline"):
            fill = _rgba(look.highlight)
        _draw_text(layer, (lx, ly), line, font, fill, style, outline)

    block_h = line_h * len(lines) - gap
    center = (width / 2, top + block_h / 2)
    if style.italic:
        layer = layer.transform(layer.size, Image.AFFINE, (1, SLANT, -SLANT * center[1], 0, 1, 0), Image.BICUBIC)
    return _effects(layer, style, look, font.size, phase, center, whole=True)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: float) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    lines.append(line)
    return lines


def _draw_plain_title(draw: ImageDraw.ImageDraw, title: str, size: tuple[int, int], caption_position: str) -> None:
    """Title card used when captions are off: dark text on a white box."""
    width, height = size
    font = _font("Arial Black.ttf", max(28, int(width * 0.055)))
    lines = _wrap(draw, title, font, width * 0.84)[:2]
    pad, gap = font.size // 2, font.size // 4
    line_h = font.size + gap
    box_w = max(draw.textlength(l, font=font) for l in lines) + pad * 2
    box_h = line_h * len(lines) - gap + pad * 2
    top = int(height * (0.08 if caption_position != "top" else 0.04))
    left = (width - box_w) / 2
    draw.rounded_rectangle((left, top, left + box_w, top + box_h), radius=pad, fill=(255, 255, 255, 240))
    for i, l in enumerate(lines):
        tw = draw.textlength(l, font=font)
        draw.text(((width - tw) / 2, top + pad + i * line_h), l, font=font, fill=(17, 17, 17, 255))


def _loop_motion(loop: str, tick: int, font_size: int) -> tuple[float, float, float, float]:
    """(dx, dy, degrees, scale) of a continuous motion `tick` steps after the caption appeared."""
    t = tick * LOOP_STEP
    if loop == "zoom":  # slow push-in for as long as the caption shows
        return 0.0, 0.0, 0.0, 1 + min(0.06 * t, 0.2)
    if loop == "quake":
        rnd = random.Random(tick)
        return (rnd.uniform(-1, 1) * 0.025 * font_size, rnd.uniform(-1, 1) * 0.025 * font_size,
                rnd.uniform(-1, 1) * 1.2, 1.0)
    if loop == "hover":
        return 0.0, math.sin(2 * math.pi * t / 1.6) * 0.07 * font_size, 0.0, 1.0
    if loop == "breathe":
        return 0.0, 0.0, 2.5 * math.sin(2 * math.pi * t / 1.8), 1 + 0.05 * math.sin(2 * math.pi * t / 1.2)
    return 0.0, 0.0, 0.0, 1.0


def _effects(layer: Image.Image, style: CaptionStyle, look: Look, font_size: int, phase: int | None,
             center: tuple[float, float], whole: bool, tick: int | None = None) -> Image.Image:
    """Glow, line-level animation, continuous motion and tilt. `whole` also animates the
    per-word entrances (pop, rise, ...) on the full layer, for the title."""
    box = layer.getbbox()
    if box is None:
        return layer
    margin = font_size  # room for glow, shake, zoom and rotation
    box = (max(0, box[0] - margin), max(0, box[1] - margin),
           min(layer.width, box[2] + margin), min(layer.height, box[3] + margin))
    part = layer.crop(box)
    cx, cy = center[0] - box[0], center[1] - box[1]
    dx = dy = 0.0
    angle = style.tilt
    scale = 1.0

    if style.glow:
        halo = part.getchannel("A").filter(ImageFilter.GaussianBlur(max(4, font_size // 5)))
        halo = halo.point(lambda a: min(255, int(a * 1.6)))
        glow = Image.new("RGBA", part.size, _rgba(look.highlight)[:3] + (0,))
        glow.putalpha(halo)
        part = Image.alpha_composite(glow, part)

    if phase is not None:
        anim = style.animation
        if anim == "shake":
            sx, sy, sa = SHAKES[phase]
            dx, dy, angle = dx + sx * font_size, dy + sy * font_size, angle + sa
        elif anim == "glitch":
            shift = int(GLITCH_SHIFTS[phase] * font_size)
            alpha = part.getchannel("A")
            red = Image.new("RGBA", part.size, (255, 40, 90, 0))
            red.putalpha(alpha.point(lambda a: a * 3 // 4))
            cyan = Image.new("RGBA", part.size, (30, 220, 255, 0))
            cyan.putalpha(alpha.point(lambda a: a * 3 // 4))
            split = Image.new("RGBA", part.size, (0, 0, 0, 0))
            split.alpha_composite(red, (max(0, -shift), 0), (max(0, shift), 0))
            split.alpha_composite(cyan, (max(0, shift), 0), (max(0, -shift), 0))
            part = Image.alpha_composite(split, part)
        elif anim == "line-pop" or (whole and anim == "pop"):
            scale *= POP_SCALES[phase]
        elif anim == "line-scale" or (whole and anim == "grow"):
            scale *= GROW_SCALES[phase]
        elif whole and anim == "rise":
            dy += RISE_OFFSETS[phase] * font_size
        elif whole and anim == "drop":
            dy += DROP_OFFSETS[phase] * font_size
        elif whole and anim == "bounce":
            dy += BOUNCE_OFFSETS[phase] * font_size
        elif whole and anim == "blur":
            part = part.filter(ImageFilter.GaussianBlur(BLUR_RADII[phase] * font_size))
        if (whole and anim in ("fade", "drop", "grow")) or anim == "line-scale":
            part = _with_alpha(part, FADE_ALPHAS[phase])

    if tick is not None and style.loop != "none":
        ldx, ldy, langle, lscale = _loop_motion(style.loop, tick, font_size)
        dx, dy, angle, scale = dx + ldx, dy + ldy, angle + langle, scale * lscale

    if scale != 1.0:
        part = part.resize((max(1, int(part.width * scale)), max(1, int(part.height * scale))), Image.BICUBIC)
        cx, cy = cx * scale, cy * scale
    if angle:
        part = part.rotate(angle, resample=Image.BICUBIC, center=(cx, cy), expand=False)
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    # Keep the anchor point (caption centre) where it was, plus any animation offset.
    ox = int(center[0] - cx + dx)
    oy = int(center[1] - cy + dy)
    out.paste(part, (ox, oy))  # plain copy: `out` is empty, and a masked paste would darken edges
    return out


# --- Timeline -------------------------------------------------------------------


def _caption_groups(words: list[Word], per_line: int) -> list[list[Word]]:
    groups: list[list[Word]] = []
    for w in words:
        if groups and len(groups[-1]) < per_line and w.start - groups[-1][-1].end <= MAX_CAPTION_GAP:
            groups[-1].append(w)
        else:
            groups.append([w])
    return groups


def _per_word_frames(style: CaptionStyle) -> bool:
    """Whether captions change while their words are spoken, or are one frame per caption."""
    if style.reveal or style.spoken or style.upcoming:
        return True
    return style.emphasis == "active" and (style.active != "none" or style.active_scale != 1)


def _timeline(words: list[Word], start: float, end: float, look: Look, per_line: int):
    """Yield (t0, t1, frame_key) intervals covering [0, end-start]."""
    length = end - start
    events = []  # (t0, t1, words, active, phase, alt, caption start)
    style = look.style
    animation = style.animation if style else "none"

    def add(t0: float, t1: float, texts: tuple[str, ...], active: int | None, animate: bool, alt: bool,
            g0: float) -> None:
        if animate:
            for phase in range(PHASES):
                a, b = t0 + phase * ANIM_STEP, min(t1, t0 + (phase + 1) * ANIM_STEP)
                if b - a > 0.005:
                    events.append((a, b, texts, active, phase, alt, g0))
            t0 = min(t1, t0 + PHASES * ANIM_STEP)
        if t1 - t0 > 0.005:
            events.append((t0, t1, texts, active, None, alt, g0))

    if style:
        for n, group in enumerate(_caption_groups(words, per_line)):
            texts = tuple(w.text for w in group)
            g0, g_end = group[0].start - start, group[-1].end - start
            alt = n % 2 == 1
            if not _per_word_frames(style):
                add(g0, g_end, texts, None, animation != "none", alt, g0)
            else:
                for i, w in enumerate(group):
                    w_end = group[i + 1].start - start if i + 1 < len(group) else g_end
                    animate = animation in PER_WORD_ANIMATIONS or (animation in PER_LINE_ANIMATIONS and i == 0)
                    add(w.start - start, w_end, texts, i, animate, alt, g0)

    if style and style.loop != "none":
        # Continuous motion: cut every event into LOOP_STEP slices, counted from the caption start.
        sliced = []
        for a, b, texts, active, phase, alt, g0 in events:
            t = a
            while b - t > 0.005:
                tick = int((t - g0) / LOOP_STEP + 1e-6)
                t_next = min(b, g0 + (tick + 1) * LOOP_STEP)
                sliced.append((t, t_next, texts, active, phase, alt, g0, tick))
                t = t_next
        events = sliced
    else:
        events = [(*e, None) for e in events]

    title_end = min(TITLE_SECONDS, length) if look.title else 0.0
    # The title enters with the caption style's animation.
    title_cuts = [k * ANIM_STEP for k in range(1, PHASES + 1)] if title_end and animation != "none" else []

    def title_phase(t: float) -> int | None:
        return int(t // ANIM_STEP) if title_cuts and t < PHASES * ANIM_STEP else None

    cuts = sorted({0.0, length, title_end, *(t for t in title_cuts if t < title_end),
                   *(t for e in events for t in e[:2] if 0 < t < length)})
    events.sort(key=lambda e: e[0])
    starts = [e[0] for e in events]
    for t0, t1 in zip(cuts, cuts[1:]):
        if t1 - t0 < 0.001:
            continue
        mid = (t0 + t1) / 2
        k = bisect.bisect_right(starts, mid) - 1
        current = events[k] if k >= 0 and events[k][0] <= mid < events[k][1] else None
        show_title = mid < title_end
        tp = title_phase(mid) if show_title else None
        if current:
            texts, active, phase, alt, _, tick = current[2:]
            yield t0, t1, (texts, active, show_title, phase, tp, tick, alt)
        else:
            yield t0, t1, (None, None, show_title, None, tp, None, False)


def _write_overlay_track(words: list[Word], start: float, end: float, look: Look, per_line: int,
                         size: tuple[int, int], work: Path) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    words = [w for w in words if w.start >= start - 0.05 and w.end <= end + 0.05]
    frames: dict[tuple, Path] = {}
    entries: list[list] = []  # [path, duration]
    for t0, t1, key in _timeline(words, start, end, look, per_line):
        if key not in frames:
            check()
            frames[key] = work / f"f{len(frames):04d}.png"
            draw_frame(size, look, *key).save(frames[key])
        if entries and entries[-1][0] == frames[key]:
            entries[-1][1] += t1 - t0
        else:
            entries.append([frames[key], t1 - t0])

    track = work / "overlay.ffconcat"
    lines = ["ffconcat version 1.0"]
    for path, duration in entries:
        lines += [f"file '{path.name}'", f"duration {max(duration, 0.001):.4f}"]
    lines.append(f"file '{entries[-1][0].name}'")  # concat demuxer needs the last file repeated
    track.write_text("\n".join(lines) + "\n")
    return track


def look_for(options: LookOptions, title: str | None) -> Look:
    style = None if options.caption_style == NO_CAPTIONS else CAPTION_STYLES[options.caption_style]
    return Look(style, options.caption_position, options.highlight_color, title if options.title_overlay else None,
                options.caption_size / 100, options.caption_offset / 100)


# --- Reframing ------------------------------------------------------------------


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


Rect = tuple[int, int, int, int]  # x, y, w, h in source pixels

# Two-person framing. Scores are rough "how good does this look" numbers in [~-1, ~1.2].
GROUP_MARGIN = 1.4        # face heights of room beside the outer faces in a shared shot
MIN_GROUP_FACE = 0.075    # in a shared shot faces must be at least this share of the output height
GROUP_BONUS = 0.15        # one natural shot beats a split when both frame the faces equally well
IDEAL_PANEL_FACE = 0.22   # face height / panel height for a comfortable head-and-shoulders panel
MIN_PANEL_ASPECT = 0.5    # narrower panels (e.g. side-by-side in 9:16) look like slivers


def _crop_rect(face: Face, src: tuple[int, int], aspect: float, zoom_to_face: bool,
               max_w: float | None = None) -> Rect:
    """Crop of the given aspect ratio around a face, clamped inside the source frame.

    `max_w` narrows (and so tightens) the crop, e.g. to keep the other person out of a panel.
    """
    sw, sh = src
    crop_h = sh
    if zoom_to_face:
        # Head and shoulders: about 3.5 face-heights tall, but never tighter than 45% of the frame.
        crop_h = min(sh, max(sh * 0.45, face.h * sh * 3.5))
    crop_w = crop_h * aspect
    if crop_w > sw:
        crop_w, crop_h = sw, sw / aspect
    if max_w and crop_w > max_w:
        # Never tighter than about 2.2 face-heights, or the panel is all forehead.
        crop_w = max(max_w, min(crop_w, face.h * sh * 2.2 * aspect))
        crop_h = crop_w / aspect
    crop_w, crop_h = _even(crop_w), _even(crop_h)
    x = min(max(face.cx * sw - crop_w / 2, 0), sw - crop_w)
    # Put the eyes about 40% down the crop.
    y = min(max(face.cy * sh - crop_h * 0.4, 0), sh - crop_h)
    return int(x), int(y), crop_w, crop_h


def _crop(rect: Rect) -> str:
    x, y, w, h = rect
    return f"crop={w}:{h}:{x}:{y}"


def _group_rect(faces: tuple[Face, ...], src: tuple[int, int], aspect: float) -> Rect | None:
    """One crop of the output aspect showing both people, or None if they are too far apart."""
    sw, sh = src
    left, right = faces
    face_h = max(left.h, right.h) * sh
    need_w = min(sw, (right.cx - left.cx) * sw + 2 * GROUP_MARGIN * face_h)
    # Never tighter than head-and-shoulders.
    crop_h = max(need_w / aspect, sh * 0.45, face_h * 3.5)
    crop_w = crop_h * aspect
    if crop_h > sh:
        crop_h, crop_w = sh, sh * aspect
    if crop_w > sw:
        crop_w, crop_h = sw, sw / aspect
    if crop_w < need_w - 1:
        return None
    crop_w, crop_h = _even(crop_w), _even(crop_h)
    cx = (left.cx + right.cx) / 2 * sw
    cy = (left.cy + right.cy) / 2 * sh
    x = min(max(cx - crop_w / 2, 0), sw - crop_w)
    y = min(max(cy - crop_h * 0.4, 0), sh - crop_h)
    return int(x), int(y), crop_w, crop_h


def _panel_rects(faces: tuple[Face, ...], src: tuple[int, int], panel: tuple[int, int]) -> list[Rect]:
    # Each panel is at most as wide as the gap between the two people, so neither shows up twice.
    gap = abs(faces[1].cx - faces[0].cx) * src[0]
    return [_crop_rect(f, src, panel[0] / panel[1], zoom_to_face=True, max_w=gap) for f in faces]


def _panels_score(faces: tuple[Face, ...], src: tuple[int, int], panel: tuple[int, int]) -> float:
    sw, sh = src
    aspect = panel[0] / panel[1]
    score = 0.0
    for face, other, (x, y, w, h) in zip(faces, faces[::-1], _panel_rects(faces, src, panel)):
        s = min(face.h * sh / h / IDEAL_PANEL_FACE, 1.0)
        # The other person showing up in this panel reads as a duplicate.
        half_face = other.h * sh * 0.4
        if x - half_face < other.cx * sw < x + w + half_face:
            s -= 0.6
        if panel[1] / h > 3:  # heavy upscaling looks soft
            s -= 0.2
        score += s
    score /= len(faces)
    if aspect < MIN_PANEL_ASPECT:
        score -= 0.8
    return score


def _group_score(faces: tuple[Face, ...], src: tuple[int, int], out: tuple[int, int]) -> float | None:
    rect = _group_rect(faces, src, out[0] / out[1])
    if rect is None:
        return None
    ratio = sum(f.h for f in faces) / len(faces) * src[1] / rect[3]
    if ratio < MIN_GROUP_FACE:
        return None
    return min(ratio / 0.14, 1.0) + GROUP_BONUS


def split_panels(layout: str, out: tuple[int, int]) -> tuple[int, int]:
    """Panel size for 'split' (stacked) or 'side' (side by side)."""
    width, height = out
    return (width, _even(height / 2)) if layout == "split" else (_even(width / 2), height)


def choose_pair_layout(subjects: Subjects, src: tuple[int, int], out: tuple[int, int],
                       allow_group: bool = True) -> str:
    """Pick the framing that shows two people best: a shared shot, stacked, or side by side."""
    scores = {name: _panels_score(subjects.faces, src, split_panels(name, out)) for name in ("split", "side")}
    if allow_group:
        group = _group_score(subjects.faces, src, out)
        if group is not None:
            scores["group"] = group
    return max(scores, key=scores.get)


def resolve_layout(layout: str, aspect: Aspect, subjects: Subjects | None,
                   src: tuple[int, int], out: tuple[int, int]) -> str:
    """Turn 'auto' (or a mode the footage can't support) into the layout actually rendered."""
    kind = subjects.kind if subjects else "none"
    if layout == "auto":
        if kind == "pair":
            return choose_pair_layout(subjects, src, out)
        return "track" if kind == "single" else "fill"
    if layout == "split":
        # Chosen explicitly: two people are always stacked half and half (side by side in 16:9).
        # A shot with one person can't be split, so that person fills the frame instead.
        if kind == "pair":
            return "side" if aspect == "16:9" else "split"
        return "track" if kind == "single" else "fill"
    if layout == "track" and kind == "none":
        return "fill"
    return layout


def _video_filter(layout: str, subjects: Subjects | None, src: tuple[int, int], out: tuple[int, int]) -> str:
    """Filter graph from [0:v] to [v]."""
    width, height = out
    if layout in ("split", "side"):
        pw, ph = split_panels(layout, out)
        first, second = (_crop(r) for r in _panel_rects(subjects.faces, src, (pw, ph)))
        if layout == "split":
            return (f"[0:v]split=2[p1][p2];"
                    f"[p1]{first},scale={width}:{ph},setsar=1[t];"
                    f"[p2]{second},scale={width}:{height - ph},setsar=1[b];"
                    f"[t][b]vstack=inputs=2[v]")
        return (f"[0:v]split=2[p1][p2];"
                f"[p1]{first},scale={pw}:{height},setsar=1[l];"
                f"[p2]{second},scale={width - pw}:{height},setsar=1[r];"
                f"[l][r]hstack=inputs=2[v]")
    rect = _group_rect(subjects.faces, src, width / height) if layout == "group" else None
    if rect is not None:
        return f"[0:v]{_crop(rect)},scale={width}:{height},setsar=1[v]"
    if layout == "track":
        speaker = max(subjects.faces, key=lambda f: f.h)
        rect = _crop_rect(speaker, src, width / height, zoom_to_face=False)
        return f"[0:v]{_crop(rect)},scale={width}:{height},setsar=1[v]"
    if layout == "fit":
        return (f"[0:v]split=2[bg][fg];"
                f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
                f"gblur=sigma=30,eq=brightness=-0.08[bgb];"
                f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease,setsar=1[fgs];"
                f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2[v]")
    return (f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1[v]")


def _zoom_intervals(words: list[Word], start: float, end: float) -> list[tuple[float, float]]:
    """Every other sentence gets a punch-in, which reads as a cut between two camera angles."""
    sentences = split_words([w for w in words if w.start >= start - 0.05 and w.end <= end + 0.05])
    spans = []
    for s in sentences[1::2][:MAX_ZOOM_INTERVALS]:
        a, b = max(0.0, s.start - start - 0.1), min(end - start, s.end - start + 0.1)
        if b - a >= 1.0:  # very short zooms feel like jitter
            spans.append((a, b))
    return spans


def _with_zoom(graph: str, spans: list[tuple[float, float]], size: tuple[int, int]) -> str:
    if not spans:
        return graph
    width, height = size
    inside = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in spans)
    factor = f"if({inside},{ZOOM_LEVEL},1)"
    return (graph.removesuffix("[v]") + "[vz];"
            f"[vz]scale=w='trunc({width}*{factor}/2)*2':h='trunc({height}*{factor}/2)*2':eval=frame,"
            f"crop={width}:{height},setsar=1[v]")


@dataclass(frozen=True)
class Segment:
    start: float              # seconds from the clip start
    end: float
    layout: str               # resolved layout for this shot
    subjects: Subjects | None


def plan_segments(shots: list[Shot], layout: str, aspect: Aspect, src: tuple[int, int],
                  out: tuple[int, int]) -> list[Segment]:
    """Resolve the layout per camera shot and merge neighbours that render identically."""
    segments: list[Segment] = []
    for shot in shots:
        resolved = resolve_layout(layout, aspect, shot.subjects, src, out)
        subjects = shot.subjects if resolved not in ("fill", "fit") else None
        if segments and segments[-1].layout == resolved and (subjects is None or segments[-1].subjects == subjects):
            segments[-1] = Segment(segments[-1].start, shot.end, resolved, segments[-1].subjects)
        else:
            segments.append(Segment(shot.start, shot.end, resolved, subjects))
    return segments


def _segments_filter(segments: list[Segment], src: tuple[int, int], out: tuple[int, int]) -> str:
    """One filter chain per shot, cut out with trim and joined back with concat, ending in [v]."""
    if len(segments) == 1:
        return _video_filter(segments[0].layout, segments[0].subjects, src, out)
    parts = ["[0:v]split=" + str(len(segments)) + "".join(f"[in{i}]" for i in range(len(segments)))]
    for i, seg in enumerate(segments):
        end = f":end={seg.end:.3f}" if i < len(segments) - 1 else ""
        parts.append(f"[in{i}]trim=start={seg.start:.3f}{end},setpts=PTS-STARTPTS[cut{i}]")
        chain = _video_filter(seg.layout, seg.subjects, src, out)
        chain = re.sub(r"\[(\w+)\]", lambda m: f"[{m.group(1)}_{i}]", chain).replace("[0:v]", f"[cut{i}]")
        parts.append(chain)
    parts.append("".join(f"[v_{i}]" for i in range(len(segments))) + f"concat=n={len(segments)}:v=1:a=0[v]")
    return ";".join(parts)


def render_clip(src: Path, src_size: tuple[int, int], words: list[Word], start: float, end: float,
                look_options: LookOptions, title: str, dest: Path) -> str:
    """Render one clip and return the layouts actually used, e.g. "split+track"."""
    width, height = output_size(look_options.aspect, look_options.resolution)
    look = look_for(look_options, title)

    if look_options.layout in ("auto", "track", "split"):
        shots = find_shots(src, start, end)
    else:
        shots = [Shot(0.0, end - start, Subjects("none", ()))]
    segments = plan_segments(shots, look_options.layout, look_options.aspect, src_size, (width, height))
    layout = "+".join(dict.fromkeys(seg.layout for seg in segments))
    graph = _segments_filter(segments, src_size, (width, height))
    if look_options.auto_zoom and not any(seg.layout in ("split", "side") for seg in segments):
        graph = _with_zoom(graph, _zoom_intervals(words, start, end), (width, height))

    cmd = [FFMPEG, "-y", "-v", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src)]
    if look.style or look.title:
        track = _write_overlay_track(words, start, end, look, look_options.words_per_line, (width, height),
                                     dest.parent / f"{dest.stem}_overlay")
        cmd += ["-f", "concat", "-safe", "0", "-i", str(track),
                "-filter_complex", f"{graph};[1:v]format=rgba[c];[v][c]overlay=0:0:eof_action=pass:format=auto[out]",
                "-map", "[out]"]
    else:
        cmd += ["-filter_complex", graph, "-map", "[v]"]
    cmd += ["-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            "-shortest", str(dest)]
    run(cmd)
    return layout


def preview(style_id: str, highlight: str, position: str, aspect: Aspect, title: str | None,
            size: int = 100, offset: int = 0) -> Image.Image:
    """Sample frame for the style picker, drawn by the same code as real clips."""
    width, height = output_size(aspect)
    preview_scale = min(360 / width, 640 / height)
    width, height = round(width * preview_scale), round(height * preview_scale)
    style = None if style_id == NO_CAPTIONS else CAPTION_STYLES[style_id]
    bg = Image.new("RGBA", (width, height))
    top, bottom = (52, 64, 85), (15, 23, 42)
    bg_draw = ImageDraw.Draw(bg)
    for y in range(height):
        f = y / height
        bg_draw.line([(0, y), (width, y)], fill=tuple(int(a + (b - a) * f) for a, b in zip(top, bottom)) + (255,))
    look = Look(style, position, highlight, title, size / 100, offset / 100)
    words = ("THIS", "CHANGED", "EVERYTHING")
    # Styles that reveal words show the whole caption; the rest show the middle word being spoken.
    active = len(words) - 1 if style and style.reveal else 1
    phase = 1 if style and style.animation in ("pop", "glitch") and style.group != "opus" else None
    frame = draw_frame((width, height), look, words, active, bool(title), phase)
    return Image.alpha_composite(bg, frame).convert("RGB")
