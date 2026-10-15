"""Split a word-level transcript into sentences, the unit clips are cut on."""

import re
from dataclasses import dataclass

from ..schemas import Transcript, Word

SENTENCE_END = re.compile(r"[.?!…][\"'”’)\]]*$")
PAUSE_BREAK = 0.9        # a silence this long ends a sentence even without punctuation
MAX_WORDS = 45           # very long unpunctuated runs are split at the last comma or here


@dataclass
class Sentence:
    index: int
    words: list[Word]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def split_sentences(transcript: Transcript) -> list[Sentence]:
    return split_words([w for s in transcript.segments for w in s.words])


def split_words(words: list[Word]) -> list[Sentence]:
    groups: list[list[Word]] = []
    current: list[Word] = []
    for i, w in enumerate(words):
        current.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if nxt is None or SENTENCE_END.search(w.text) or nxt.start - w.end >= PAUSE_BREAK:
            groups.append(current)
            current = []
        elif len(current) >= MAX_WORDS:
            comma = max((j for j, cw in enumerate(current[:-1]) if cw.text.endswith(",")), default=None)
            if comma is not None and comma >= MAX_WORDS // 3:
                groups.append(current[: comma + 1])
                current = current[comma + 1:]
            else:
                groups.append(current)
                current = []
    return [Sentence(i, g) for i, g in enumerate(groups)]


def cut_points(sentences: list[Sentence], first: int, last: int, duration: float) -> tuple[float, float]:
    """Cut slightly outside the spoken words without bleeding into neighbouring speech."""
    start = sentences[first].start - 0.15
    if first > 0:
        start = max(start, sentences[first - 1].end + 0.02)
    end = sentences[last].end + 0.35
    if last + 1 < len(sentences):
        end = min(end, sentences[last + 1].start - 0.02)
    return round(max(0.0, start), 3), round(min(duration, end), 3)
