"""Pick short-form highlights from a transcript with an LLM (Groq or Claude).

Two passes:
1. Find candidates. The transcript is shown as numbered sentences and the model
   answers with sentence ranges, so every cut lands on a sentence boundary.
2. Review boundaries. Each candidate is shown with the sentences around it and the
   model moves its start/end so it opens on a new thought and ends where the topic
   closes, or rejects it.
"""

import json
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..config import CLAUDE_FAST_MODEL, CLAUDE_MODEL, GROQ_FAST_MODEL, GROQ_LLM_MODEL, GROQ_TPM, LLM_PROVIDER
from ..presets import GENRES
from ..schemas import Candidate, CandidateSet, ProjectOptions, Review, ReviewSet, Transcript
from . import cancel
from .sentences import Sentence, cut_points, split_sentences

T = TypeVar("T", bound=BaseModel)

log = logging.getLogger(__name__)

REVIEW_CONTEXT = 5  # sentences of context shown on each side during review

FIND_SYSTEM = """You are a senior short-form video editor. You find the moments in long videos \
that work as standalone vertical clips for TikTok, YouTube Shorts and Reels.

A clip is only good if ALL of these hold:
1. It opens on the first sentence of a new thought. Never start on a sentence that depends on what came \
before: one that begins with "and", "so", "but", "because", "that's why", "which", or uses "he/she/it/this/that" \
for something not yet introduced in the clip.
2. The first 3 seconds hook the viewer: a bold claim, a question, a surprising fact, or tension.
3. It is self-contained: someone with no context understands it.
4. It ends where the topic closes: after the answer, conclusion, punchline or story resolution. Never end \
mid-list, mid-argument, on a question that is answered later, or on a sentence that introduces something new.
5. It covers ONE topic. If the speaker moves to another subject, end before that.

Clips are chosen by sentence numbers (S#). Each sentence shows its start and end time in seconds; \
make sure the clip's total duration (end of last sentence minus start of first) fits the requested length. \
Clips must not overlap. Score honestly: most clips land between 40 and 85; reserve 90+ for exceptional moments."""

REVIEW_SYSTEM = """You are a meticulous video editor checking clip boundaries before publishing.

For each clip you see the clip's sentences (marked >>) plus the sentences just before and after it.
- Move start_sentence earlier if the clip needs earlier context (e.g. the question being answered, \
the setup of a joke, who "he" is). Move it later if the opening sentences are filler or a leftover \
from the previous topic.
- Move end_sentence later if the thought, answer, list or story is not finished. Move it earlier if the \
clip runs into a new topic or trailing filler.
- Only use sentence numbers that are shown. Respect the length limit you are given.
- Set keep to false if the clip cannot stand alone even after adjusting.
Return one review per clip, using its clip number."""


class HighlightError(RuntimeError):
    pass


class RequestTooLarge(HighlightError):
    """The provider rejected one request as too large; a smaller request may succeed."""


class DailyLimitReached(HighlightError):
    """Groq's per-day allowance for one model is used up. Each model has its own allowance."""


@dataclass
class Highlight:
    start: float
    end: float
    topic: str
    title: str
    hook: str
    score: int
    reason: str
    hashtags: list[str]


# Share of the highlight step spent finding candidates; the rest is boundary review.
FIND_SHARE = 0.8


def pick_highlights(transcript: Transcript, options: ProjectOptions,
                    on_progress: Callable[[float], None] = lambda f: None) -> list[Highlight]:
    sentences = split_sentences(transcript)
    lo = options.range_start or 0.0
    hi = options.range_end or transcript.duration
    window = [s for s in sentences if s.start >= lo and s.end <= hi]
    if not window:
        raise HighlightError("No speech found in the selected part of the video")

    candidates = _find(window, transcript, options, lambda f: on_progress(FIND_SHARE * f))
    candidates = _normalize(candidates, sentences, window, options)
    if not candidates:
        raise HighlightError("No clips matched the requested length limits")
    candidates = _review(candidates, sentences, window, options,
                         lambda f: on_progress(FIND_SHARE + (1 - FIND_SHARE) * f))
    if not candidates:
        raise HighlightError("No clips passed the boundary review")

    candidates.sort(key=lambda c: c.score, reverse=True)
    result = []
    for c in candidates[: options.clip_count]:
        start, end = cut_points(sentences, c.start_sentence, c.end_sentence, transcript.duration)
        result.append(Highlight(start, end, c.topic, c.title, c.hook, max(0, min(100, c.score)), c.reason,
                                [h.lstrip("#") for h in c.hashtags][:5]))
    return result


# --- Pass 1: find ---------------------------------------------------------------


def _find(window: list[Sentence], transcript: Transcript, options: ProjectOptions,
          on_progress: Callable[[float], None]) -> list[Candidate]:
    # Ask for spares: some candidates are dropped by length checks or review.
    wanted = min(options.clip_count + max(2, options.clip_count // 2), 30)
    topic_rule = ""
    if options.topics:
        topic_rule = (
            "\nOnly pick moments that are substantially about at least one of these topics: "
            + "; ".join(options.topics)
            + ". Return fewer clips rather than off-topic ones.\n"
        )
    instructions = ""
    if options.instructions.strip():
        instructions = (
            "\nThe creator gave these instructions about which moments to include. Follow them when choosing "
            f"clips, but never break the boundary rules:\n<instructions>\n{options.instructions.strip()}\n</instructions>\n"
        )

    def prompt(part: list[Sentence], n: int) -> str:
        lines = "\n".join(_line(s) for s in part)
        return (
            f"Content type guidance: {GENRES[options.genre].guidance}\n"
            f"Language: {transcript.language}.\n"
            f"Find up to {n} clips, each between {options.min_seconds} and {options.max_seconds} seconds long, "
            f"ordered best first.{topic_rule}{instructions}\n"
            f"<transcript>\n{lines}\n</transcript>"
        )

    def ask(part: list[Sentence]) -> list[Candidate]:
        # Each part gets its share of the clips, plus one spare; _normalize drops overlaps later.
        n = min(wanted, math.ceil(wanted * len(part) / len(window)) + 1)
        return _structured(FIND_SYSTEM, prompt(part, n), CandidateSet, options.clip_model).clips

    budget = _line_budget(FIND_SYSTEM, prompt([], wanted), CandidateSet)
    parts = _split_window(window, budget, options.max_seconds)
    halve = lambda part: _halve_window(part, options.max_seconds)
    size = lambda part: sum(len(_line(s)) for s in part)
    return [c for clips in _ask_parts(parts, ask, halve, size, on_progress) for c in clips]


def _line(s: Sentence) -> str:
    return f"S{s.index} [{s.start:.1f}-{s.end:.1f}] {s.text}"


def _split_window(window: list[Sentence], budget: int | None, max_seconds: float) -> list[list[Sentence]]:
    """Split the transcript into parts whose lines fit the token budget. Consecutive parts
    overlap by one clip length so a moment that straddles a split is still whole in one part."""
    if budget is None:
        return [window]
    parts: list[list[Sentence]] = []
    start = 0
    while start < len(window):
        end, used = start, 0
        while end < len(window) and (end == start or used + _tokens(_line(window[end])) <= budget):
            used += _tokens(_line(window[end]))
            end += 1
        parts.append(window[start:end])
        if end == len(window):
            break
        overlap_from = window[end - 1].end - max_seconds
        nxt = end
        while nxt - 1 > start + 1 and window[nxt - 1].start >= overlap_from:
            nxt -= 1
        start = nxt
    return parts


def _halve_window(part: list[Sentence], max_seconds: float) -> tuple[list[Sentence], list[Sentence]] | None:
    """Two halves of a transcript part, overlapping by one clip length like _split_window."""
    if len(part) < 4:
        return None
    mid = len(part) // 2
    start = mid
    while start > 1 and part[start - 1].start >= part[mid - 1].end - max_seconds:
        start -= 1
    return part[:mid], part[start:]


P = TypeVar("P")
R = TypeVar("R")


def _ask_parts(parts: list[P], ask: Callable[[P], R], halve: Callable[[P], tuple[P, P] | None],
               size: Callable[[P], int], on_progress: Callable[[float], None]) -> list[R]:
    """Ask about each part in order. Groq's per-minute check counts more than the prompt text,
    so the size estimate can fall short. A part it rejects as too large is halved and asked
    again, and later parts are cut to that size up front instead of being rejected one by one."""
    queue = list(parts)
    results: list[R] = []
    shown = 0.0
    cap: int | None = None  # largest size worth sending, learned from the first rejection
    while queue:
        part = queue.pop(0)
        if cap is not None and size(part) > cap and (halves := halve(part)) is not None:
            queue[:0] = halves
            continue
        try:
            results.append(ask(part))
        except RequestTooLarge:
            halves = halve(part)
            if halves is None:
                raise
            log.info("request too large for the Groq per-minute limit; sending smaller parts")
            # Room for the halves' overlap, while every part about this size gets split.
            cap = min(cap or size(part), size(part) * 3 // 4)
            queue[:0] = halves
            continue
        shown = max(shown, len(results) / (len(results) + len(queue)))  # splitting must not move it back
        on_progress(shown)
    return results


# --- Pass 2: review -------------------------------------------------------------


def _review(candidates: list[Candidate], sentences: list[Sentence], window: list[Sentence],
            options: ProjectOptions, on_progress: Callable[[float], None]) -> list[Candidate]:
    lo, hi = window[0].index, window[-1].index
    blocks = []
    for n, c in enumerate(candidates):
        first, last = max(lo, c.start_sentence - REVIEW_CONTEXT), min(hi, c.end_sentence + REVIEW_CONTEXT)
        rows = [
            f"{'>>' if c.start_sentence <= i <= c.end_sentence else '  '} S{i} "
            f"[{sentences[i].start:.1f}-{sentences[i].end:.1f}] {sentences[i].text}"
            for i in range(first, last + 1)
        ]
        blocks.append(f"<clip number=\"{n}\" title=\"{c.title}\" topic=\"{c.topic}\">\n" + "\n".join(rows) + "\n</clip>")
    header = f"Each clip must last between {options.min_seconds} and {options.max_seconds} seconds.\n\n"

    budget = _line_budget(REVIEW_SYSTEM, header, ReviewSet)
    batches: list[list[str]] = [[]]
    used = 0
    for block in blocks:
        if budget is not None and batches[-1] and used + _tokens(block) > budget:
            batches.append([])
            used = 0
        batches[-1].append(block)
        used += _tokens(block)

    def ask(batch: list[str]) -> list[Review]:
        try:
            return _structured(REVIEW_SYSTEM, header + "\n\n".join(batch), ReviewSet, options.clip_model).reviews
        except RequestTooLarge:
            if len(batch) > 1:
                raise  # _ask_parts halves the batch
            log.warning("boundary review skipped for 1 clip: too large for the Groq per-minute limit")
            return []
        except HighlightError as e:
            # The candidates already sit on sentence boundaries; ship them unreviewed rather than fail.
            log.warning("boundary review skipped for %d clips: %s", len(batch), e)
            return []

    halve = lambda batch: (batch[:len(batch) // 2], batch[len(batch) // 2:]) if len(batch) > 1 else None
    size = lambda batch: sum(len(b) for b in batch)
    reviews = [r for rs in _ask_parts(batches, ask, halve, size, on_progress) for r in rs]

    by_clip = {r.clip: r for r in reviews if 0 <= r.clip < len(candidates)}
    revised = []
    for n, c in enumerate(candidates):
        r = by_clip.get(n)
        if r is None:
            revised.append(c)
        elif r.keep:
            revised.append(c.model_copy(update={"start_sentence": r.start_sentence, "end_sentence": r.end_sentence}))
    return _normalize(revised, sentences, window, options, trim=False)


# --- Shared ---------------------------------------------------------------------


def _normalize(candidates: list[Candidate], sentences: list[Sentence], window: list[Sentence],
               options: ProjectOptions, trim: bool = True) -> list[Candidate]:
    """Clamp to the window, enforce length on sentence boundaries, drop overlaps (best score wins).

    Before review, over-long clips are trimmed by whole sentences and review can restore a
    proper ending. After review, trimming would undo that, so over-long clips are dropped.
    """
    lo, hi = window[0].index, window[-1].index
    ok: list[Candidate] = []
    for c in sorted(candidates, key=lambda c: c.score, reverse=True):
        first = max(lo, min(c.start_sentence, c.end_sentence))
        last = min(hi, max(c.start_sentence, c.end_sentence))
        if first > hi or last < lo:
            continue
        # Too long: drop whole sentences from the end, never cut mid-sentence.
        while trim and last > first and sentences[last].end - sentences[first].start > options.max_seconds:
            last -= 1
        length = sentences[last].end - sentences[first].start
        if length > options.max_seconds * 1.1 or length < options.min_seconds * 0.8:
            continue
        if any(first <= k.end_sentence and last >= k.start_sentence for k in ok):
            continue
        ok.append(c.model_copy(update={"start_sentence": first, "end_sentence": last}))
    return ok


def _structured(system: str, prompt: str, schema: type[T], clip_model: str = "quality") -> T:
    fast = clip_model == "fast"
    if LLM_PROVIDER == "groq":
        return _ask_groq_any(system, prompt, schema,
                             [GROQ_FAST_MODEL, GROQ_LLM_MODEL] if fast else [GROQ_LLM_MODEL, GROQ_FAST_MODEL])
    if LLM_PROVIDER == "anthropic":
        return _ask_claude(system, prompt, schema, CLAUDE_FAST_MODEL if fast else CLAUDE_MODEL)
    raise HighlightError(f"Unknown SHORTCUT_LLM_PROVIDER {LLM_PROVIDER!r} (use 'groq' or 'anthropic')")


def _tokens(text: str) -> int:
    # No gpt-oss tokenizer here; ~3 characters per token overestimates for timestamped transcripts.
    return len(text) // 3 + 1


def _line_budget(system: str, prompt_header: str, schema: type[BaseModel]) -> int | None:
    """Tokens left for transcript lines in one request, or None when the provider needs no splitting.

    Half of Groq's per-minute limit goes to the prompt; the rest is left for the model's
    reasoning and answer, which count against the same limit. Groq's check also adds an
    overhead this estimate cannot see, so _ask_parts halves parts it rejects."""
    if LLM_PROVIDER != "groq" or GROQ_TPM <= 0:
        return None
    # The JSON-mode fallback repeats the schema in the system prompt.
    fixed = _tokens(system) + _tokens(prompt_header) + _tokens(json.dumps(schema.model_json_schema()))
    return max(GROQ_TPM // 2 - fixed, 500)


# Groq's default max_completion_tokens is 1024. gpt-oss reasons before answering, so that
# budget can be spent before any JSON is written, which Groq reports as json_validate_failed.
GROQ_MAX_COMPLETION_TOKENS = 32768


def _groq_error_code(e: Exception) -> str | None:
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict):
            return err.get("code")
    return None


# Models whose daily allowance ran out, and when. The allowance is a rolling 24-hour window
# that frees up gradually, so a model is tried again after a while rather than never.
_exhausted: dict[str, float] = {}
EXHAUSTED_RECHECK_SECONDS = 30 * 60


def _ask_groq_any(system: str, prompt: str, schema: type[T], models: list[str]) -> T:
    """Ask the first model whose daily allowance is not used up; later ones are fallbacks."""
    models = list(dict.fromkeys(models))
    for model in models:
        if time.time() - _exhausted.get(model, float("-inf")) < EXHAUSTED_RECHECK_SECONDS:
            continue
        try:
            return _ask_groq(system, prompt, schema, model)
        except DailyLimitReached:
            _exhausted[model] = time.time()
            if model != models[-1]:
                log.warning("Groq daily token limit reached for %s; using another Groq model", model)
    raise DailyLimitReached(
        f"Groq's daily token limit is used up for {' and '.join(models)}. It frees up gradually over "
        "24 hours, so retry later, upgrade your Groq tier, or set SHORTCUT_LLM_PROVIDER=anthropic"
    )


def _ask_groq(system: str, prompt: str, schema: type[T], model: str) -> T:
    """Strict JSON-schema mode first; if Groq can't produce valid JSON that way, retry in
    plain JSON mode with the schema in the prompt and validate locally."""
    import groq

    client = groq.Groq(max_retries=0)  # rate limits are retried by _groq_create, which tells them apart
    strict = {"type": "json_schema",
              "json_schema": {"name": schema.__name__, "strict": True, "schema": schema.model_json_schema()}}
    loose_system = (f"{system}\n\nRespond with only a JSON object matching this JSON Schema:\n"
                    f"{json.dumps(schema.model_json_schema())}")
    attempts = [(system, strict), (loose_system, {"type": "json_object"})]

    last_problem = ""
    for attempt_system, response_format in attempts:
        try:
            response = _groq_create(
                client,
                model=model,
                messages=[{"role": "system", "content": attempt_system}, {"role": "user", "content": prompt}],
                response_format=response_format,
                reasoning_effort="medium",
                max_completion_tokens=GROQ_MAX_COMPLETION_TOKENS,
            )
        except groq.AuthenticationError as e:
            raise HighlightError("Groq API key is missing or invalid (set GROQ_API_KEY)") from e
        except groq.RateLimitError as e:
            if _is_daily_limit(e):
                raise DailyLimitReached(f"Groq daily token limit reached for {model}") from e
            raise HighlightError("Groq rate limit reached; retry later") from e
        except groq.BadRequestError as e:
            if _groq_error_code(e) == "json_validate_failed":
                last_problem = "Groq could not produce valid JSON"
                continue
            raise HighlightError(f"Groq API error {e.status_code}: {e.message}") from e
        except groq.APIStatusError as e:
            if e.status_code == 413:
                raise RequestTooLarge(
                    f"Request is larger than your Groq tier allows per minute ({e.message}). "
                    "Set SHORTCUT_GROQ_TPM to your tier's tokens-per-minute limit, or lower it to send smaller requests"
                ) from e
            raise HighlightError(f"Groq API error {e.status_code}: {e.message}") from e
        except groq.APIConnectionError as e:
            raise HighlightError("Could not reach the Groq API") from e

        choice = response.choices[0]
        try:
            return schema.model_validate(json.loads(choice.message.content or ""))
        except (json.JSONDecodeError, ValidationError):
            last_problem = f"Groq returned an unusable answer (finish_reason={choice.finish_reason})"
    raise HighlightError(last_problem)


GROQ_RATE_LIMIT_RETRIES = 6


def _is_daily_limit(e: Exception) -> bool:
    """Groq names the limit that was hit: "tokens per day (TPD)" or "requests per day (RPD)"."""
    body = getattr(e, "body", None)
    err = body.get("error", body) if isinstance(body, dict) else None
    message = err.get("message", "") if isinstance(err, dict) else str(e)
    return "per day" in message


def _groq_create(client, **request):
    """Split transcripts send several requests in a row, which can exceed the per-minute
    token limit; wait for the window Groq names in retry-after and send again. A per-day
    limit is not waited out: its retry-after only says when this one request would fit."""
    import groq

    for attempt in range(GROQ_RATE_LIMIT_RETRIES + 1):
        try:
            return client.chat.completions.create(**request)
        except groq.RateLimitError as e:
            if attempt == GROQ_RATE_LIMIT_RETRIES or _is_daily_limit(e):
                raise
            try:
                wait = float(e.response.headers.get("retry-after", ""))
            except ValueError:
                wait = 15.0
            wait = min(max(wait, 1.0), 65.0)
            log.info("Groq rate limit reached; waiting %.0fs", wait)
            cancel.sleep(wait)


def _ask_claude(system: str, prompt: str, schema: type[T], model: str) -> T:
    import anthropic

    client = anthropic.Anthropic()
    try:
        response = client.messages.parse(
            model=model,
            max_tokens=16000,
            output_config={"effort": "medium"},
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
        )
    except anthropic.AuthenticationError as e:
        raise HighlightError("Claude API credentials are missing or invalid (set ANTHROPIC_API_KEY)") from e
    except anthropic.RateLimitError as e:
        raise HighlightError("Claude API rate limit reached; retry later") from e
    except anthropic.APIStatusError as e:
        raise HighlightError(f"Claude API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise HighlightError("Could not reach the Claude API") from e

    if response.stop_reason == "refusal":
        raise HighlightError("Claude declined to analyze this transcript")
    if response.parsed_output is None:
        raise HighlightError(f"Claude returned no usable answer (stop_reason={response.stop_reason})")
    return response.parsed_output


def clip_transcript(transcript: Transcript, start: float, end: float) -> str:
    return " ".join(w.text for s in transcript.segments for w in s.words if w.start >= start - 0.2 and w.end <= end + 0.2)


def whole_video(transcript: Transcript, options: ProjectOptions, title: str) -> Highlight:
    """'Don't clip' mode: one clip covering the timeframe, trimmed to the first and last sentence."""
    sentences = split_sentences(transcript)
    lo = options.range_start or 0.0
    hi = options.range_end or transcript.duration
    window = [s for s in sentences if s.start >= lo and s.end <= hi]
    if window:
        start, end = cut_points(sentences, window[0].index, window[-1].index, transcript.duration)
    else:
        start, end = lo, hi
    return Highlight(start, end, "", title, "", 0, "Full video", [])
