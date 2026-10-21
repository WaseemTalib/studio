"""Style library for stickman and real-world director modes.

The stickman styles come from Stickman Video Director
(https://github.com/kaomei/stickman-video-director), MIT License,
Copyright (c) 2026 Stickman Video Director contributors. See director/LICENSE.

Character anchors, voice block, music lock and negative constraints are the skill's tested wording and are
repeated verbatim in every clip prompt; do not paraphrase them.
"""

from dataclasses import dataclass

# --- Characters ------------------------------------------------------------------------------

_CLASSIC = ("Use minimalist two-dimensional {ink} line animation. Show the same consistent stick figure with a hollow "
            "circular head, no face, no eyes, no mouth, no hair, no clothing, no filled body, stable proportions, "
            "connected line limbs, and uniform medium line weight. Exactly two arms and two legs are visible per figure. "
            "Each limb appears only in its current pose: raising or lowering the right arm replaces that arm's previous "
            "line, never leaving a resting arm beneath an active arm. The figure stays {ink}; accent colors affect only "
            "props and graphic effects.")
# The skill allows occasional simple expressions by default; a faceless figure keeps the base unchanged.
_FACE_SWAP = ("no face, no eyes, no mouth,", "occasional simple line expressions, no detailed facial features,")

BEANIE_ANCHOR = ("One minimalist 2D animated stick figure with a hollow circular head, simple black line limbs, stable "
                 "head-to-body proportions and uniform medium line weight. The figure always wears the same bright red "
                 "smooth rounded knit beanie with no pom-pom, the same plain yellow t-shirt and the same black shorts. "
                 "Exactly two arms and two legs are visible. Keep this complete design, clothing silhouette and colors "
                 "identical in every shot; only pose and simple line expression may change.")

COZY_ANCHOR = ("One minimalist 2D cartoon protagonist with a white filled circular head, two small dark oval eyes, simple "
               "eyebrow strokes and a short curved mouth. The torso is a slim closed tapered shape with solid white fill "
               "inside its dark outline. Both arms and both legs are dark single-line strokes with constant medium weight; "
               "hands are small dark rounded shapes and feet are short dark rounded strokes. No hair or clothing "
               "accessories. Keep the same head-to-body proportions, white head and torso fill, and outline weight in "
               "every shot. Exactly two arms and two legs exist; a moving limb replaces its previous pose rather than "
               "leaving another limb behind. Expressions change only the simple facial marks, never the head shape or "
               "character design.")

PREHISTORIC_GRAMMAR = ("Crude hand-drawn 2D style with black outlines, white filled round heads, thin black stick limbs, "
                       "expressive simple cartoon eyes, brows and mouths, animal-hide clothing, earthy ochre and brown "
                       "scenery, small warm fire accents and subtle paper texture with irregular outlines.")

# --- Voice and music ---------------------------------------------------------------------------

TESTED_MALE_VOICE = ("One off-screen adult male narrator throughout. General American accent, warm clean baritone, "
                     "medium-low pitch, gentle chest resonance, no raspiness or breathiness. Calm conversational delivery, "
                     "restrained pitch variation, steady speaking pace, and close, dry studio sound. Keep the same vocal "
                     "timbre, register, accent, pace, and recording quality throughout. Emotional progression belongs to "
                     "the visuals and music; do not change the narrator's voice or switch to character dialogue.")
FEMALE_EXAMPLE = ("adult, General American, clear warm mid-high voice, gentle forward resonance, no rasp/breathiness, "
                  "lively conversational delivery with controlled pitch variation, steady pace, close dry studio recording")
DEFAULT_MUSIC = "quiet, thoughtful piano and subtle soft ambient synth"

AUDIO_ONLY = "Audio voiceover only, strictly no speech bubbles, no dialogue boxes."
DENSITY = ("Rapid scene changes, kinetic motion-graphic transformations, and frequent visual events, while preserving an "
           "identical stick-figure design, constant line weight, and strict temporal consistency.")
NO_TEXT = ("No visible words, letters, numbers, captions, subtitles, interface copy, palette labels, production "
           "annotations, logos or watermarks; message bubbles, content cards, clocks, meters and notifications are "
           "icon-only.")
UNIVERSAL_NEGATIVES = ("Negative constraints: no photorealism or unwanted 3D humanoid rendering, no extra limbs, malformed "
                       "anatomy, disconnected lines or changed proportions, no broken or changing line weight, no "
                       "unintended characters or irrelevant spectacle, no abstract liquid or shape morphing, no altered, "
                       "omitted, repeated, reordered or added dialogue, no speech bubbles, comic dialog balloons or visual "
                       "text boxes.")
COMPOSITION = {
    "16:9": "Stage the action across left, center and right with lateral tracking, horizontal match cuts and deliberate "
            "negative space.",
    "9:16": "Use foreground and background depth, vertical reveals, stacked motion, foreground passes and "
            "interface-safe space.",
}


@dataclass(frozen=True)
class Style:
    id: str
    name: str
    short: str
    best_for: str
    environment: str          # global environment wording; clip-specific settings are added for 2B, 3 and 4
    negatives: str            # style-specific negative clause
    palette_rule: str         # what the proposal's palette describes
    music: str                # default score when the plan does not choose one
    per_clip_location: bool   # 2B, 3 and 4 describe a setting per clip
    devices_rule: bool = True  # at least four visual devices per clip (Style 3 overrides)
    discussion_ending: bool = True
    generic: bool = False


STYLES: dict[str, Style] = {s.id: s for s in [
    Style("1-light", "Style 1 · Classic Minimalist (Light)", "Classic light",
          "High-density explainers, logic, mental models",
          "Flat, uniform, digitally pure-white canvas with no gray tint, paper texture, gradients, shadows, lighting, "
          "bloom, fog or 3D depth.",
          "Negative constraints for this style: no filled head or body, no body silhouettes, no separate hand or foot "
          "shapes, no inverted theme polarity, no unexplained colors.",
          "Up to three saturated accent colors, named in ordinary words, each with a meaning", DEFAULT_MUSIC, False),
    Style("1-dark", "Style 1 · Classic Minimalist (Dark)", "Classic dark",
          "High-density explainers, logic, mental models",
          "Flat, uniform, pitch-black canvas with pure white line art and high-contrast accent colors.",
          "Negative constraints for this style: no filled head or body, no body silhouettes, no separate hand or foot "
          "shapes, no inverted theme polarity, no unexplained colors.",
          "Up to three saturated accent colors, named in ordinary words, each with a meaning", DEFAULT_MUSIC, False),
    Style("2a", "Style 2A · Modern Studio Tech", "Studio tech",
          "Tech tutorials, AI tools, career insights, methodology",
          "In a modern bright white studio space with subtle light-gray perspective grid lines on the floor plane. "
          "High-key studio lighting, clean white negative space, sleek glowing cyan and electric blue glass holographic "
          "UI elements. STRICTLY MINIMALIST, NO CIRCUIT BOARD TEXTURES, NO SCI-FI WALL PANELS, NO CRACKED CONCRETE.",
          "Negative constraints: strictly minimalist studio aesthetic, no circuit board textures, no sci-fi wall "
          "panels, no spaceship corridors, no cracked concrete, no grunge textures, no photorealistic human skin, no "
          "speech bubbles, no dialogue text boxes.",
          "Studio palette and accent glows, named in ordinary words", DEFAULT_MUSIC, False),
    Style("2b", "Style 2B · Cinematic Story", "Cinematic story",
          "Emotional storytelling, personal growth, motivational journeys",
          "In a rich full-color cinematic environment. Cinematic volumetric lighting, soft depth of field, atmospheric "
          "narrative mood, with the character kept in smooth 2D vector animation style.",
          "Negative constraints: no photorealistic human skin or faces, no 3D humanoid CGI uncanny valley models, no "
          "chaotic line glitches, no speech bubbles, no dialogue text boxes.",
          "Environment mood and lighting palette, named in ordinary words", DEFAULT_MUSIC, True),
    Style("3", "Style 3 · Prehistoric Hand-Drawn / Dark Comedy", "Prehistoric",
          "Satirical stories, visual analogies, ironic callbacks",
          "Earthy prehistoric setting with ochre and brown tones, small warm fire accents and subtle paper texture. "
          + PREHISTORIC_GRAMMAR,
          "Negative constraints: no photorealistic skin, no glossy 3D characters, no modern studio UI, no extra "
          "humans, no duplicate props, no unintended identity or costume changes.",
          "Earthy palette with warm fire accents, named in ordinary words", "understated primitive percussion with a steady sparse pulse",
          True, devices_rule=False, discussion_ending=False),
    Style("4", "Style 4 · Cozy Storytelling", "Cozy storytelling",
          "Relatable life stories, emotional growth, educational storytelling",
          "Full-color 2D cartoon story environment with clear cartoon contours, gentle atmospheric light and depth, "
          "varied wide, close and moving shots; not hand-drawn, sketchbook or photorealistic 3D.",
          "Negative constraints: no white card border, no sketchbook or paper overlay, no photorealistic or 3D "
          "characters, no background clones, no pressure graphics leaking into calm scenes.",
          "Story-driven color and light per location, named in ordinary words",
          "a gentle story score with one recurring motif", True),
    Style("g-logical", "Logical Explainer", "Logical",
          "Clear reasoning, mental models, business ideas and step-by-step arguments",
          "Photorealistic real people in practical offices, workshops, homes and public spaces. Use clean composition, "
          "cause-and-effect actions, physical demonstrations and restrained cinematic lighting.",
          "Negative constraints: no animation, abstract shape-only scenes, plastic skin, warped faces, extra fingers, "
          "duplicate people, inconsistent wardrobe, logos, watermarks or unintended text.",
          "Clean neutral palette with purposeful accent colors, named in ordinary words",
          "precise understated score with a steady explanatory pulse", True,
          discussion_ending=False, generic=True),
    Style("g-tech", "Modern Tech", "Modern tech",
          "AI, software, innovation, startups, productivity and future-facing topics",
          "Photorealistic real people in contemporary offices, labs, studios and urban environments with practical "
          "screens, glass, cool natural light and grounded technology interactions.",
          "Negative constraints: no sci-fi spaceship interiors, floating fantasy interfaces, fake readable UI text, "
          "plastic skin, warped hands, duplicate people, logos or watermarks.",
          "Cool modern palette with controlled cyan or electric-blue accents, named in ordinary words",
          "minimal electronic score with warm human texture", True,
          discussion_ending=False, generic=True),
    Style("g-emotional", "Emotional Story", "Emotional",
          "Personal growth, relationships, motivation and human-centred brand stories",
          "Photorealistic real people in expressive real-world locations with motivated practical lighting, intimate "
          "close-ups, cinematic depth, natural gestures and emotionally meaningful objects.",
          "Negative constraints: no melodramatic posing, plastic skin, warped faces or hands, duplicate people, sudden "
          "wardrobe changes, floating props, logos, watermarks or unintended text.",
          "Cinematic palette that follows the emotional arc, named in ordinary words",
          "emotional cinematic score with one restrained recurring motif", True,
          discussion_ending=False, generic=True),
    Style("g-educational", "Educational Documentary", "Educational",
          "History, science, news, how-things-work and evidence-led lessons",
          "Grounded documentary imagery with real people, authentic locations, observational camera work, natural light, "
          "physical demonstrations and clearly motivated editorial cutaways.",
          "Negative constraints: no staged fiction presented as fact, invented documents or quotations, fake readable "
          "labels, distorted faces, duplicate people, logos, watermarks or unintended text.",
          "Naturalistic documentary palette with one consistent editorial accent, named in ordinary words",
          "subtle documentary underscore that never overwhelms narration", True,
          discussion_ending=False, generic=True),
    Style("g-satire", "Real-World Satire", "Satire",
          "Humour, social observations, ironic comparisons and playful commentary",
          "Photorealistic real people in recognizable everyday locations, staged with deadpan framing, precise reaction "
          "beats, visual irony and grounded physical comedy.",
          "Negative constraints: no cartoon rendering, grotesque facial distortion, unsafe stunts, warped hands, duplicate "
          "people, unexplained costume changes, logos, watermarks or unintended text.",
          "Natural everyday palette with one comedic accent color, named in ordinary words",
          "light rhythmic score with a continuous pulse and accented reaction beats", True, devices_rule=False,
          discussion_ending=False, generic=True),
    Style("g-lifestyle", "Cozy Lifestyle", "Lifestyle",
          "Wellness, habits, food, travel, home, family and optimistic everyday stories",
          "Photorealistic real people in warm homes, cafes, parks, neighbourhoods and travel locations with soft natural "
          "light, tactile detail, relaxed camera movement and candid human interaction.",
          "Negative constraints: no artificial showroom perfection, plastic skin, warped hands, duplicate people, abrupt "
          "wardrobe or location drift, logos, watermarks or unintended text.",
          "Warm story-led palette with natural materials and soft accents, named in ordinary words",
          "gentle acoustic score with one recurring motif", True,
          discussion_ending=False, generic=True),
]}


def character_anchor(style: str, clip: int, faceless: bool) -> str:
    """The frozen character block for Styles 1, 2 and 4 (Style 3 uses its approved cast cards)."""
    if style in ("1-light", "1-dark"):
        text = _CLASSIC.format(ink="white" if style == "1-dark" else "black")
        return text if faceless else text.replace(*_FACE_SWAP)
    if style in ("2a", "2b"):
        return BEANIE_ANCHOR
    if style == "4":
        return COZY_ANCHOR
    return PREHISTORIC_GRAMMAR


def music_lock(clip: int, music: str) -> str:
    music = music.strip().rstrip(".;, ")
    if clip == 0:
        return f"Audio: Voiceover clearly audible over {music}, establishing the musical theme for the whole video."
    return (f"Audio: Voiceover seamlessly continues the identical {music} from clip 1, maintaining identical tempo, "
            "instrumentation, and narrative momentum. Synchronized crisp SFX on physical actions.")


def public(mode: str = "stickman") -> list[dict]:
    generic = mode == "generic"
    return [{"id": s.id, "name": s.name, "short": s.short, "best_for": s.best_for}
            for s in STYLES.values() if s.generic == generic]
