"""Plan stickman or real-world videos in two AI-assisted phases.

Phase A creates the director's proposal and Phase B writes per-clip prompt details. Stickman rules condense
the Stickman Video Director skill (MIT, see LICENSE). Long videos are planned in batches so each request stays
within free-tier limits: first the outline, then storyboard rows, then prompt details a few clips at a time.
"""

import json
import logging

from .. import db
from ..maker.script import read_article
from ..pipeline import cancel, runner
from ..pipeline.highlights import HighlightError, structured
from . import assemble, audio_continuity, continuity
from .models import (MAX_CLIP_WORDS, MIN_CLIP_WORDS, STAGES_3, STAGES_5, ClipOutline, ClipPrompts, ClipRevision,
                     DirectorSettings, ProposalHeader, StoryRow, StoryRows)
from .styles import FEMALE_EXAMPLE, STYLES, TESTED_MALE_VOICE

log = logging.getLogger(__name__)


def submit(pid: str, kind: str) -> None:
    """Start writing the proposal ("proposal") or the prompts ("prompts") of a plan."""
    runner.submit_job(pid, lambda: (propose if kind == "proposal" else write_prompts)(pid), STAGES, db.update_plan,
                      light=True)

STAGES = {"outline": (0.0, 0.3), "storyboard": (0.3, 1.0), "prompts": (0.0, 1.0)}
ROWS_PER_REQUEST = 5
PROMPTS_PER_REQUEST = 3
SOURCE_CHARS = 12000
AI_STAGE = "director"

OUTLINE_SYSTEM = """You are the director of short English stick-figure animation videos (the Stickman Video \
Director method). From one source you write the director's proposal header and the narration of every ten-second clip.

Narration:
- English voiceover only, natural spoken English, about 20-25 words per ten-second clip, ending clips on sentence \
boundaries. Preserve the source's core claim, names, numbers and meaning. Strengthen a weak opening with an immediate \
hook, cut repetition and side branches, expand a thin source with an example, reframe or callback.
- Never invent research, statistics, quotations, product claims or facts.
- {structure}
Everything else (titles, core message, hook, summaries, purposes, directions) is written in {language}.

Characters: for Styles 3 and 4, cast contains every recurring role and each role has one complete immutable English \
identity anchor. Give different roles at least two stable non-color differences. For Styles 1 and 2, leave cast empty \
and use the built-in protagonist. Never put temporary pose, expression, camera, lighting or location details in an anchor.
Voice: one complete video-level narrator profile for every clip (identity/age, accent, timbre, pitch/register, \
resonance, texture, delivery, pace, close dry recording). voice_block is that profile as one self-contained English \
paragraph that also says the voice never changes and emotion belongs to visuals and music. The narrator is \
off-screen and audio-only. Narration begins on the first frame, fills all three timed beats at a steady natural pace and \
continues through the final frame. Never direct a speech break, reaction-only gap, intro hold or trailing interruption.
Music: one frozen English score description repeated in every clip; the emotional turn comes from the arrangement.
Palette: {palette}. Use ordinary color names only, never hex, RGB, HSL or Pantone notation.
Text: the generated video shows no words; optional 2-5 word overlays are listed separately for post-production.
{style_rules}"""

STRUCTURE_5 = """Structure the script with the 5-stage High-Completion Heartbeat Engine: 1 Golden Hook (a \
counter-intuitive question or visual paradox in the first seconds, never a platitude), 2 Disrupting Assumptions \
(state the common belief, then shatter it in one sentence), 3 Unveiling Insider Secrets (hidden mechanics or the real \
obstacle), 4 Ultimate Truth Revelation (the underlying logic or human truth, the "aha"), 5 Elevation & Discussion (a \
memorable punchline and a provocative, open-ended question that makes viewers debate in the comments; the last clip \
must end with it). Stage values: {stages}. Allocation for {n} clips: {allocation}."""

STRUCTURE_3 = """Tell it as a visual story: setup → escalation → ironic payoff, using deadpan reactions under continuous \
narration and motion. Keep the same 20-25 word VO target. No forced discussion question. It is fictional analogy, never a claim about \
real prehistory. Stage values: {stages}."""

STORYBOARD_SYSTEM = """You turn an approved director's outline into storyboard rows for ten-second stick-figure \
clips. Write in {language} (VO stays as given). For each clip:
- three timed beats: 0-3s establish or inherit the premise, 3-7s transform/escalate/explain the metaphor through \
physical character action, 7-10s climax and the outgoing transition;
- a perceptible visual change every two to three seconds; {devices}
- concrete character actions (leaping, touching glass, drawing luminous lines, opening doors); never an idle \
character, never abstract liquid or shape morphing; effects must clarify the spoken idea;
- keep narration and purposeful visual motion active in every beat, including deadpan reactions and transitions;
- composition for {aspect}: {composition}
- an ending state the next clip inherits (pose, moving object, travel direction, shape, camera move), or a named \
location/time cut where the style allows it;
- no words, letters or numbers on screen; icon-only bubbles, cards, clocks and meters.
{style_rules}"""

PROMPT_SYSTEM = """You write the per-clip details of standalone English video-generation prompts (Google Flow, \
Omni Flash) for an approved stick-figure storyboard. All output is English. The character design, voice, music \
lock and negative constraints are added separately: do NOT describe the character's appearance or the narrator's \
voice; describe only setting, palette roles, composition, positions, props, actions, camera and sound effects.
- beats: exactly three (0-3s, 3-7s, 7-10s). Split the clip's VO into the three beats in order, copying the words \
exactly (no additions, omissions or changes); each beat's action shows concrete character actions tied to its words.
- Keep narration and purposeful motion active in all three beats; never create an intro hold, reaction-only gap, frozen \
ending or audio reset.
- first_frame inherits the previous clip's ending; final_frame sets up the next clip; stitch names the join.
- Track props: who holds what, where it starts and ends; no spawning or teleporting objects.
- No visible words, letters, numbers or interface text in the scene; icon-only symbols. Ordinary color names only, \
never hex/RGB/Pantone.
{style_rules}"""

GENERIC_OUTLINE_SYSTEM = """You are the director of short photorealistic videos featuring real people in real-world \
scenes. From one source, write a director's proposal and the narration for every ten-second clip.

Narration:
- English voiceover only, natural spoken English, about 20-25 words per ten-second clip, with natural sentence ends. \
Preserve the source's claims, names, numbers and meaning. Open with a concrete hook, remove repetition, and build a \
clear beginning, development and resolution.
- Never invent research, statistics, quotations, product claims or facts.
- Stage values should describe the narrative job plainly, such as Hook, Context, Development, Turning Point and Resolution.
Everything else (titles, core message, hook, summaries, purposes and directions) is written in {language}.

People and continuity: cast contains every recurring real person or important physical subject, using one short role ID \
exactly across all storyboard rows. Each anchor is a complete immutable English identity description: approximate age, \
height and build, skin tone, facial geometry, hairline and hairstyle, wardrobe layers and colors, footwear, one recognizable \
accessory, handedness and owned props. Give different people at least three stable visible differences. Exclude temporary \
pose, expression, camera, lighting and location details. Do not name a real public person unless supplied.
Voice: one complete video-level narrator profile for every clip. The narrator remains off-screen and audio-only. \
Narration begins on the first frame, fills all three timed beats at a steady natural pace and continues through the final \
frame. Never direct a speech break, reaction-only gap, intro hold or trailing interruption.
Music: one frozen English score description repeated in every clip; arrangement may follow the emotional arc.
Palette: {palette}. Use ordinary color names only, never hex, RGB, HSL or Pantone notation.
Text: generated footage contains no readable words; optional short overlays are listed separately for post-production.
{style_rules}"""

GENERIC_STORYBOARD_SYSTEM = """You turn an approved outline into storyboard rows for ten-second photorealistic clips \
with real people in real-world scenes. Write in {language} (VO stays as given). For each clip:
- three timed beats: 0-3s establish or inherit the scene, 3-7s develop the idea through visible human action, and 7-10s \
land the point and create the outgoing transition;
- a perceptible visual change every two to three seconds; {devices}
- concrete, filmable actions, authentic locations and motivated props; avoid vague montage filler;
- keep narration and purposeful visual motion active in every beat, including reactions and transitions;
- composition for {aspect}: {composition}
- preserve each person's face, age, build, hair, wardrobe and owned props; name only the cast roles visible in the clip;
- use cast role IDs exactly, never synonyms such as “the man” or “the woman” when a named role exists;
- end with a pose, action, prop, direction or named location/time cut that the next clip can inherit;
- no readable words, letters or numbers in generated footage. Reserve any text for post-production overlays.
{style_rules}"""

GENERIC_PROMPT_SYSTEM = """You write per-clip details for standalone photorealistic video-generation prompts (Google \
Flow, Omni Flash) from an approved real-world storyboard. All output is English. Recurring cast anchors, voice, music and \
negative constraints are inserted separately: do not rewrite appearance anchors or the narrator voice. Describe the \
setting, palette, composition, positions, props, visible actions, camera and sound effects.
- beats: exactly three (0-3s, 3-7s, 7-10s). Split the clip's VO across them in order, copying every word exactly.
- Keep narration and purposeful motion active in all three beats; never create an intro hold, reaction-only gap, frozen \
ending or audio reset.
- first_frame inherits the previous ending; final_frame sets up the next clip; stitch names the visual or editorial join.
- Track who holds each prop and where it starts and ends. Keep faces, bodies, wardrobe and locations coherent.
- Name characters by their exact cast role ID in every action; do not replace a role with a generic pronoun or synonym.
- No readable words, letters, numbers, interface text, logos or watermarks in generated footage.
{style_rules}"""

STYLE_RULES = {
    "1-light": "Style 1 Classic Minimalist, light theme: flat pure-white canvas, black line stick figure, up to three "
               "saturated accent colors on props and graphics only.",
    "1-dark": "Style 1 Classic Minimalist, dark theme: flat pitch-black canvas, white line stick figure, up to three "
              "saturated accent colors on props and graphics only.",
    "2a": "Style 2A Modern Studio Tech: bright white high-key studio, light-gray perspective floor grid, floating "
          "cyan/blue glass UI; the figure wears a red beanie and yellow t-shirt. Physical interactions such as bumping "
          "a glass ceiling, tapping the floor grid to send a cyan ripple, drawing a luminous bridge with a light pen, "
          "touching a floating glass panel to reveal an icon-only lock, pushing open a minimalist vault door.",
    "2b": "Style 2B Cinematic Story: full-color cinematic environments (warm lamplit room, dusk window, desert road, "
          "summit above clouds) with volumetric light; the figure keeps its red beanie, yellow t-shirt and 2D look.",
    "3": "Style 3 Prehistoric Hand-Drawn / Dark Comedy: crude hand-drawn black outlines, white filled heads with "
         "expressive cartoon faces, animal-hide costumes, earthy caves, camps, forests and rivers with small fire "
         "accents. Turn abstract mechanisms into physical tasks and props. Cast only as many characters as the story "
         "needs, each with a full cast card and two visible non-color differences; name which roles appear per clip. "
         "Keep deadpan reactions visually active under continuous narration; do not force four devices into a quiet joke. Score: understated "
         "primitive percussion or another story-appropriate music, not optimistic piano.",
    "4": "Style 4 Cozy Storytelling: minimalist characters with white filled heads and torsos and dark stick limbs in "
         "full-color 2D cartoon locations. Each new ten-second clip normally opens in a different recognizable "
         "setting (an office, a metro platform, a cafe, a park...) with wide, close and tracking shots inside it; name "
         "each location and what carries over the cut (character, prop, direction, or a named time jump). Cozy is the "
         "visual warmth, not forced cheerfulness. Keep props' owners and states consistent across cuts.",
    "g-logical": "Logical Explainer: real people demonstrate cause and effect with practical objects in real offices, "
                 "workshops, homes and public spaces. Every visual must clarify the spoken reasoning.",
    "g-tech": "Modern Tech: real people use grounded contemporary technology in offices, labs, studios and cities. "
              "Keep interfaces unreadable and secondary to human actions; avoid fantasy holograms.",
    "g-emotional": "Emotional Story: real people, intimate acting, meaningful props and cinematic real-world locations. "
                   "Build emotion through behaviour, framing and light rather than melodrama.",
    "g-educational": "Educational Documentary: real people, authentic locations, physical demonstrations and factual "
                     "editorial cutaways. Never stage invented evidence or present fiction as fact.",
    "g-satire": "Real-World Satire: real people perform grounded physical comedy in recognizable everyday places. Use "
                "deadpan framing, reaction beats and visual irony without caricaturing anatomy.",
    "g-lifestyle": "Cozy Lifestyle: real people in warm homes, cafes, parks, neighbourhoods and travel locations. Use "
                   "natural interaction, tactile details and relaxed camera movement.",
}

VOICE_PREF = {
    "suggest": "No preference: propose the voice that fits the source best (any gender or age), as a complete profile.",
    "male": "Use exactly this tested male profile (copy it into voice_block verbatim): " + TESTED_MALE_VOICE,
    "female": f"A bright adult female voice, e.g. {FEMALE_EXAMPLE}.",
}


def _allocation(n: int) -> str:
    if n <= 3:
        return "clip 1 hook and conflict, clip 2 the hidden reality, last clip the truth and the comment question"
    if n <= 6:
        return ("clip 1 Golden Hook, clip 2 Disrupting Assumptions, clip 3 Insider Secrets, middle clips the "
                "mechanism and Ultimate Truth, last clip Elevation & Discussion")
    return ("clip 1 Golden Hook, then about 15-20% Disrupting Assumptions, 35-40% Insider Secrets, 30-35% Ultimate "
            "Truth, and the final 1-2 clips Elevation & Discussion")


def _ask(system: str, prompt: str, schema, settings: DirectorSettings):
    return structured(system, prompt, schema, service=settings.ai_service, stage=AI_STAGE)


def _material(settings: DirectorSettings) -> str:
    text = settings.source.strip()
    if settings.url:
        title, article = read_article(settings.url)
        text = f"{text}\n\n{title or ''}\n{article}".strip()
    return text[:SOURCE_CHARS]


def _language_note(settings: DirectorSettings) -> str:
    return settings.language if settings.language.lower() != "english" else "English (leave reference fields empty)"


DEFAULT_DEVICES = ("character action", "prop interaction", "camera move", "foreground transition", "lighting change")


def _plain_color_text(value: str) -> str:
    return assemble.TECH_COLOR.sub("ordinary named color", value)


def _normalize_rows(settings: DirectorSettings, proposal: dict) -> None:
    for row in proposal.get("rows") or []:
        beats = [str(beat).strip() for beat in row.get("beats") or [] if str(beat).strip()]
        while len(beats) < 3:
            beats.append("Narration and the planned character action continue into the next timed beat.")
        row["beats"] = [_plain_color_text(beat) for beat in beats[:3]]
        devices = []
        for device in row.get("devices") or []:
            cleaned = _plain_color_text(str(device).strip())
            if cleaned and cleaned not in devices:
                devices.append(cleaned)
        if STYLES[settings.style].devices_rule:
            devices.extend(device for device in DEFAULT_DEVICES if device not in devices and len(devices) < 4)
        row["devices"] = devices
        row["scene"] = _plain_color_text(str(row.get("scene") or ""))
        row["camera"] = _plain_color_text(str(row.get("camera") or ""))
        row["end_state"] = _plain_color_text(str(row.get("end_state") or "").strip()) or (
            "Continue the final pose, prop state and camera direction directly into the next clip.")
    continuity.normalize_cast_and_roles(proposal)


def normalize_proposal(settings: DirectorSettings, proposal: dict) -> dict:
    """Normalize AI-owned proposal structures once before checks, storage and prompt assembly."""
    audio_continuity.normalize_proposal(proposal)
    if settings.style in ("1-light", "1-dark"):
        proposal["palette"] = (proposal.get("palette") or [])[:3]
    for item in proposal.get("palette") or []:
        item["color"] = _plain_color_text(str(item.get("color") or ""))
        item["meaning"] = _plain_color_text(str(item.get("meaning") or ""))
    for card in proposal.get("cast") or []:
        card["anchor"] = _plain_color_text(str(card.get("anchor") or ""))
        card["summary"] = _plain_color_text(str(card.get("summary") or ""))
    proposal["environment_mood"] = _plain_color_text(str(proposal.get("environment_mood") or ""))
    _normalize_rows(settings, proposal)
    proposal["checks"] = assemble.proposal_checks(settings, proposal)
    return proposal


def refresh_saved(plan: dict) -> dict:
    """Idempotently upgrade completed saved proposals to the current normalized check contract."""
    if not plan.get("proposal") or plan.get("status") in ("queued", "processing"):
        return plan
    proposal = json.loads(json.dumps(plan["proposal"]))
    normalize_proposal(DirectorSettings(**plan["settings"]), proposal)
    if proposal != plan["proposal"]:
        db.update_plan(plan["id"], proposal=proposal)
        plan = {**plan, "proposal": proposal}
    return plan


def _prepare_header(settings: DirectorSettings, header: ProposalHeader) -> None:
    header.voice_block = audio_continuity.without_gaps(header.voice_block)
    header.music = audio_continuity.without_gaps(header.music)
    if settings.style in ("1-light", "1-dark"):
        header.palette = header.palette[:3]
    unique = []
    seen = set()
    for card in header.cast:
        key = continuity.role_key(card.role)
        if key and key not in seen:
            unique.append(card)
            seen.add(key)
    header.cast = unique


def _header_problems(settings: DirectorSettings, header: ProposalHeader, clips: list[ClipOutline]) -> list[str]:
    style = STYLES[settings.style]
    problems = []
    short = [clip.clip for clip in clips
             if not MIN_CLIP_WORDS <= len(clip.vo.split()) <= MAX_CLIP_WORDS]
    if short:
        problems.append(f"clips {short} need {MIN_CLIP_WORDS}-{MAX_CLIP_WORDS} spoken words")
    total_words = sum(len(clip.vo.split()) for clip in clips)
    if not len(clips) * MIN_CLIP_WORDS <= total_words <= len(clips) * 28:
        problems.append(f"total narration needs {len(clips) * MIN_CLIP_WORDS}-{len(clips) * 28} spoken words")
    if not header.voice_block.strip():
        problems.append("voice_block must contain the complete narrator profile")
    if not header.music.strip():
        problems.append("music must contain one continuous score description")
    if style.discussion_ending and clips and "?" not in clips[-1].vo:
        problems.append("the final clip must end with a discussion question")
    if settings.style == "3" and not header.cast:
        problems.append("Style 3 needs cast cards for every story character")
    if any(len(card.anchor.strip()) < 80 for card in header.cast):
        problems.append("every cast anchor needs a complete immutable appearance description")
    if settings.style == "4" and len(clips) > 1:
        locations = [clip.location.strip().lower() for clip in clips]
        if len(set(locations)) < min(len(clips), max(2, len(clips) - 1)):
            problems.append("Style 4 needs a different recognizable setting for nearly every clip")
    visual = json.dumps({"palette": [item.model_dump() for item in header.palette],
                         "environment": header.environment_mood, "cast": [card.model_dump() for card in header.cast]})
    if assemble.TECH_COLOR.search(visual):
        problems.append("visual directions must use ordinary color names, never technical color notation")
    return problems


# --- Phase A ---------------------------------------------------------------------------------------


def propose(pid: str) -> None:
    """Write (or recompose) the director's proposal of a plan."""
    plan = db.get_plan(pid)
    settings = DirectorSettings(**plan["settings"])
    revision = plan.get("revision") or {}
    previous = json.loads(json.dumps(plan["proposal"])) if revision and plan.get("proposal") else None
    if previous:
        normalize_proposal(settings, previous)
    style = STYLES[settings.style]
    n = settings.clips

    runner.stage(pid, "outline")
    clip_only = revision.get("clip")
    if previous and clip_only:
        proposal = _revise_clip(settings, previous, clip_only, revision.get("feedback", ""))
    else:
        header = _outline(settings, previous, revision.get("feedback", ""))
        runner.stage(pid, "storyboard")
        rows = _storyboard(pid, settings, header)
        proposal = {**header.model_dump(), "rows": [r.model_dump() for r in rows]}
    if settings.voice == "male":
        proposal["voice_block"] = TESTED_MALE_VOICE
    normalize_proposal(settings, proposal)
    failed = [check["text"] for check in proposal["checks"] if not check["ok"]]
    if failed:
        raise HighlightError("Proposal did not pass its checks: " + "; ".join(failed))
    cancel.check()
    db.update_plan(pid, status="done", stage=None, progress=1.0, error=None, proposal=proposal,
                   title=proposal.get("title_en") or plan["title"], revision=None)
    log.info("plan %s: proposal with %d clips (%s)", pid, n, style.short)


def _outline(settings: DirectorSettings, previous: dict | None, feedback: str) -> ProposalHeader:
    style = STYLES[settings.style]
    n = settings.clips
    if settings.mode == "generic":
        system = GENERIC_OUTLINE_SYSTEM.format(language=_language_note(settings), palette=style.palette_rule,
                                               style_rules=STYLE_RULES[settings.style])
    else:
        stages = STAGES_5 if style.discussion_ending else STAGES_3
        structure = (STRUCTURE_5.format(stages=", ".join(stages), n=n, allocation=_allocation(n))
                     if style.discussion_ending else STRUCTURE_3.format(stages=", ".join(stages)))
        system = OUTLINE_SYSTEM.format(structure=structure, language=_language_note(settings),
                                       palette=style.palette_rule, style_rules=STYLE_RULES[settings.style])
    voice = VOICE_PREF.get(settings.voice) or f"The user asks for: {settings.voice_custom}. Expand it into a complete profile."
    prompt = (f"Clips: exactly {n} (total {settings.seconds} seconds), numbered 1-{n}; about {n * 20}-{n * 25} words "
              f"of VO in total.\nAspect ratio: {settings.aspect}. Style: {style.name}.\nVoice: {voice}\n"
              + (f"Tone: {settings.tone}\n" if settings.tone.strip() else "")
              + f"\nSource ({settings.source_kind}):\n{_material(settings)}")
    if previous:
        prompt += ("\n\nThis is a revision. The previous proposal (keep what the feedback does not change):\n"
                   + json.dumps({k: previous.get(k) for k in ("title_en", "core_message", "hook", "voice_block",
                                                               "music", "palette", "cast", "clips")})[:14000]
                   + (f"\n\nFeedback to apply:\n{feedback}" if feedback.strip() else
                      "\n\nThe global settings changed: recompose staging, camera and composition for them."))
    for attempt in range(2):
        header = _ask(system, prompt, ProposalHeader, settings)
        _prepare_header(settings, header)
        if len(header.clips) >= n:
            clips = [ClipOutline(**{**c.model_dump(), "clip": i + 1}) for i, c in enumerate(header.clips[:n])]
            problems = _header_problems(settings, header, clips)
            if not problems:
                header.clips = clips
                return header
            prompt += ("\n\nThe proposal failed these required checks: " + "; ".join(problems) +
                       ". Rewrite the proposal to satisfy every item while preserving the source and requested style.")
        else:
            prompt += f"\n\nYour last answer had {len(header.clips)} clips; it must have exactly {n}."
    raise HighlightError("The AI did not produce enough continuous narration for every ten-second clip; try again")


def _storyboard(pid: str, settings: DirectorSettings, header: ProposalHeader) -> list[StoryRow]:
    style = STYLES[settings.style]
    template = GENERIC_STORYBOARD_SYSTEM if settings.mode == "generic" else STORYBOARD_SYSTEM
    system = template.format(
        language=_language_note(settings), aspect=settings.aspect, composition=assemble.COMPOSITION[settings.aspect],
        devices=("use at least four relevant visual devices per clip;" if style.devices_rule else
                 "use as many devices as the joke needs while narration and motion continue;"),
        style_rules=STYLE_RULES[settings.style])
    context = json.dumps({"title": header.title_en, "music": header.music, "palette": [p.model_dump() for p in header.palette],
                          "cast": [c.model_dump() for c in header.cast]})
    rows: dict[int, StoryRow] = {}
    outlines = header.clips
    for start in range(0, len(outlines), ROWS_PER_REQUEST):
        batch = outlines[start:start + ROWS_PER_REQUEST]
        around = outlines[max(0, start - 1):start + len(batch) + 1]
        prompt = (f"Video: {context}\n\nOutline of these clips and their neighbours:\n"
                  + "\n".join(f"Clip {c.clip} [{c.stage}] {c.purpose} | location: {c.location or '-'} | VO: {c.vo}"
                              for c in around)
                  + f"\n\nWrite storyboard rows for clips {batch[0].clip}-{batch[-1].clip} only.")
        for _ in range(2):
            got = _ask(system, prompt, StoryRows, settings)
            rows.update({r.clip: r for r in got.rows if batch[0].clip <= r.clip <= batch[-1].clip})
            missing = [c.clip for c in batch if c.clip not in rows]
            if not missing:
                break
            prompt += f"\n\nRows for clips {missing} were missing; give every requested clip."
        runner.stage(pid, "storyboard", min(1.0, (start + len(batch)) / len(outlines)))
    missing = [c.clip for c in outlines if c.clip not in rows]
    if missing:
        raise HighlightError(f"The AI did not write storyboard rows for clips {missing}; try again")
    normalized = {
        "cast": [card.model_dump() for card in header.cast],
        "rows": [rows[clip.clip].model_dump() for clip in outlines],
    }
    _normalize_rows(settings, normalized)
    return [StoryRow(**row) for row in normalized["rows"]]


def _revise_clip(settings: DirectorSettings, proposal: dict, clip: int, feedback: str) -> dict:
    """Redo one clip of the proposal: its outline and storyboard row."""
    style = STYLES[settings.style]
    template = GENERIC_STORYBOARD_SYSTEM if settings.mode == "generic" else STORYBOARD_SYSTEM
    system = template.format(
        language=_language_note(settings), aspect=settings.aspect, composition=assemble.COMPOSITION[settings.aspect],
        devices="use at least four relevant visual devices;" if style.devices_rule else
                "use reaction beats while narration and motion continue;",
        style_rules=STYLE_RULES[settings.style]) + (
        "\nYou revise ONE clip: return its outline (stage, purpose, English VO of about 20-25 words, location) and its "
        "storyboard row, keeping it connected to the neighbouring clips.")
    clips = proposal["clips"]
    around = [c for c in clips if abs(c["clip"] - clip) <= 1]
    prompt = (f"Video: {proposal.get('title_en')} — {proposal.get('core_message')}\n"
              f"Locked cast bible: {json.dumps(proposal.get('cast') or [], ensure_ascii=False)}\nNeighbouring clips:\n"
              + "\n".join(json.dumps(c) for c in around)
              + f"\nCurrent row of clip {clip}:\n{json.dumps(proposal['rows'][clip - 1])}"
              + f"\n\nRevise clip {clip} with this feedback:\n{feedback}")
    for _ in range(2):
        got = _ask(system, prompt, ClipRevision, settings)
        words = len(got.outline.vo.split())
        out = json.loads(json.dumps(proposal))
        out["clips"][clip - 1] = {**got.outline.model_dump(), "clip": clip}
        out["rows"][clip - 1] = {**got.row.model_dump(), "clip": clip}
        normalize_proposal(settings, out)
        failed = [check["text"] for check in out["checks"] if not check["ok"]]
        if MIN_CLIP_WORDS <= words <= MAX_CLIP_WORDS and not failed:
            return out
        prompt += (f"\n\nThe revision failed these required checks: {'; '.join(failed) or 'continuous narration length'}. "
                   "Revise the clip again and satisfy every check.")
    raise HighlightError("The revised clip did not pass every proposal check; try again")


# --- Phase B ---------------------------------------------------------------------------------------


def write_prompts(pid: str) -> None:
    """The production package for the approved proposal (or just one clip, with feedback)."""
    plan = db.get_plan(pid)
    settings = DirectorSettings(**plan["settings"])
    proposal = normalize_proposal(settings, json.loads(json.dumps(plan["proposal"])))
    failed = [check["text"] for check in proposal["checks"] if not check["ok"]]
    if failed:
        raise HighlightError("Cannot write prompts until the proposal passes its checks: " + "; ".join(failed))
    revision = plan.get("revision") or {}
    only = revision.get("clip")
    previous_package = plan.get("package") or {}
    existing = {c["clip"]: c for c in previous_package.get("clips", [])}
    upgrade_all = bool(existing) and previous_package.get("prompt_skills_version") != assemble.PROMPT_SKILLS_VERSION
    wanted = [only] if only and not upgrade_all else [c["clip"] for c in proposal["clips"]]

    runner.stage(pid, "prompts")
    details = {}
    for start in range(0, len(wanted), PROMPTS_PER_REQUEST):
        batch = wanted[start:start + PROMPTS_PER_REQUEST]
        feedback = revision.get("feedback", "") if only in batch else ""
        got = _prompt_details(settings, proposal, batch, feedback)
        details.update(got)
        runner.stage(pid, "prompts", (start + len(batch)) / len(wanted))
    clips = []
    for c in proposal["clips"]:
        n = c["clip"]
        if n in details:
            clips.append(assemble.clip_prompt(settings, proposal, n, details[n]))
        elif n in existing:
            clips.append(existing[n])
    package = assemble.package(settings, proposal, clips)
    cancel.check()
    db.update_plan(pid, status="done", stage=None, progress=1.0, error=None, proposal=proposal, package=package,
                   revision=None)


def _prompt_details(settings: DirectorSettings, proposal: dict, batch: list[int], feedback: str) -> dict[int, dict]:
    style = STYLES[settings.style]
    template = GENERIC_PROMPT_SYSTEM if settings.mode == "generic" else PROMPT_SYSTEM
    system = template.format(style_rules=STYLE_RULES[settings.style])
    clips, rows = proposal["clips"], proposal["rows"]
    lines = []
    for n in range(max(1, batch[0] - 1), min(len(clips), batch[-1] + 1) + 1):
        c, r = clips[n - 1], rows[n - 1]
        lines.append(json.dumps({"clip": n, "requested": n in batch, "stage": c["stage"], "location": c["location"],
                                 "vo": c["vo"], "scene": r["scene"], "beats": r["beats"], "devices": r["devices"],
                                 "camera": r["camera"], "sfx": r["bgm_sfx"], "end_state": r["end_state"],
                                 "roles": r["roles"]}, ensure_ascii=False))
    prompt = (f"Aspect ratio {settings.aspect}; {style.name}; {len(clips)} clips.\n"
              f"Palette: {json.dumps(proposal.get('palette'), ensure_ascii=False)}; mood: {proposal.get('environment_mood')}\n"
              f"Locked cast bible: {json.dumps(proposal.get('cast') or [], ensure_ascii=False)}\n"
              f"Storyboard (translate any non-English planning text into English):\n" + "\n".join(lines)
              + f"\n\nWrite prompt details for clips {batch} only."
              + (f"\nRevise with this feedback: {feedback}" if feedback.strip() else ""))
    out: dict[int, dict] = {}
    for _ in range(2):
        got = _ask(system, prompt, ClipPrompts, settings)
        out.update({p.clip: p.model_dump() for p in got.clips if p.clip in batch})
        missing = [n for n in batch if n not in out]
        if not missing:
            return out
        prompt += f"\n\nDetails for clips {missing} were missing; give every requested clip."
    raise HighlightError(f"The AI did not write prompts for clips {[n for n in batch if n not in out]}; try again")
