"""Scripts for the video maker: written by an LLM from a prompt, description or news article,
or taken as given and split into scenes. Every scene gets stock-footage search terms."""

import re
from html.parser import HTMLParser
from urllib.parse import urlparse

import requests
from pydantic import BaseModel, ConfigDict, Field

from ..pipeline.highlights import HighlightError, ask_once, default_service
from ..schemas import (CARD_HELP, MUSIC_MOOD_HELP, PLACE_HELP, QUOTE_BY_HELP, QUOTE_HELP, SEARCH_TERMS_HELP, DraftRequest,
                       ScriptDraft)

WORDS_PER_MINUTE = 150      # typical narration pace at speed 1.0
SCENE_WORDS = 45            # about 18 seconds of narration per scene
ARTICLE_CHARS = 12000       # keeps a request inside Groq's free per-minute token allowance
TAG_BATCH_CHARS = 6000      # scene text per tagging request
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"

WRITE_SYSTEM = """You write narration scripts for long-form YouTube videos that are illustrated with stock footage.

Rules for the narration:
- Spoken words only: no headings, stage directions, speaker labels, emojis, markdown or URLs.
- Open with a hook in the first two sentences, then deliver on it; end with a short conclusion.
- Short, clear sentences that sound natural when read aloud by a text-to-speech voice.
- Split the narration into scenes of 2-4 sentences, each about one idea, in order.

Rules for search_terms (used to find stock video clips for each scene):
- 4-6 English searches of 2-3 words, always in English whatever the narration language, in the order the \
narration mentions those things, so each shot matches what is being said. End with one broader search \
(e.g. "courthouse exterior", "city street") as a fallback.
- Describe what a camera could film: a person or object, doing something, somewhere. Use the words stock \
sites tag footage with: "judge gavel", "police car night", "prison cell door", "woman crying", "surfer waves".
- Never search for abstract ideas, documents, charts, maps, timelines, graphics, text, people's names, \
brands or logos: those cannot be filmed. Use something visual that stands for them instead.

Cards and photos (leave a field empty when it does not apply):
- card_text: at most one scene in four gets a short striking fact or number shown full screen.
- quote / quote_by: only words someone is quoted as saying in the source, copied exactly.
- place: the real city, region or landmark the scene is about, for a photo of it."""

NEWS_RULES = """
This video reports a news article. Use only facts stated in the article; never add names, numbers, \
dates, quotes or claims that are not in it. Attribute claims to their source ("according to ..."). \
Keep a neutral, factual tone."""

TAG_SYSTEM = """You prepare a narration script for a video illustrated with stock footage.
For each numbered scene, give 4-6 English stock-footage searches of 2-3 words (always English, whatever the \
script language), in the order the scene mentions those things, ending with one broader search. Describe what \
a camera could film, using words stock sites tag footage with ("judge gavel", "police car night", "doctor \
laptop"); never abstract ideas, documents, charts, maps, graphics, text, people's names, brands or logos.
Optionally per scene: card_text, a striking fact or number to show full screen (max 6 words, at most one scene \
in four); quote and quote_by, only words the script quotes someone as saying; place, a real named place the \
scene is about. Leave them empty otherwise.
Also give the video a title, a short description and hashtags in the script's language, and a music_mood: \
2-4 English words to search for fitting instrumental background music."""


class SceneTerms(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene: int = Field(description="The scene number")
    search_terms: list[str] = Field(description=SEARCH_TERMS_HELP)
    card_text: str = Field(description=CARD_HELP)
    quote: str = Field(description=QUOTE_HELP)
    quote_by: str = Field(description=QUOTE_BY_HELP)
    place: str = Field(description=PLACE_HELP)


class TagSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="Video title, max ~70 characters")
    description: str = Field(description="One or two sentence video description")
    hashtags: list[str] = Field(description="3-6 hashtags without the # symbol")
    music_mood: str = Field(description=MUSIC_MOOD_HELP)
    scenes: list[SceneTerms]


def draft(req: DraftRequest) -> dict:
    """A reviewed-before-rendering script: title, description, hashtags and scenes.
    A usage limit raises limits.LimitReached so the caller can ask what to do."""
    service = req.service or default_service()
    source_title = None
    if req.kind == "script":
        result = _tag(split_script(req.text), service)
    else:
        material = req.text.strip()
        if req.kind == "news" and req.url:
            source_title, article = read_article(req.url)
            material = f"{material}\n\n{article}".strip()
        result = _write(req, material, source_title, service)
    words = sum(len(s.text.split()) for s in result.scenes)
    return {**result.model_dump(), "service": service, "source_title": source_title, "words": words,
            "estimated_seconds": round(words / WORDS_PER_MINUTE * 60)}


def _write(req: DraftRequest, material: str, source_title: str | None, service: str) -> ScriptDraft:
    words = round(req.minutes * WORDS_PER_MINUTE)
    scenes = max(2, round(words / SCENE_WORDS))
    system = WRITE_SYSTEM + (NEWS_RULES if req.kind == "news" else "")
    what = {"prompt": "Topic or prompt", "description": "Description of the video",
            "news": "News article" + (f" ({source_title})" if source_title else "")}[req.kind]
    prompt = (f"Write the narration in {req.language}, about {words} words in total "
              f"(roughly {req.minutes:g} minutes spoken), split into about {scenes} scenes.\n"
              + (f"Tone and style: {req.tone}\n" if req.tone.strip() else "")
              + f"\n{what}:\n{material[:ARTICLE_CHARS]}")
    result = ask_once(service, system, prompt, ScriptDraft)
    result.scenes = [s for s in result.scenes if s.text.strip()]
    if not result.scenes:
        raise HighlightError("The model returned an empty script; try again")
    for s in result.scenes:
        s.search_terms = _clean_terms(s.search_terms) or fallback_terms(s.text)
        s.card_text, s.quote, s.quote_by, s.place = (s.card_text.strip()[:80], s.quote.strip()[:400],
                                                     s.quote_by.strip()[:120], s.place.strip()[:120])
        if s.quote and not quoted_in(s.quote, material):
            s.quote = ""  # never show words the source does not contain
        if not s.quote:
            s.quote_by = ""
    return result


def _tag(scenes: list[str], service: str) -> ScriptDraft:
    """Search terms for a script the user wrote, without changing its words."""
    if not scenes:
        raise HighlightError("The script is empty")
    tags: dict[int, SceneTerms] = {}
    head: TagSet | None = None
    batch: list[int] = []
    size = 0
    for i, text in enumerate(scenes + [""]):
        if batch and (i == len(scenes) or size + len(text) > TAG_BATCH_CHARS):
            prompt = "Scenes:\n" + "\n".join(f"[{n + 1}] {scenes[n]}" for n in batch)
            result = ask_once(service, TAG_SYSTEM, prompt, TagSet)
            head = head or result
            tags.update({t.scene - 1: t for t in result.scenes if t.scene - 1 in batch})
            batch, size = [], 0
        if i < len(scenes):
            batch.append(i)
            size += len(text)
    assert head is not None
    out = []
    for n, text in enumerate(scenes):
        t = tags.get(n)
        terms = _clean_terms(t.search_terms) if t else []
        quote = t.quote.strip() if t and quoted_in(t.quote, text) else ""
        out.append({"text": text, "search_terms": terms or fallback_terms(text),
                    "card_text": t.card_text.strip()[:80] if t else "", "quote": quote[:400],
                    "quote_by": t.quote_by.strip()[:120] if t and quote else "", "place": t.place.strip()[:120] if t else ""})
    return ScriptDraft(title=head.title, description=head.description, hashtags=head.hashtags,
                       music_mood=head.music_mood.strip()[:100], scenes=out)


def _letters(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.lower()))


def quoted_in(quote: str, source: str) -> bool:
    """Whether the quote appears word for word in the source (ignoring punctuation and case)."""
    q = _letters(quote)
    return bool(q) and q in _letters(source)


def split_script(text: str, target_words: int = SCENE_WORDS) -> list[str]:
    """Scenes from a written script: one per paragraph, with long paragraphs split between sentences."""
    scenes: list[str] = []
    for para in re.split(r"\n\s*\n|\n(?=\S)", text.strip()):
        para = " ".join(para.split())
        if not para:
            continue
        sentences = re.split(r"(?<=[.!?。！？])\s+", para)
        current: list[str] = []
        first = len(scenes)
        for sentence in sentences:
            current.append(sentence)
            if len(" ".join(current).split()) >= target_words * 1.4:
                scenes.append(" ".join(current))
                current = []
        if current:
            # A short leftover of a long paragraph joins its previous part; short paragraphs stay scenes.
            if len(scenes) > first and len(" ".join(current).split()) < target_words * 0.3:
                scenes[-1] += " " + " ".join(current)
            else:
                scenes.append(" ".join(current))
    return scenes


def _clean_terms(terms: list[str]) -> list[str]:
    out = []
    for t in terms:
        t = re.sub(r"\s+", " ", re.sub(r"[^\w\s'-]", " ", t)).strip().lower()
        if t and t not in out:
            out.append(t[:60])
    return out[:6]


STOPWORDS = frozenset("""
a an and are as at be been but by can could did do does for from had has have he her his i if in into is it its
just like me my no not of on or our she so than that the their them then there these they this those to too up us
was we were what when where which who why will with would you your about after again all also any because before
being between both each few more most other over same should some such through under until very while
""".split())


def fallback_terms(text: str) -> list[str]:
    """Search terms without an LLM: the scene's longest uncommon words."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]{3,}", text) if w.lower() not in STOPWORDS]
    ranked = sorted(dict.fromkeys(w.lower() for w in words), key=len, reverse=True)
    return ranked[:2] or ["abstract background"]


# --- Reading a news article -----------------------------------------------------


class _Article(HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "figure", "button", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.og_title = ""
        self.paragraphs: list[str] = []
        self._skip = 0
        self._in_title = False
        self._p: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            a = dict(attrs)
            if a.get("property") == "og:title" and a.get("content"):
                self.og_title = a["content"] or ""
        elif tag == "p" and not self._skip:
            self._p = []

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag == "p" and self._p is not None:
            text = " ".join("".join(self._p).split())
            if len(text.split()) >= 8:  # skip captions, bylines and buttons
                self.paragraphs.append(text)
            self._p = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif self._p is not None and not self._skip:
            self._p.append(data)


def read_article(url: str) -> tuple[str | None, str]:
    """(title, text) of a news article page."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HighlightError("The article link must be an http(s) URL")
    try:
        r = requests.get(url.strip(), timeout=30, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    except requests.RequestException as e:
        raise HighlightError(f"Could not open the article link: {e.__class__.__name__}") from e
    if r.status_code >= 400:
        raise HighlightError(f"The article site answered {r.status_code}; paste the article text instead")
    if "html" not in r.headers.get("content-type", "html"):
        raise HighlightError("The link is not a web page; paste the article text instead")
    parser = _Article()
    parser.feed(r.text)
    text = "\n\n".join(parser.paragraphs)
    if len(text.split()) < 80:
        raise HighlightError("Could not find the article text on that page (it may need a login or "
                             "JavaScript); paste the article text instead")
    title = (parser.og_title or parser.title).strip() or None
    return title, text
