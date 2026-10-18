"""ffmpeg/ffprobe/yt-dlp helpers."""

import json
from collections.abc import Callable
from pathlib import Path

from ..config import FFMPEG, FFPROBE, YT_DLP
from .cancel import run_process


class MediaError(RuntimeError):
    pass


def run(cmd: list[str], on_line: Callable[[str], None] | None = None) -> str:
    proc = run_process(cmd, on_line=on_line)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-8:]
        raise MediaError(f"{Path(cmd[0]).name} failed: " + "\n".join(tail))
    return proc.stdout


def video_info(path: Path) -> dict:
    """Duration and size of a video, with or without sound; also says whether it has audio."""
    out = run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    data = json.loads(out)
    video = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaError("Source has no video stream")
    return {
        "duration": float(data["format"]["duration"]),
        "width": int(video["width"]),
        "height": int(video["height"]),
        "audio": any(s.get("codec_type") == "audio" for s in data["streams"]),
    }


def probe(path: Path) -> dict:
    """A source to clip: it needs speech to transcribe."""
    info = video_info(path)
    if not info.pop("audio"):
        raise MediaError("Source has no audio stream to transcribe")
    return info


PROGRESS_PREFIX = "shortcut-progress "


def download(url: str, dest_dir: Path, on_progress: Callable[[float], None] = lambda f: None) -> Path:
    """Download a video URL to dest_dir/source.mp4 (capped at 1080p).

    Video and audio usually arrive as two files; progress counts the bytes of every file seen so
    far, so it can dip slightly when the (small) audio file starts. It is reported as a running maximum."""
    template = str(dest_dir / "source.%(ext)s")
    files: dict[str, tuple[float, float]] = {}  # filename -> (downloaded, total)
    shown = 0.0

    def on_line(line: str) -> None:
        nonlocal shown
        if not line.startswith(PROGRESS_PREFIX):
            return
        name, done, total, estimate = line[len(PROGRESS_PREFIX):].rstrip("\n").rsplit(" ", 3)
        size = _number(total) or _number(estimate)
        if size:
            files[name] = (_number(done) or 0.0, size)
            fraction = sum(d for d, _ in files.values()) / sum(t for _, t in files.values())
            if fraction > shown:
                shown = min(fraction, 1.0)
                on_progress(shown)

    run([
        YT_DLP, "--no-playlist", "--newline",
        "--progress-template", "download:" + PROGRESS_PREFIX + "%(progress.filename)s %(progress.downloaded_bytes)s "
        "%(progress.total_bytes)s %(progress.total_bytes_estimate)s",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
        "--merge-output-format", "mp4",
        "-o", template, url,
    ], on_line)
    matches = sorted(dest_dir.glob("source.*"))
    if not matches:
        raise MediaError("Download produced no file")
    return matches[0]


def _number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:  # yt-dlp prints "NA" for unknown fields
        return None


def source_info(url: str) -> dict:
    """Title, duration and thumbnail of a link, without downloading the video."""
    out = run([YT_DLP, "--no-playlist", "--skip-download", "--dump-single-json", "--no-warnings", url])
    data = json.loads(out)
    return {
        "title": data.get("title"),
        "duration": data.get("duration"),
        "thumbnail": data.get("thumbnail"),
        "uploader": data.get("uploader") or data.get("channel"),
    }


def extract_audio(src: Path, dest: Path) -> None:
    run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(dest)])


def thumbnail(src: Path, at: float, dest: Path, width: int = 640) -> None:
    run([
        FFMPEG, "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", str(src),
        "-frames:v", "1", "-vf", f"scale={width}:-2", str(dest),
    ])
