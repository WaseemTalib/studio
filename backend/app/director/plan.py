"""Planning with the AI: Phase A (director's proposal) and Phase B (per-clip prompt details).

The rules below condense the Stickman Video Director skill (MIT, see LICENSE). Long videos are planned in
batches so each request stays within free-tier limits: first the outline (header and every clip's VO),
then the storyboard rows, then the prompt details, a few clips at a time.
"""

import json
import logging

from .. import db
from ..maker.script import read_article
from ..pipeline import cancel, runner
from ..pipeline.highlights import HighlightError, structured
from . import assemble
from .models import (STAGES_3, STAGES_5, ClipOutline, ClipPrompts, ClipRevision, DirectorSettings, ProposalHeader,
                     StoryRow, StoryRows)
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

Voice: one complete video-level narrator profile for every clip (identity/age, accent, timbre, pitch/register, \
resonance, texture, delivery, pace, close dry recording). voice_block is that profile as one self-contained English \
paragraph that also says the voice never changes and emotion belongs to visuals and music. The narrator is \
off-screen and audio-only.
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

STRUCTURE_3 = """Tell it as a visual story: setup → escalation → ironic payoff, leaving room for deadpan reaction \
beats (VO may run shorter than 20 words). No forced discussion question. It is fictional analogy, never a claim about \
real prehistory. Stage values: {stages}."""

STORYBOARD_SYSTEM = """You turn an approved director's outline into storyboard rows for ten-second stick-figure \
clips. Write in {language} (VO stays as given). For each clip:
- three timed beats: 0-3s establish or inherit the premise, 3-7s transform/escalate/explain the metaphor through \
physical character action, 7-10s climax and the outgoing transition;
- a perceptible visual change every two to three seconds; {devices}
- concrete character actions (leaping, touching glass, drawing luminous lines, opening doors); never an idle \
character, never abstract liquid or shape morphing; effects must clarify the spoken idea;
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
- first_frame inherits the previous clip's ending; final_frame sets up the next clip; stitch names the join.
- Track props: who holds what, where it starts and ends; no spawning or teleporting objects.
- No visible words, letters, numbers or interface text in the scene; icon-only symbols. Ordinary color names only, \
never hex/RGB/Pantone.
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
         "Reaction pauses of 0.5-2 s are welcome; do not force four devices into a quiet joke. Score: understated "
         "primitive percussion or another story-appropriate music, not optimistic piano.",
    "4": "Style 4 Cozy Storytelling: minimalist characters with white filled heads and torsos and dark stick limbs in "
         "full-color 2D cartoon locations. Each new ten-second clip normally opens in a different recognizable "
         "setting (an office, a metro platform, a cafe, a park...) with wide, close and tracking shots inside it; name "
         "each location and what carries over the cut (character, prop, direction, or a named time jump). Cozy is the "
         "visual warmth, not forced cheerfulness. Keep props' owners and states consistent across cuts.",
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


# --- Phase A ---------------------------------------------------------------------------------------


def propose(pid: str) -> None:
    """Write (or recompose) the director's proposal of a plan."""
    plan = db.get_plan(pid)
    settings = DirectorSettings(**plan["settings"])
    revision = plan.get("revision") or {}
    previous = plan.get("proposal") if revision else None
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
    proposal["checks"] = assemble.proposal_checks(settings, proposal)
    cancel.check()
    db.update_plan(pid, status="done", stage=None, progress=1.0, error=None, proposal=proposal,
                   title=proposal.get("title_en") or plan["title"], revision=None)
    log.info("plan %s: proposal with %d clips (%s)", pid, n, style.short)


def _outline(settings: DirectorSettings, previous: dict | None, feedback: str) -> ProposalHeader:
    style = STYLES[settings.style]
    n = settings.clips
    stages = STAGES_5 if style.discussion_ending else STAGES_3
    structure = (STRUCTURE_5.format(stages=", ".join(stages), n=n, allocation=_allocation(n)) if style.discussion_ending
                 else STRUCTURE_3.format(stages=", ".join(stages)))
    system = OUTLINE_SYSTEM.format(structure=structure, language=_language_note(settings), palette=style.palette_rule,
                                   style_rules=STYLE_RULES[settings.style])
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
        if len(header.clips) >= n:
            header.clips = [ClipOutline(**{**c.model_dump(), "clip": i + 1}) for i, c in enumerate(header.clips[:n])]
            return header
        prompt += f"\n\nYour last answer had {len(header.clips)} clips; it must have exactly {n}."
    raise HighlightError(f"The AI returned {len(header.clips)} clips instead of {n}; try again")


def _storyboard(pid: str, settings: DirectorSettings, header: ProposalHeader) -> list[StoryRow]:
    style = STYLES[settings.style]
    system = STORYBOARD_SYSTEM.format(
        language=_language_note(settings), aspect=settings.aspect, composition=assemble.COMPOSITION[settings.aspect],
        devices=("use at least four relevant visual devices per clip;" if style.devices_rule else
                 "use as many devices as the joke needs, with reaction pauses;"),
        style_rules=STYLE_RULES[settings.style])
    context = json.dumps({"title": header.title_en, "music": header.music, "palette": [p.model_dump() for p in header.palette],
                          "cast": [{"role": c.role, "summary": c.summary} for c in header.cast]})
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
    return [rows[c.clip] for c in outlines]


def _revise_clip(settings: DirectorSettings, proposal: dict, clip: int, feedback: str) -> dict:
    """Redo one clip of the proposal: its outline and storyboard row."""
    style = STYLES[settings.style]
    system = STORYBOARD_SYSTEM.format(
        language=_language_note(settings), aspect=settings.aspect, composition=assemble.COMPOSITION[settings.aspect],
        devices="use at least four relevant visual devices;" if style.devices_rule else "use reaction pauses freely;",
        style_rules=STYLE_RULES[settings.style]) + (
        "\nYou revise ONE clip: return its outline (stage, purpose, English VO of about 20-25 words, location) and its "
        "storyboard row, keeping it connected to the neighbouring clips.")
    clips = proposal["clips"]
    around = [c for c in clips if abs(c["clip"] - clip) <= 1]
    prompt = (f"Video: {proposal.get('title_en')} — {proposal.get('core_message')}\nNeighbouring clips:\n"
              + "\n".join(json.dumps(c) for c in around)
              + f"\nCurrent row of clip {clip}:\n{json.dumps(proposal['rows'][clip - 1])}"
              + f"\n\nRevise clip {clip} with this feedback:\n{feedback}")
    got = _ask(system, prompt, ClipRevision, settings)
    out = json.loads(json.dumps(proposal))
    out["clips"][clip - 1] = {**got.outline.model_dump(), "clip": clip}
    out["rows"][clip - 1] = {**got.row.model_dump(), "clip": clip}
    return out


# --- Phase B ---------------------------------------------------------------------------------------


def write_prompts(pid: str) -> None:
    """The production package for the approved proposal (or just one clip, with feedback)."""
    plan = db.get_plan(pid)
    settings = DirectorSettings(**plan["settings"])
    proposal = plan["proposal"]
    revision = plan.get("revision") or {}
    only = revision.get("clip")
    existing = {c["clip"]: c for c in (plan.get("package") or {}).get("clips", [])}
    wanted = [only] if only else [c["clip"] for c in proposal["clips"]]

    runner.stage(pid, "prompts")
    details = {}
    for start in range(0, len(wanted), PROMPTS_PER_REQUEST):
        batch = wanted[start:start + PROMPTS_PER_REQUEST]
        got = _prompt_details(settings, proposal, batch, revision.get("feedback", "") if only else "")
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
    db.update_plan(pid, status="done", stage=None, progress=1.0, error=None, package=package, revision=None)


def _prompt_details(settings: DirectorSettings, proposal: dict, batch: list[int], feedback: str) -> dict[int, dict]:
    style = STYLES[settings.style]
    system = PROMPT_SYSTEM.format(style_rules=STYLE_RULES[settings.style])
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
              f"Cast: {json.dumps([c['role'] for c in proposal.get('cast') or []])}\n"
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
