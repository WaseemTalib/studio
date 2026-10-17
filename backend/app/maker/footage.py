"""Free stock video from Pexels and Pixabay (both need a free API key).

Clips are not downloaded whole: ffmpeg reads only the seconds a shot uses straight from the
file link (see make.py). Search results are cached per job.
"""

import time
from dataclasses import dataclass

import requests

from ..config import PEXELS_API_KEY, PIXABAY_API_KEY
from ..pipeline import limits

PER_PAGE = 30
SIGNUP = {"pexels": "pexels.com/api", "pixabay": "pixabay.com/api/docs"}
KEY_NAME = {"pexels": "PEXELS_API_KEY", "pixabay": "PIXABAY_API_KEY"}


class FootageError(RuntimeError):
    pass


@dataclass(frozen=True)
class StockClip:
    provider: str
    id: str
    link: str           # the mp4 file
    width: int
    height: int
    duration: float
    page: str           # the clip's page, for credits
    author: str

    @property
    def key(self) -> str:
        return f"{self.provider}-{self.id}"


def available() -> dict[str, bool]:
    return {"pexels": bool(PEXELS_API_KEY), "pixabay": bool(PIXABAY_API_KEY)}


def providers_for(choice: str) -> list[str]:
    if choice == "none":
        return []
    wanted = ["pexels", "pixabay"] if choice == "both" else [choice]
    have = available()
    missing = [p for p in wanted if not have[p]]
    if missing and len(missing) == len(wanted):
        names = " and ".join(KEY_NAME[p] for p in missing)
        raise FootageError(f"Stock footage needs {names} in backend/.env (free key at "
                           f"{', '.join(SIGNUP[p] for p in missing)}), or choose a plain background")
    return [p for p in wanted if have[p]]


def search(provider: str, term: str, orientation: str, short_side: int, page: int = 1) -> list[StockClip]:
    """Clips for `term`, best orientation match first. orientation: landscape, portrait or square."""
    clips = _pexels(term, orientation, short_side, page) if provider == "pexels" else \
        _pixabay(term, short_side, page)
    want_wide = orientation == "landscape"
    want_tall = orientation == "portrait"

    def fits(c: StockClip) -> bool:
        return (c.width > c.height) if want_wide else (c.height > c.width) if want_tall else True
    return sorted(clips, key=lambda c: not fits(c))  # stable: keeps the site's relevance order


def _get(provider: str, url: str, params: dict, headers: dict) -> dict:
    try:
        r = requests.get(url, params=params, headers=headers, timeout=30)
    except requests.RequestException as e:
        raise FootageError(f"Could not reach {limits.SERVICE_LABELS[provider]}") from e
    if r.status_code == 429:
        raise limits.LimitReached(_limit(provider, r))
    if r.status_code in (400, 401, 403) and provider == "pixabay" and "key" in r.text.lower():
        raise FootageError("Pixabay API key is invalid (PIXABAY_API_KEY)")
    if r.status_code in (401, 403):
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
        wait = 60.0
    elif reset > 1e9:  # Pexels: a Unix time
        wait = max(1.0, reset - time.time())
    else:              # Pixabay: seconds left
        wait = max(1.0, reset)
    used = None if total is None or left is None else total - left
    name = "requests per hour" if provider == "pexels" else "requests per minute"
    return limits.Limit(provider, "stock video search", name, None, total, used, 1, wait, r.text[:300])


def _pexels(term: str, orientation: str, short_side: int, page: int) -> list[StockClip]:
    data = _get("pexels", "https://api.pexels.com/videos/search",
                {"query": term, "orientation": orientation, "per_page": PER_PAGE, "page": page},
                {"Authorization": PEXELS_API_KEY})
    out = []
    for v in data.get("videos") or []:
        files = [f for f in v.get("video_files") or []
                 if f.get("file_type") == "video/mp4" and f.get("width") and f.get("height") and f.get("link")]
        best = _smallest_enough(files, short_side)
        if best and v.get("duration"):
            out.append(StockClip("pexels", str(v["id"]), best["link"], int(best["width"]), int(best["height"]),
                                 float(v["duration"]), v.get("url") or "", (v.get("user") or {}).get("name") or ""))
    return out


def _pixabay(term: str, short_side: int, page: int) -> list[StockClip]:
    data = _get("pixabay", "https://pixabay.com/api/videos/",
                {"key": PIXABAY_API_KEY, "q": term[:100], "per_page": PER_PAGE, "page": page,
                 "safesearch": "true"}, {})
    out = []
    for v in data.get("hits") or []:
        files = [{**f, "link": f.get("url")} for f in (v.get("videos") or {}).values()
                 if isinstance(f, dict) and f.get("url") and f.get("width") and f.get("height")]
        best = _smallest_enough(files, short_side)
        if best and v.get("duration"):
            out.append(StockClip("pixabay", str(v["id"]), best["link"], int(best["width"]), int(best["height"]),
                                 float(v["duration"]), v.get("pageURL") or "", v.get("user") or ""))
    return out


def _smallest_enough(files: list[dict], short_side: int) -> dict | None:
    """The smallest file at least as sharp as the output; otherwise the sharpest one."""
    if not files:
        return None
    side = lambda f: min(int(f["width"]), int(f["height"]))
    enough = [f for f in files if side(f) >= short_side]
    return min(enough, key=side) if enough else max(files, key=side)
