"""Assembling standalone clip prompts, checking them, and exporting the plan.

Each prompt follows the skill's production order: output spec → environment → character lock → palette →
composition → first frame → timed beats → exact audio-only VO → frozen voice block → music lock and SFX → final
frame → negatives. The frozen blocks are inserted verbatim by code, so they are identical in every clip.
"""

import re

from .models import DirectorSettings
from .styles import (AUDIO_ONLY, COMPOSITION, DENSITY, NO_TEXT, STYLES, UNIVERSAL_NEGATIVES, character_anchor,
                     music_lock)

BEAT_TIMES = ("[0–3s]", "[3–7s]", "[7–10s]")
TECH_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\s*\(|\bhsla?\s*\(|\bpantone\b", re.I)


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[\w']+", text.lower()))


def split_vo(vo: str, parts: list[str]) -> list[str]:
    """The AI's three VO parts if they are exactly the clip's VO, else an even split by words."""
    if len(parts) == 3 and _norm(" ".join(parts)) == _norm(vo):
        return [p.strip() for p in parts]
    words = vo.split()
    a, b = round(len(words) * 0.3), round(len(words) * 0.7)
    return [" ".join(words[:a]), " ".join(words[a:b]), " ".join(words[b:])]


def _characters(settings: DirectorSettings, proposal: dict, n: int) -> str:
    cast = {c["role"]: c["anchor"] for c in proposal.get("cast") or []}
    roles = [r for r in proposal["rows"][n - 1].get("roles") or [] if r in cast] or list(cast)
    if settings.style == "3":
        cards = " ".join(f"{role}: {cast[role]}" for role in roles)
        return f"{character_anchor('3', n - 1, False)} Characters in this clip (only these): {cards}".strip()
    if settings.style == "4" and cast:
        return " ".join(f"{role}: {cast[role]}" for role in roles)
    return character_anchor(settings.style, n - 1, settings.faceless)


def clip_prompt(settings: DirectorSettings, proposal: dict, n: int, d: dict) -> dict:
    style = STYLES[settings.style]
    vo = proposal["clips"][n - 1]["vo"].strip()
    beats = d.get("beats") or []
    parts = split_vo(vo, [b.get("vo", "") for b in beats])
    actions = [(beats[i].get("action") if i < len(beats) else "") or "" for i in range(3)]
    environment = style.environment if not style.per_clip_location else f"{style.environment} Setting: {d['environment']}"
    if not style.per_clip_location and d.get("environment"):
        environment += f" {d['environment']}"
    lines = [
        f"Output: approximately ten-second clip, {settings.aspect} aspect ratio, 720p target, 24 FPS, synchronized audio.",
        f"Environment: {environment}",
        f"Character: {_characters(settings, proposal, n)}",
        f"Palette: {d['palette']}",
        f"Composition: {COMPOSITION[settings.aspect]} {d['composition']}",
        f"First frame: {d['first_frame']}",
        "Timed synchronized visual beats:",
        *[f"- {BEAT_TIMES[i]} (VO: \"{parts[i]}\"): {actions[i]}" for i in range(3)],
        f"{DENSITY}" if style.devices_rule else "Clear actions, expressive reactions and motivated cuts; leave room for "
                                                "the reaction beat.",
        f"Dialogue: {AUDIO_ONLY} Speak exactly this narration once, without adding, omitting, paraphrasing, repeating, "
        f"reordering, captioning, subtitling or visually transcribing any words: \"{vo}\"",
        f"Voice: {proposal['voice_block']}",
        f"{music_lock(n - 1, proposal['music'])} Voice-first mix. SFX: {d['sfx']}",
        f"Final frame: {d['final_frame']}",
        f"{NO_TEXT} {UNIVERSAL_NEGATIVES} {style.negatives}",
    ]
    text = "\n".join(lines)
    return {"clip": n, "title": d.get("title") or f"Clip {n}", "prompt": text, "vo": vo, "stitch": d.get("stitch", ""),
            "final_frame": d.get("final_frame", ""),
            # whether the AI's three VO parts were exact (otherwise the narration was split evenly by words)
            "vo_split_by_ai": len(beats) == 3 and _norm(" ".join(b.get("vo", "") for b in beats)) == _norm(vo)}


def package(settings: DirectorSettings, proposal: dict, clips: list[dict]) -> dict:
    style = STYLES[settings.style]
    palette = ", ".join(f"{p['color']} ({p['meaning']})" for p in proposal.get("palette") or [])
    cast = "; ".join(f"{c['role']}: {c['anchor']}" for c in proposal.get("cast") or [])
    global_block = "\n".join(x for x in [
        f"Aspect ratio: {settings.aspect} · Duration: {settings.seconds}s ({settings.clips} clips of ~10s) · "
        f"Style: {style.name}",
        f"Character anchor: {cast or character_anchor(settings.style, 0, settings.faceless)}",
        f"Palette / environment: {palette or '-'}; {style.environment}",
        f"Narrator: {proposal['voice_block']}",
        f"Music: {proposal['music']} (clip 1 establishes it; every later clip continues it identically)",
        "Continuity: every clip repeats ratio, style, character, palette, voice, music and negative locks; each ending "
        "hands an action, prop, direction or a named location/time cut to the next opening.",
    ] if x)
    stitching = [f"Clip {c['clip']} → {c['clip'] + 1}: {c['stitch']}" for c in clips[:-1]]
    if clips:
        stitching.append(f"Clip {clips[-1]['clip']}: ends the video — {clips[-1]['final_frame']}")
    audio_note = (
        "1. Every prompt repeats the same complete voice block; compare the generated clips by listening (timbre, "
        "register, accent, pace, recording sound), not by screenshots. 2. If one clip sounds like a different person, "
        "retest only that clip with everything else unchanged. 3. For an exact match, record one continuous narration "
        "(e.g. in Voice Studio) and lay it over the assembled clips, with a short audio crossfade at each join. "
        "Music: clip 1 sets the theme and every later clip asks for the identical score; trim to usable frames and "
        "crossfade audio at cuts.")
    pkg = {"global_block": global_block, "clips": clips, "stitching": stitching, "audio_note": audio_note,
           "overlays": proposal.get("overlays") or []}
    pkg["checks"] = package_checks(settings, proposal, pkg)
    return pkg


# --- Checks -----------------------------------------------------------------------------------------


def _check(ok: bool, text: str) -> dict:
    return {"ok": bool(ok), "text": text}


def proposal_checks(settings: DirectorSettings, p: dict) -> list[dict]:
    style = STYLES[settings.style]
    n = settings.clips
    words = [len(c["vo"].split()) for c in p["clips"]]
    low = 8 if settings.style == "3" else 15
    out = [
        _check(len(p["clips"]) == n and len(p["rows"]) == n, f"Exactly {n} clips for {settings.seconds}s"),
        _check(n * low <= sum(words) <= n * 28,
               f"Narration length: {sum(words)} words (aim ~{n * 20}-{n * 25})"),
        _check(all(low <= w <= 30 for w in words),
               "Every clip's VO fits ten seconds" + ("" if all(low <= w <= 30 for w in words) else
                                                     f" (check clips {[i + 1 for i, w in enumerate(words) if not low <= w <= 30]})")),
        _check(all(len(r["beats"]) == 3 for r in p["rows"]), "Every clip has three timed beats"),
        _check(all(r["end_state"].strip() for r in p["rows"]), "Every clip names the state the next clip inherits"),
        _check(bool(p.get("voice_block", "").strip()), "One complete voice profile for the whole video"),
        _check(not TECH_COLOR.search(str({k: v for k, v in p.items() if k != "checks"})),
               "No technical color notation (hex, RGB, Pantone)"),
    ]
    if style.devices_rule:
        out.append(_check(all(len(r["devices"]) >= 4 for r in p["rows"]), "At least four visual devices per clip"))
    if style.discussion_ending:
        out.append(_check("?" in p["clips"][-1]["vo"], "The last clip ends with a discussion question"))
    if settings.style in ("1-light", "1-dark"):
        out.append(_check(len(p.get("palette") or []) <= 3, "No more than three accent colors"))
    if settings.style == "3":
        out.append(_check(bool(p.get("cast")), "Cast cards for the story's characters"))
    if settings.style == "4" and n > 1:
        locations = [c["location"].strip().lower() for c in p["clips"]]
        out.append(_check(len(set(locations)) >= min(n, max(2, n - 1)), "A different setting for each new clip"))
    return out


def package_checks(settings: DirectorSettings, proposal: dict, pkg: dict) -> list[dict]:
    clips = pkg["clips"]
    voice = proposal["voice_block"]
    return [
        _check(len(clips) == settings.clips, f"Exactly {settings.clips} standalone prompts"),
        _check(all(voice in c["prompt"] for c in clips), "Identical complete voice block in every prompt"),
        _check(all(f"\"{c['vo']}\"" in c["prompt"] for c in clips), "Narration matches the approved proposal exactly"),
        _check(all(c["prompt"].count("[0–3s]") == 1 and "[7–10s]" in c["prompt"] for c in clips),
               "Three timed beats in every prompt"),
        _check(not any(TECH_COLOR.search(c["prompt"]) for c in clips), "No technical color notation in prompts"),
        _check(all("no speech bubbles" in c["prompt"] for c in clips), "Narration is audio-only in every prompt"),
        _check(all(c["stitch"].strip() for c in clips[:-1]), "Every cut has a stitching note"),
    ]


# --- Exports ---------------------------------------------------------------------------------------


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt(proposal: dict) -> str:
    """English subtitles for post-production: each clip's VO over its ten seconds, a sentence per cue."""
    cues, i = [], 1
    for c in proposal["clips"]:
        start = (c["clip"] - 1) * 10.0
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", c["vo"].strip()) if s] or [c["vo"]]
        total = sum(len(s) for s in sentences) or 1
        t = start
        for s in sentences:
            d = 10.0 * len(s) / total
            cues.append(f"{i}\n{_ts(t)} --> {_ts(min(t + d, start + 10.0))}\n{s}\n")
            t += d
            i += 1
    return "\n".join(cues)


def markdown(plan: dict) -> str:
    s = DirectorSettings(**plan["settings"])
    p = plan.get("proposal") or {}
    out = [f"# {p.get('title_en') or plan['title']}", ""]
    if p.get("title_ref"):
        out += [f"*{p['title_ref']}*", ""]
    out += [f"{s.aspect} · {s.seconds}s · {STYLES[s.style].name}", "", "## Director's proposal", "",
            f"**Core message:** {p.get('core_message', '')}", "", f"**Hook:** {p.get('hook', '')}", "",
            f"**Voice:** {p.get('voice_summary', '')}", "", f"**Music:** {p.get('bgm_direction', '')}", "",
            f"**Arc:** {p.get('emotional_arc', '')}", ""]
    for c in p.get("cast") or []:
        out.append(f"- **{c['role']}** — {c['summary']}")
    out += ["", "| Clip | Stage | Scene | Camera | VO | Translation | BGM/SFX |", "|---|---|---|---|---|---|---|"]
    for c, r in zip(p.get("clips", []), p.get("rows", [])):
        cell = lambda x: str(x).replace("|", "/").replace("\n", " ")
        out.append(f"| {c['clip']} ({(c['clip'] - 1) * 10}-{c['clip'] * 10}s) | {cell(c['stage'])} | {cell(r['scene'])}"
                   f" — {cell(' / '.join(r['beats']))} | {cell(r['camera'])} | {cell(c['vo'])} | "
                   f"{cell(r['vo_translation'])} | {cell(r['bgm_sfx'])} |")
    pkg = plan.get("package")
    if pkg:
        out += ["", "## Production package", "", "### Global continuity block", "", pkg["global_block"], ""]
        for c in pkg["clips"]:
            out += [f"### Clip {c['clip']} — {c['title']}", "", "```text", c["prompt"], "```", ""]
        out += ["### Stitching guide", "", *[f"- {x}" for x in pkg["stitching"]], "",
                "### Voice and music continuity", "", pkg["audio_note"], ""]
        if pkg.get("overlays"):
            out += ["### Post-production overlays", "",
                    *[f"- Clip {o['clip']}: “{o['text']}” ({o['placement']})" for o in pkg["overlays"]], ""]
    out += ["---", "Planned with Stickman Director, based on Stickman Video Director "
            "(https://github.com/kaomei/stickman-video-director, MIT License)."]
    return "\n".join(out)
