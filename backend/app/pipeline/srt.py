"""Import an .srt subtitle file as a transcript.

SRT only times whole cues, so word times inside a cue are spread across it in
proportion to word length. Clip boundaries and captions stay aligned to the cue.
"""

import re

from ..schemas import Segment, Transcript, Word

TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")
ARROW = re.compile(rf"{TIME.pattern}\s*-->\s*{TIME.pattern}")
TAGS = re.compile(r"<[^>]+>|\{[^}]+\}")


class SrtError(ValueError):
    pass


def _seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_srt(text: str, language: str | None) -> Transcript:
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    segments: list[Segment] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        timing = next((i for i, line in enumerate(lines) if ARROW.search(line)), None)
        if timing is None:
            continue
        g = ARROW.search(lines[timing]).groups()
        start, end = _seconds(*g[:4]), _seconds(*g[4:])
        body = TAGS.sub("", " ".join(lines[timing + 1:])).split()
        if not body or end <= start:
            continue
        total = sum(len(w) + 1 for w in body)
        words, t = [], start
        for w in body:
            span = (end - start) * (len(w) + 1) / total
            words.append(Word(start=round(t, 3), end=round(t + span * 0.92, 3), text=w))
            t += span
        segments.append(Segment(start=start, end=end, text=" ".join(body), words=words))
    if not segments:
        raise SrtError("No subtitle cues found in the .srt file")
    segments.sort(key=lambda s: s.start)
    return Transcript(language=language or "unknown", duration=segments[-1].end, segments=segments)
