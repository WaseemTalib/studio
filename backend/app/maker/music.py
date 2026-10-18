"""Background music: the user's library, plus free-licensed tracks found on Jamendo (free client ID) or
Wikimedia Commons (no key).

Only tracks whose licence allows commercial use are offered, so videos stay safe to monetise, and
Jamendo tracks are only offered when their artist allows downloads. A track added to the library
keeps its author, licence and page in `.credits.json` beside it; videos list them in their credits.
"""

import json
import random
import re
import threading
from pathlib import Path
from urllib.parse import urlparse

import requests

from ..config import CONTACT, JAMENDO_CLIENT_ID, MUSIC_DIR
from ..pipeline import cancel
from .footage import USER_AGENT, _get, _plain

SUFFIXES = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".oga", ".opus", ".flac", ".webm"}
CREDITS = MUSIC_DIR / ".credits.json"
MAX_DOWNLOAD = 30 * 1024 * 1024
MIN_SECONDS = 30
MAX_SECONDS = 600       # it loops under the narration, so a long piece is never needed
MAX_RETRY_WAIT = 10     # seconds; a longer "try again later" is reported instead of waited out
COMMONS_TYPES = {"audio/mpeg", "audio/ogg", "application/ogg", "audio/x-flac", "audio/flac", "audio/wav",
                 "audio/x-wav", "audio/webm", "audio/opus"}
FREE_LICENSES = ("cc0", "public domain", "pd", "cc by", "cc-by")
_lock = threading.Lock()


class MusicError(RuntimeError):
    pass


def sources() -> dict[str, bool]:
    return {"jamendo": bool(JAMENDO_CLIENT_ID), "commons": True}


# --- Library ------------------------------------------------------------------------------


def _credits() -> dict[str, dict]:
    try:
        return json.loads(CREDITS.read_text())
    except (OSError, ValueError):
        return {}


def _save_credits(data: dict[str, dict]) -> None:
    CREDITS.write_text(json.dumps(data, indent=1))


def library(media_url) -> list[dict]:
    credits = _credits()
    return [{"name": p.name, "url": media_url(p), "size": p.stat().st_size, "credit": credits.get(p.name)}
            for p in sorted(MUSIC_DIR.iterdir()) if p.is_file() and p.suffix.lower() in SUFFIXES]


def path_of(name: str) -> Path:
    path = MUSIC_DIR / name
    if path.parent != MUSIC_DIR or not path.is_file() or path.suffix.lower() not in SUFFIXES:
        raise MusicError(f"Background music {name!r} is not in the music library")
    return path


def credit_of(name: str) -> dict | None:
    return _credits().get(name)


def safe_name(filename: str) -> str:
    stem, suffix = Path(filename).stem, Path(filename).suffix.lower()
    if suffix not in SUFFIXES:
        raise MusicError(f"Unsupported audio type {suffix or '(none)'}; use {', '.join(sorted(SUFFIXES))}")
    stem = re.sub(r"[^\w .()&'-]+", " ", stem).strip()[:80] or "track"
    return stem + suffix


def remove(name: str) -> None:
    path_of(name).unlink()
    with _lock:
        credits = _credits()
        if credits.pop(name, None) is not None:
            _save_credits(credits)


def pick_random() -> str:
    names = [p.name for p in MUSIC_DIR.iterdir() if p.is_file() and p.suffix.lower() in SUFFIXES]
    if not names:
        raise MusicError("The music library is empty; add a track first")
    return random.choice(names)


# --- Finding music --------------------------------------------------------------------------


def search(query: str, source: str, instrumental: bool = True) -> list[dict]:
    """Free-licensed tracks for `query`, e.g. "calm ambient piano"."""
    if source == "jamendo":
        return _jamendo(query, instrumental)
    if source == "commons":
        return _commons(query)
    raise MusicError(f"Unknown music source {source!r}")


def _commercial_ok(license_text: str) -> bool:
    text = license_text.lower()
    return not re.search(r"(\bnc\b|-nc\b|noncommercial|non-commercial|\bnd\b|-nd\b|noderiv)", text)


def _jamendo(query: str, instrumental: bool) -> list[dict]:
    if not JAMENDO_CLIENT_ID:
        raise MusicError("Jamendo needs JAMENDO_CLIENT_ID in backend/.env (free at devportal.jamendo.com)")
    params = {"client_id": JAMENDO_CLIENT_ID, "format": "json", "limit": 40, "search": query,
              "include": "musicinfo licenses", "audioformat": "mp32", "audiodlformat": "mp32",
              "ccnc": "false", "ccnd": "false", "order": "relevance", "durationbetween": f"{MIN_SECONDS}_{MAX_SECONDS}",
              "imagesize": 100}
    if instrumental:
        params["vocalinstrumental"] = "instrumental"
    try:
        data = _get("jamendo", "https://api.jamendo.com/v3.0/tracks/", params, {})
    except Exception as e:  # FootageError or a limit: say it in music terms
        raise MusicError(f"Jamendo: {e}") from e
    status = (data.get("headers") or {})
    if status.get("status") == "failed":
        raise MusicError(f"Jamendo: {status.get('error_message') or 'request failed'}")
    out = []
    for t in data.get("results") or []:
        lic = t.get("license_ccurl") or ""
        if not t.get("audiodownload_allowed") or not t.get("audiodownload") or not _commercial_ok(lic):
            continue
        tags = ((t.get("musicinfo") or {}).get("tags") or {})
        out.append({
            "provider": "jamendo", "id": str(t["id"]), "title": t.get("name") or "Untitled",
            "author": t.get("artist_name") or "", "duration": float(t.get("duration") or 0),
            "preview": t.get("audio") or "", "download": t["audiodownload"], "page": t.get("shareurl") or "",
            "license": _license_name(lic), "license_url": lic, "image": t.get("image") or "",
            "tags": ", ".join((tags.get("genres") or []) + (tags.get("vartags") or []))[:120],
            "ext": ".mp3",
        })
    return out


def _license_name(url: str) -> str:
    m = re.search(r"licenses/([a-z-]+)/([\d.]+)", url)
    return f"CC {m.group(1).upper()} {m.group(2)}" if m else (url or "")


def _commons(query: str) -> list[dict]:
    found = _get("wikimedia", "https://api.wikimedia.org/core/v1/commons/search/page",
                 {"q": f"{query} music filetype:audio", "limit": 30}, {})
    titles = [p["key"] for p in found.get("pages") or [] if str(p.get("key", "")).startswith("File:")]
    if not titles:
        return []
    info = _get("wikimedia", "https://en.wikipedia.org/w/api.php",
                {"action": "query", "titles": "|".join(titles[:30]), "prop": "imageinfo", "format": "json",
                 "iiprop": "url|size|mime|extmetadata"}, {})
    pages = {p.get("title", "").replace(" ", "_"): p for p in (info.get("query") or {}).get("pages", {}).values()}
    out = []
    for title in titles:
        ii = ((pages.get(title) or {}).get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata") or {}
        lic = _plain((meta.get("LicenseShortName") or {}).get("value", ""))
        duration = float(ii.get("duration") or 0)
        if (ii.get("mime") not in COMMONS_TYPES or not MIN_SECONDS <= duration <= MAX_SECONDS
                or int(ii.get("size") or 0) > MAX_DOWNLOAD):
            continue
        if not lic.lower().startswith(FREE_LICENSES) or not _commercial_ok(lic):
            continue
        name = _clean_title(re.sub(r"\.[a-z0-9]+$", "", title.removeprefix("File:")).replace("_", " "))
        out.append({
            "provider": "commons", "id": title, "title": name,
            "author": _clean_author(_plain((meta.get("Artist") or {}).get("value", "")), name), "duration": duration,
            "preview": ii["url"], "download": ii["url"], "page": ii.get("descriptionurl") or "",
            "license": lic, "license_url": _plain((meta.get("LicenseUrl") or {}).get("value", "")), "image": "",
            "tags": _plain((meta.get("ImageDescription") or {}).get("value", ""))[:120],
            "ext": Path(urlparse(ii["url"]).path).suffix.lower() or ".mp3",
        })
    return sorted(out, key=lambda t: not 60 <= t["duration"] <= 300)  # typical background lengths first


def _clean_title(title: str) -> str:
    return re.sub(r"\s*\((?:ISRC|isrc)[^)]*\)", "", title).strip() or title


def _clean_author(author: str, title: str) -> str:
    """Commons' author field is free text, e.g. "Created by Jason Shaw. Released under Creative Commons..."."""
    a = author.strip()
    if a.lower().startswith(title.lower()):
        a = a[len(title):].strip(" -–:,")
    if m := re.search(r"(?:created|composed|performed|written) by ([^.,;(]+)", a, re.I):
        a = m.group(1)
    a = re.split(r"\b(?:released|licensed|license|https?://)|\s\d{3,}", a, maxsplit=1, flags=re.I)[0]  # or an address
    return a.strip(" -–:,.")[:60]


def add(track: dict) -> str:
    """Download a found track into the library, with its credit; returns its file name."""
    link = track.get("download") or ""
    url = urlparse(link)
    host = (url.hostname or "").lower()
    if url.scheme != "https" or not (host == "jamendo.com" or host.endswith(".jamendo.com")
                                     or host == "upload.wikimedia.org"):
        raise MusicError("That track link is not from Jamendo or Wikimedia Commons")
    ext = track.get("ext") if track.get("ext") in SUFFIXES else ".mp3"
    name = safe_name(f"{track.get('title') or 'track'} - {track.get('author') or 'unknown'}"[:90] + ext)
    path = MUSIC_DIR / name
    if not path.exists():
        part = path.with_suffix(path.suffix + ".part")
        try:
            for attempt in range(2):
                r = requests.get(link, stream=True, timeout=60, headers={"User-Agent": USER_AGENT})
                try:
                    wait = float(r.headers.get("Retry-After") or 5)
                except ValueError:
                    wait = 5.0
                if r.status_code != 429 or attempt or wait > MAX_RETRY_WAIT:
                    break
                r.close()
                cancel.sleep(wait)  # asked to slow down briefly: once more
            with r:
                if r.status_code == 429 and host == "upload.wikimedia.org" and not CONTACT:
                    raise MusicError("Wikimedia Commons only allows music downloads from apps that give a contact: "
                                     "add SHORTCUT_CONTACT=<your email or website> to backend/.env and restart "
                                     "the backend, or use Jamendo")
                r.raise_for_status()
                size = 0
                with part.open("wb") as out:
                    for chunk in r.iter_content(1 << 20):
                        cancel.check()
                        size += len(chunk)
                        if size > MAX_DOWNLOAD:
                            raise MusicError("That track is too large")
                        out.write(chunk)
        except requests.RequestException as e:
            part.unlink(missing_ok=True)
            raise MusicError(f"Could not download the track ({e.__class__.__name__})") from e
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        part.rename(path)
    with _lock:
        credits = _credits()
        credits[name] = {"provider": track.get("provider"), "title": track.get("title"), "author": track.get("author"),
                         "page": track.get("page"), "license": track.get("license"),
                         "license_url": track.get("license_url")}
        _save_credits(credits)
    return name


def auto(mood: str, min_seconds: float) -> str:
    """A track matching the mood, from Jamendo when it has a key, else Commons; added to the library."""
    query = mood.strip() or "calm background"
    tried: list[str] = []
    for source in [s for s, ok in sources().items() if ok]:
        try:
            tracks = search(query, source)
        except MusicError as e:
            tried.append(str(e))
            continue
        tracks = sorted(tracks, key=lambda t: t["duration"] < min(min_seconds, 180))  # long enough first
        for track in tracks[:3]:
            try:
                return add(track)
            except MusicError as e:
                tried.append(str(e))
    raise MusicError(f"No music found for “{query}”" + (f" ({tried[-1]})" if tried else "") +
                     "; pick a track or try another mood")
