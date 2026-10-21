"""Character identity locks shared by the real-world and stickman directors."""

import re

from .models import DirectorSettings
from .styles import character_anchor

VERSION = 1

STICKMAN_SKILL = (
    "Treat every character anchor as an immutable model sheet for the whole video. Preserve head and body proportions, "
    "line weight, limb count, face grammar, clothing silhouette and colors, footwear, accessories and owned props. "
    "Pose, expression, framing and lighting may change; character design may not. Never duplicate, merge, substitute, "
    "age, recolor or restyle a character, and never leave ghost limbs from an earlier pose."
)

REAL_WORLD_SKILL = (
    "Treat every person anchor as an immutable identity record for the whole video. Preserve facial geometry, skin tone, "
    "apparent age, height and build, hairline and hairstyle, wardrobe layers and colors, footwear, accessories, handedness "
    "and owned props. Pose, expression, framing and lighting may change; identity may not. Never duplicate, merge, "
    "substitute, age, beautify, recolor or restyle a person."
)

REFERENCE_WORKFLOW = (
    "Generate and approve one neutral character reference sheet before the scene clips. Attach that same reference image "
    "to every clip generation when the video tool supports image references. Otherwise keep the complete character bible "
    "and consistency skill below unchanged in every prompt."
)


def role_key(role: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", role.lower()))


def cast(proposal: dict) -> list[dict]:
    return [card for card in proposal.get("cast") or [] if card.get("role") and card.get("anchor")]


def visible_roles(proposal: dict, clip: int) -> list[str]:
    cards = cast(proposal)
    by_key = {role_key(card["role"]): card["role"] for card in cards}
    rows = proposal.get("rows") or []
    row = rows[clip - 1] if clip <= len(rows) else {}
    visible = []
    for role in row.get("roles") or []:
        canonical = by_key.get(role_key(role))
        if canonical and canonical not in visible:
            visible.append(canonical)
    return visible


def normalize_cast_and_roles(proposal: dict) -> None:
    """Own cast identity normalization once: unique cards and canonical row role IDs."""
    unique = []
    seen = set()
    for card in proposal.get("cast") or []:
        key = role_key(str(card.get("role") or ""))
        if key and key not in seen:
            unique.append(card)
            seen.add(key)
    proposal["cast"] = unique
    canonical = {role_key(card["role"]): card["role"] for card in unique}
    for row in proposal.get("rows") or []:
        roles = []
        for role in row.get("roles") or []:
            name = canonical.get(role_key(str(role)))
            if name and name not in roles:
                roles.append(name)
        row["roles"] = roles


def anchors(settings: DirectorSettings, proposal: dict) -> list[str]:
    cards = cast(proposal)
    if settings.mode == "generic" or (settings.style in ("3", "4") and cards):
        return [card["anchor"].strip() for card in cards]
    return [character_anchor(settings.style, 0, settings.faceless)]


def character_bible(settings: DirectorSettings, proposal: dict, clip: int) -> str:
    cards = cast(proposal)
    use_cast = settings.mode == "generic" or (settings.style in ("3", "4") and cards)
    if not use_cast:
        return f"Default protagonist: {character_anchor(settings.style, 0, settings.faceless)}"
    if not cards:
        return "No recurring on-screen person is required; do not invent a recurring presenter or background clone."
    roster = " ".join(f"{card['role']}: {card['anchor']}" for card in cards)
    visible = visible_roles(proposal, clip)
    absent = [card["role"] for card in cards if card["role"] not in visible]
    visibility = (f"Render only these roles in this clip: {', '.join(visible)}." if visible else
                  "Render none of the recurring cast in this clip.")
    if absent:
        visibility += f" Do not render absent roles: {', '.join(absent)}."
    return f"Locked video cast: {roster} {visibility} Use role names exactly as written."


def skill(settings: DirectorSettings) -> str:
    return REAL_WORLD_SKILL if settings.mode == "generic" else STICKMAN_SKILL


def reference_prompt(settings: DirectorSettings, proposal: dict) -> str:
    identity = " ".join(f"{card['role']}: {card['anchor']}" for card in cast(proposal))
    if not identity and settings.mode == "generic":
        return ""
    identity = identity or f"Default protagonist: {character_anchor(settings.style, 0, settings.faceless)}"
    medium = "photorealistic casting reference" if settings.mode == "generic" else "clean animation model sheet"
    return (
        f"Create one {medium} for the following locked character bible. {identity} Show each character once in a neutral "
        "full-body front view, three-quarter view, side profile and close facial or head view on a plain uncluttered "
        "background. Keep proportions, face or head design, wardrobe, colors and accessories identical across all views. "
        "No scene action, text, labels, logos, duplicate characters or alternate outfits."
    )
