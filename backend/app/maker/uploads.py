"""Your own clips and images for the video maker's footage, kept in VIDEOS_DIR/uploads."""

import json
import re
import threading
from pathlib import Path

from PIL import Image

from ..config import VIDEOS_DIR
from ..pipeline import media
from .footage import StockClip

UPLOAD_DIR = VIDEOS_DIR / "uploads"
THUMBS = UPLOAD_DIR / "thumbs"
META = UPLOAD_DIR / ".meta.json"
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_lock = threading.Lock()


class UploadError(RuntimeError):
    pass


def _meta() -> dict[str, dict]:
    try:
        return json.loads(META.read_text())
    except (OSError, ValueError):
        return {}


def safe_name(filename: str) -> str:
    stem, suffix = Path(filename).stem, Path(filename).suffix.lower()
    if suffix not in VIDEO_SUFFIXES | IMAGE_SUFFIXES:
        raise UploadError(f"Unsupported file type {suffix or '(none)'}; use a video "
                          f"({', '.join(sorted(VIDEO_SUFFIXES))}) or an image ({', '.join(sorted(IMAGE_SUFFIXES))})")
    stem = re.sub(r"[^\w .()&'-]+", " ", stem).strip()[:80] or "clip"
    name, n = stem + suffix, 2
    while (UPLOAD_DIR / name).exists():
        name, n = f"{stem} ({n}){suffix}", n + 1
    return name


def path_of(name: str) -> Path:
    path = UPLOAD_DIR / name
    if path.parent != UPLOAD_DIR or not path.is_file():
        raise UploadError(f"Your clip {name!r} is no longer in your uploads")
    return path


def register(path: Path) -> None:
    """Measure a saved upload and make its thumbnail."""
    THUMBS.mkdir(parents=True, exist_ok=True)
    thumb = THUMBS / f"{path.name}.jpg"
    try:
        if path.suffix.lower() in IMAGE_SUFFIXES:
            with Image.open(path) as img:
                width, height = img.size
                small = img.convert("RGB")
                small.thumbnail((640, 640))
                small.save(thumb)
            info = {"kind": "photo", "width": width, "height": height, "duration": 0.0}
        else:
            probe = media.video_info(path)
            media.thumbnail(path, min(1.0, probe["duration"] / 2), thumb, width=640)
            info = {"kind": "video", "duration": probe["duration"], "width": probe["width"], "height": probe["height"]}
    except (OSError, ValueError, media.MediaError) as e:
        path.unlink(missing_ok=True)
        raise UploadError(f"Could not read that file: {str(e).splitlines()[-1] if str(e) else e.__class__.__name__}") from e
    with _lock:
        meta = _meta()
        meta[path.name] = info
        META.write_text(json.dumps(meta, indent=1))


def clips(media_url) -> list[StockClip]:
    meta = _meta()
    out = []
    for path in sorted(UPLOAD_DIR.glob("*"), key=lambda p: -p.stat().st_mtime) if UPLOAD_DIR.exists() else []:
        info = meta.get(path.name)
        if not path.is_file() or info is None:
            continue
        url = media_url(path)
        out.append(StockClip("upload", path.name, url, int(info["width"]), int(info["height"]), float(info["duration"]),
                             "", "", thumb=media_url(THUMBS / f"{path.name}.jpg"),
                             preview=url if info["kind"] == "video" else "", text=path.stem.replace("_", " "),
                             kind=info["kind"], score=1.0))
    return out


def remove(name: str) -> None:
    path_of(name).unlink()
    (THUMBS / f"{name}.jpg").unlink(missing_ok=True)
    with _lock:
        meta = _meta()
        if meta.pop(name, None) is not None:
            META.write_text(json.dumps(meta, indent=1))

