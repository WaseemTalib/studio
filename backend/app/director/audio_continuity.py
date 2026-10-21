"""Deterministic voice and picture continuity rules for director prompts."""

import re

VOICE_SKILL = (
    "Use one identical off-screen narrator for the entire video. The voice starts on the first frame, continues at a "
    "steady natural pace through every timed beat and reaches the final frame without interruption. Preserve identical "
    "speaker identity, age, accent, timbre, pitch, resonance, texture, pace, loudness and recording treatment in every "
    "clip. At each boundary, the next spoken phrase follows the previous phrase immediately without a voice reset, "
    "intro, outro or breath-only gap."
)

PICTURE_SOUND_SKILL = (
    "Keep picture and sound continuous for the full clip. Every frame contains purposeful motion or a motivated held "
    "action while narration remains active. Match the previous final frame at the opening and hand the final pose, prop, "
    "travel direction and camera motion directly into the next clip. Maintain continuous music or room tone underneath "
    "the voice with no blank frames, frozen ending, fade to black, audio dropout, restart or disconnected establishing shot."
)

_GAP_WORDING = re.compile(
    r"\b(?:a\s+|an\s+)?(?:(?:brief|long|short|dramatic|deliberate|natural|occasional|awkward|comedic|dry|quiet)\s+)?"
    r"(?:silent\s+(?:beat|gap)|silence|pauses?|dead\s+air)\b",
    re.I,
)
_DROPOUT_WORDING = re.compile(
    r"\b(?:audio|music|sound|room\s+tone)\s+(?:cuts?|drops?|fades?)\s+(?:out|away|off)\b",
    re.I,
)


def without_gaps(text: str) -> str:
    """Remove creative directions that ask a generator to stop voice, music or motion."""
    cleaned = _GAP_WORDING.sub("continuous narration and motion", text or "")
    cleaned = _DROPOUT_WORDING.sub("music and room tone continue", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_proposal(proposal: dict) -> dict:
    proposal["voice_block"] = without_gaps(proposal.get("voice_block", ""))
    proposal["music"] = without_gaps(proposal.get("music", ""))
    return proposal
