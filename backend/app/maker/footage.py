"""Free stock media for the video maker: video from Pixabay, Pexels and Coverr (free API keys), real-world
video and photos from Wikimedia Commons (no key), and photos from Unsplash (free key).

Every result is scored against the search words using the site's own description of it
(Pixabay and Coverr tags, the Pexels page name, the Commons file name, the Unsplash description), so a
search for "judge gavel"
never fills a shot with a flower that Pixabay matched loosely. Animation, cartoons, green
screen and AI-generated clips are left out. Searches are cached for a day (Pixabay asks for
that), which also lets the footage review and the final render share one set of results.

Clips are not downloaded whole: ffmpeg reads only the seconds a shot uses (see make.py).
"""

import html
import os
import re
import threading
import time
from dataclasses import asdict, dataclass

import requests

from ..config import CONTACT, COVERR_API_KEY, PEXELS_API_KEY, PIXABAY_API_KEY, UNSPLASH_ACCESS_KEY
from ..pipeline import limits

PER_PAGE = {"pexels": 40, "pixabay": 50, "coverr": 40, "unsplash": 30}
# Searchable sources, in the order their results are preferred when equally relevant.
SOURCES = ("pixabay", "pexels", "coverr", "commons", "unsplash")
SIGNUP = {"pexels": "pexels.com/api", "pixabay": "pixabay.com/api/docs", "coverr": "email team@coverr.co",
          "unsplash": "unsplash.com/developers"}
KEY_NAME = {"pexels": "PEXELS_API_KEY", "pixabay": "PIXABAY_API_KEY", "coverr": "COVERR_API_KEY",
            "unsplash": "UNSPLASH_ACCESS_KEY"}
# Commons holds real news footage, some of it graphic; such titles are never offered.
GRAPHIC = re.compile(r"\b(kill(ed|ing|s)?|murder|massacre|terroris[tm]s?|shoot(ing|s)?|shot dead|gunfire|fire into|"
                     r"behead\w*|execution|corpse|dead bod(y|ies)|gore|blood(y)?|wounded|injur(ed|ies)|"
                     r"bomb(ing|ed)?|explosion|attack(s|ed)?|war crime|torture|suicide|victims?|ois|officer[- ]involved|"
                     r"body[- ]?cam|bodycam|dash[- ]?cam|riot|stabb(ing|ed)|assault|arrest(ed)? video)\b", re.I)
USER_AGENT = f"ShortcutStudio/1.0 (personal video tool{'; ' + CONTACT if CONTACT else ''})"
CACHE_SECONDS = 24 * 3600
RELEVANT = 0.66         # share of the search words a clip's description must contain to be used automatically
PHOTO_MIN_WIDTH = 1000
# Descriptions that mark footage unsuited to a narrated documentary-style video.
UNWANTED = {"cartoon", "animation", "animated", "2d", "3d", "green screen", "greenscreen", "chroma key", "chromakey",
            "ai generated", "ai-generated", "vector", "illustration", "clipart", "clip art", "emoji", "render",
            "cgi", "character design", "motion graphics", "flat design", "logo", "intro", "template"}
FREE_LICENSES = ("cc0", "public domain", "pd", "cc by", "cc-by")
STOPWORDS = frozenset("a an and at by for from in into of on or the to with".split())
# Words that say little about what is on screen; a clip must match the specific words of a search.
GENERIC = frozenset(_w.rstrip("s") if len(_w) > 3 and not _w.endswith("ss") else _w for _w in """
team people person man men woman women group crowd city street night day view background scene close closeup shot
light dark old new young big small hand hands face life time place area thing building outdoor indoor
""".split())
GENERIC_WEIGHT = 0.3


class FootageError(RuntimeError):
    pass


@dataclass(frozen=True)
class StockClip:
    provider: str            # pixabay | pexels | coverr | commons (video) | wikimedia (photo) | unsplash | upload
    id: str
    link: str                # the mp4 file, or the photo
    width: int
    height: int
    duration: float          # seconds; 0 for photos, which can fill a shot of any length
    page: str                # the clip's page, for credits
    author: str
    thumb: str = ""          # still image for the footage review
    preview: str = ""        # small mp4 for previewing on hover
    text: str = ""           # what the site says is in it: tags, page name or file name
    kind: str = "video"      # video | photo
    license: str = ""        # photos: e.g. "CC BY-SA 4.0"
    score: float = 0.0       # relevance to the search that found it, 0..1
    ping: str = ""           # called once when used (Unsplash counts downloads this way)

    @property
    def key(self) -> str:
        return f"{self.provider}-{self.id}"

    def long_enough(self, seconds: float) -> bool:
        return self.kind == "photo" or self.duration >= seconds + 0.3

    def public(self) -> dict:
        return asdict(self)


def available() -> dict[str, bool]:
    return {"pixabay": bool(PIXABAY_API_KEY), "pexels": bool(PEXELS_API_KEY), "coverr": bool(COVERR_API_KEY),
            "commons": True, "unsplash": bool(UNSPLASH_ACCESS_KEY)}


def providers_for(wanted: list[str]) -> list[str]:
    """The chosen sources that can be used; an error if none of them has its key."""
    have = available()
    usable = [p for p in SOURCES if p in wanted and have[p]]
    missing = [p for p in wanted if not have.get(p)]
    if wanted and not usable:
        names = " and ".join(KEY_NAME[p] for p in missing)
        raise FootageError(f"Those footage sources need {names} in backend/.env "
                           f"({', '.join(SIGNUP[p] for p in missing)}), or choose another source")
    return usable


def orientation_of(aspect: str) -> str:
    return {"16:9": "landscape", "9:16": "portrait"}.get(aspect, "square")


# --- Relevance -----------------------------------------------------------------------------


def _stem(word: str) -> str:
    word = word.lower()
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _words(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-zA-Z0-9]+", text.lower())]


def relevance(term: str, text: str) -> float:
    """Share of the search words found in a clip's description. Words match on their stem or
    when one is the other plus a short ending ("sentencing" ~ "sentence", "courtroom" ~ "court")."""
    wanted = [w for w in _words(term) if w not in STOPWORDS]
    if not wanted:
        return 0.0
    have = set(_words(text))

    def same(a: str, b: str) -> bool:  # "court" ~ "courtroom", "sentence" ~ "sentencing"; not "test" ~ "testimony"
        common = len(os.path.commonprefix([a, b]))
        return common >= 5 and common >= min(len(a), len(b)) - 1

    weight = {w: GENERIC_WEIGHT if w in GENERIC else 1.0 for w in wanted}
    hit = sum(weight[w] for w in wanted if w in have or any(same(w, h) for h in have))
    return hit / sum(weight.values())


def unwanted(text: str) -> bool:
    t = " " + " ".join(re.findall(r"[a-z0-9-]+", text.lower())) + " "
    return any(f" {u} " in t for u in UNWANTED)


# --- Searching -------------------------------------------------------------------------------

_cache: dict[tuple, tuple[float, list[StockClip]]] = {}
_cache_lock = threading.Lock()


def search(provider: str, term: str, orientation: str, short_side: int, page: int = 1) -> list[StockClip]:
    """Clips for `term`, most relevant first (ties keep the site's order); orientation matches
    rank above others. Unsuitable clips (animation, AI, green screen...) are left out."""
    key = (provider, term.strip().lower(), orientation, short_side, page)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    if provider == "pexels":
        found = _pexels(term, orientation, short_side, page)
    elif provider == "pixabay":
        found = _pixabay(term, short_side, page)
    elif provider == "coverr":
        found = _coverr(term, short_side, page)
    elif provider == "commons":
        found = _commons_video(term, short_side)
    elif provider == "unsplash":
        found = _unsplash(term, orientation, page)
    elif provider == "wikimedia":
        found = _wikimedia(term, page)
    else:
        raise FootageError(f"Unknown footage source {provider!r}")
    want_wide, want_tall = orientation == "landscape", orientation == "portrait"

    def fits(c: StockClip) -> bool:
        return (c.width > c.height) if want_wide else (c.height > c.width) if want_tall else True
    scored = [StockClip(**{**asdict(c), "score": round(relevance(term, c.text), 2)}) for c in found
              if not unwanted(c.text)]
    result = sorted(scored, key=lambda c: (-c.score, not fits(c)))
    with _cache_lock:
        _cache[key] = (time.time(), result)
    return result


def _get(provider: str, url: str, params: dict, headers: dict) -> dict:
    try:
        r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT, **headers}, timeout=30)
    except requests.RequestException as e:
        raise FootageError(f"Could not reach {limits.SERVICE_LABELS[provider]}") from e
    if r.status_code == 429:
        raise limits.LimitReached(_limit(provider, r))
    if r.status_code in (400, 401, 403) and provider == "pixabay" and "key" in r.text.lower():
        raise FootageError("Pixabay API key is invalid (PIXABAY_API_KEY)")
    if r.status_code in (401, 403) and provider in KEY_NAME:
        raise FootageError(f"{limits.SERVICE_LABELS[provider]} API key is invalid ({KEY_NAME[provider]})")
    if r.status_code >= 400:
        raise FootageError(f"{limits.SERVICE_LABELS[provider]} error {r.status_code}: {r.text[:200]}")
    return r.json()


def _limit(provider: str, r: requests.Response) -> limits.Limit:
    h = r.headers

    def num(name: str) -> float | None:
        try:
            return float(h.get(name))
        except (TypeError, ValueError):
            return None
    total, left, reset = num("X-Ratelimit-Limit"), num("X-Ratelimit-Remaining"), num("X-Ratelimit-Reset")
    if reset is None:
        wait = num("Retry-After") or 60.0
    elif reset > 1e9:  # Pexels: a Unix time
        wait = max(1.0, reset - time.time())
    else:              # Pixabay: seconds left
        wait = max(1.0, reset)
    used = None if total is None or left is None else total - left
    name = {"pexels": "requests per hour", "pixabay": "requests per minute", "unsplash": "requests per hour"}.get(
        provider, "requests")
    return limits.Limit(provider, "stock media search", name, None, total, used, 1, wait, r.text[:300])


def _pexels(term: str, orientation: str, short_side: int, page: int) -> list[StockClip]:
    data = _get("pexels", "https://api.pexels.com/videos/search",
                {"query": term, "orientation": orientation, "per_page": PER_PAGE["pexels"], "page": page},
                {"Authorization": PEXELS_API_KEY})
    out = []
    for v in data.get("videos") or []:
        files = [f for f in v.get("video_files") or []
                 if f.get("file_type") == "video/mp4" and f.get("width") and f.get("height") and f.get("link")]
        best = _smallest_enough(files, short_side)
        if not best or not v.get("duration"):
            continue
        smallest = min(files, key=lambda f: min(int(f["width"]), int(f["height"])))
        page_url = v.get("url") or ""
        slug = re.search(r"/video/(.+?)-?\d*/?$", page_url)
        out.append(StockClip("pexels", str(v["id"]), best["link"], int(best["width"]), int(best["height"]),
                             float(v["duration"]), page_url, (v.get("user") or {}).get("name") or "",
                             thumb=v.get("image") or "", preview=smallest["link"],
                             text=slug.group(1).replace("-", " ") if slug else ""))
    return out


def _pixabay(term: str, short_side: int, page: int) -> list[StockClip]:
    data = _get("pixabay", "https://pixabay.com/api/videos/",
                {"key": PIXABAY_API_KEY, "q": term[:100], "per_page": PER_PAGE["pixabay"], "page": page,
                 "safesearch": "true", "video_type": "film"}, {})
    out = []
    for v in data.get("hits") or []:
        if v.get("type", "film") != "film" or v.get("isAiGenerated"):
            continue
        variants = v.get("videos") or {}
        files = [{**f, "link": f.get("url")} for f in variants.values()
                 if isinstance(f, dict) and f.get("url") and f.get("width") and f.get("height")]
        best = _smallest_enough(files, short_side)
        if not best or not v.get("duration"):
            continue
        tiny = variants.get("tiny") or variants.get("small") or {}
        thumb = (variants.get("small") or variants.get("medium") or {}).get("thumbnail") or tiny.get("thumbnail") or ""
        out.append(StockClip("pixabay", str(v["id"]), best["link"], int(best["width"]), int(best["height"]),
                             float(v["duration"]), v.get("pageURL") or "", v.get("user") or "",
                             thumb=thumb, preview=tiny.get("url") or "", text=v.get("tags") or ""))
    return out


def _coverr(term: str, short_side: int, page: int) -> list[StockClip]:
    data = _get("coverr", "https://api.coverr.co/videos",
                {"query": term, "page": page - 1, "page_size": PER_PAGE["coverr"], "urls": "true"},
                {"Authorization": f"Bearer {COVERR_API_KEY}"})
    out = []
    for v in data.get("hits") or []:
        urls = v.get("urls") or {}
        if not urls.get("mp4") or not v.get("duration"):
            continue
        text = " ".join([v.get("title") or "", v.get("description") or "", ", ".join(v.get("tags") or [])])
        out.append(StockClip("coverr", str(v["id"]), urls["mp4"], int(v.get("max_width") or 1920),
                             int(v.get("max_height") or 1080), float(v["duration"]),
                             f"https://coverr.co/videos/{v.get('slug') or v['id']}", "Coverr",
                             thumb=v.get("thumbnail") or v.get("poster") or "", preview=urls.get("mp4_preview") or "",
                             text=text))
    return out


def coverr_link(clip_id: str) -> str | None:
    """A current file link for a Coverr video (its links are signed and may expire)."""
    try:
        v = _get("coverr", f"https://api.coverr.co/videos/{clip_id}", {}, {"Authorization": f"Bearer {COVERR_API_KEY}"})
    except (FootageError, limits.LimitReached):
        return None
    return (v.get("urls") or {}).get("mp4")


def _unsplash(term: str, orientation: str, page: int) -> list[StockClip]:
    data = _get("unsplash", "https://api.unsplash.com/search/photos",
                {"query": term, "page": page, "per_page": PER_PAGE["unsplash"], "content_filter": "high",
                 "orientation": "squarish" if orientation == "square" else orientation},
                {"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}", "Accept-Version": "v1"})
    out = []
    for p in data.get("results") or []:
        urls, links, user = p.get("urls") or {}, p.get("links") or {}, p.get("user") or {}
        if not urls.get("raw"):
            continue
        tags = ", ".join(t.get("title", "") for t in p.get("tags") or [] if isinstance(t, dict))
        text = " ".join(x for x in [p.get("alt_description") or "", p.get("description") or "", tags] if x)
        page_url = (links.get("html") or "") + "?utm_source=shortcut_studio&utm_medium=referral"
        out.append(StockClip("unsplash", str(p["id"]), urls["raw"] + "&w=2560&fm=jpg&q=85", int(p.get("width") or 0),
                             int(p.get("height") or 0), 0.0, page_url, user.get("name") or "",
                             thumb=urls.get("small") or "", text=text, kind="photo", license="Unsplash License",
                             ping=links.get("download_location") or ""))
    return out


def register_use(clip: StockClip) -> None:
    """Unsplash asks apps to report each photo they use through its download link."""
    if clip.provider == "unsplash" and clip.ping:
        try:
            requests.get(clip.ping, headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}",
                                             "User-Agent": USER_AGENT}, timeout=15)
        except requests.RequestException:
            pass


def _commons_video(term: str, short_side: int) -> list[StockClip]:
    """Real-world video from Wikimedia Commons, read from its transcoded copies."""
    found = _get("wikimedia", "https://api.wikimedia.org/core/v1/commons/search/page",
                 {"q": f"{term} filetype:video", "limit": 30}, {})
    titles = [p["key"] for p in found.get("pages") or [] if str(p.get("key", "")).startswith("File:")
              and not GRAPHIC.search(str(p["key"]).replace("_", " "))]
    if not titles:
        return []
    info = _get("wikimedia", "https://en.wikipedia.org/w/api.php",
                {"action": "query", "titles": "|".join(titles[:30]), "prop": "videoinfo", "format": "json",
                 "viprop": "url|size|mime|extmetadata|derivatives", "viurlwidth": 640}, {})
    pages = {p.get("title", "").replace(" ", "_"): p for p in (info.get("query") or {}).get("pages", {}).values()}
    out = []
    for title in titles:
        vi = ((pages.get(title) or {}).get("videoinfo") or [{}])[0]
        meta = vi.get("extmetadata") or {}
        lic = _plain((meta.get("LicenseShortName") or {}).get("value", ""))
        if not lic.lower().startswith(FREE_LICENSES) or re.search(r"\b(nc|nd)\b", lic.lower()):
            continue
        files = [{"link": d["src"], "width": d["width"], "height": d["height"]} for d in vi.get("derivatives") or []
                 if d.get("width") and d.get("height") and "webm" in (d.get("type") or "")]
        best = _smallest_enough(files, short_side)
        if not best or not vi.get("duration"):
            continue
        name = re.sub(r"\.[a-z0-9]+$", "", title.removeprefix("File:")).replace("_", " ")
        out.append(StockClip("commons", title, best["link"], int(best["width"]), int(best["height"]),
                             float(vi["duration"]), vi.get("descriptionurl") or "",
                             _plain((meta.get("Artist") or {}).get("value", ""))[:120],
                             thumb=vi.get("thumburl") or "", preview=min(files, key=lambda f: f["height"])["link"],
                             text=name, license=lic))  # descriptions are long and match almost anything
    return out


def _wikimedia(term: str, page: int) -> list[StockClip]:
    """Freely licensed photos from Wikimedia Commons, found through api.wikimedia.org and
    described through en.wikipedia.org (both serve Commons files)."""
    found = _get("wikimedia", "https://api.wikimedia.org/core/v1/commons/search/page",
                 {"q": f"{term} filetype:bitmap", "limit": 20 * page}, {})
    titles = [p["key"] for p in found.get("pages") or [] if str(p.get("key", "")).startswith("File:")]
    titles = titles[20 * (page - 1):]
    if not titles:
        return []
    info = _get("wikimedia", "https://en.wikipedia.org/w/api.php",
                {"action": "query", "titles": "|".join(titles[:20]), "prop": "imageinfo", "format": "json",
                 "iiprop": "url|size|extmetadata|mime", "iiurlwidth": 1920}, {})
    pages = {p.get("title", "").replace(" ", "_"): p for p in (info.get("query") or {}).get("pages", {}).values()}
    out = []
    for title in titles:  # keep the search order
        p = pages.get(title)
        ii = ((p or {}).get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata") or {}
        lic = _plain((meta.get("LicenseShortName") or {}).get("value", ""))
        if ii.get("mime") not in ("image/jpeg", "image/png") or int(ii.get("width") or 0) < PHOTO_MIN_WIDTH:
            continue
        if not lic.lower().startswith(FREE_LICENSES) or re.search(r"\b(nc|nd)\b", lic.lower()):
            continue
        name = re.sub(r"\.[a-z]+$", "", title.removeprefix("File:")).replace("_", " ")
        width, height = int(ii.get("thumbwidth") or ii["width"]), int(ii.get("thumbheight") or ii["height"])
        out.append(StockClip("wikimedia", title, ii.get("thumburl") or ii["url"], width, height, 0.0,
                             ii.get("descriptionurl") or "", _plain((meta.get("Artist") or {}).get("value", ""))[:120],
                             thumb=ii.get("thumburl") or ii["url"], text=name, kind="photo", license=lic))
    return out


def _plain(markup: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", markup)).split())


def _smallest_enough(files: list[dict], short_side: int) -> dict | None:
    """The smallest file at least as sharp as the output; otherwise the sharpest one."""
    if not files:
        return None
    side = lambda f: min(int(f["width"]), int(f["height"]))
    enough = [f for f in files if side(f) >= short_side]
    return min(enough, key=side) if enough else max(files, key=side)


def clip_from(data: dict) -> StockClip:
    """A clip the footage review sent back (chosen by the user)."""
    fields = set(StockClip.__dataclass_fields__)
    return StockClip(**{k: v for k, v in data.items() if k in fields})
