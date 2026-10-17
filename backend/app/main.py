"""HTTP API for Shortcut Studio."""

import io
import logging
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import db, presets, tts
from .config import MUSIC_DIR, PROJECTS_DIR, VIDEOS_DIR, VOICE_DIR
from .maker import footage, make, script
from .pipeline import limits, media, render, runner
from .pipeline.highlights import HighlightError, default_service, highlight_options
from .pipeline.srt import SrtError, parse_srt
from .schemas import (SOURCE_SPECIFIC_OPTIONS, AddLibraryVoice, ClipRender, ClipUpdate, DraftRequest, LimitChoice,
                      ProjectOptions, ProjectUpdate, SourceInfoRequest, SpeechRequest, TemplateIn, VideoRequest)

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Shortcut Studio API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/media", StaticFiles(directory=PROJECTS_DIR), name="media")
app.mount("/voice-media", StaticFiles(directory=VOICE_DIR), name="voice-media")
app.mount("/video-media", StaticFiles(directory=VIDEOS_DIR), name="video-media")

ALLOWED_UPLOAD_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}


@app.on_event("startup")
def _startup() -> None:
    db.init()


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/presets")
def get_presets() -> dict:
    return presets.as_json()


@app.get("/api/caption-preview.png")
def caption_preview(
    style: str = "karaoke",
    color: str = "#facc15",
    position: Literal["top", "middle", "bottom"] = "bottom",
    aspect: Literal["9:16", "1:1", "16:9"] = "9:16",
    title: bool = False,
) -> Response:
    if style != presets.NO_CAPTIONS and style not in presets.CAPTION_STYLES:
        raise HTTPException(404, "Unknown caption style")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise HTTPException(422, "color must be #rrggbb")
    img = render.preview(style, color, position, aspect, "Your clip title appears here" if title else None)
    img.thumbnail((360, 640))
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


def _live(project: dict | None) -> dict | None:
    """Add the running job's state: whether a cancel is in progress and the estimated seconds left."""
    return None if project is None else {**project, **runner.live_status(project["id"])}


@app.get("/api/projects")
def list_projects() -> list[dict]:
    return [_live(p) for p in db.list_projects()]


@app.post("/api/source-info")
def get_source_info(body: SourceInfoRequest) -> dict:
    """Title and duration of a link, so the timeframe can be chosen before submitting."""
    parsed = urlparse(body.url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(422, "URL must be an http(s) link")
    try:
        return media.source_info(body.url.strip())
    except (media.MediaError, ValueError) as e:
        raise HTTPException(422, f"Could not read this link: {str(e).splitlines()[-1]}") from e


@app.get("/api/templates")
def list_templates() -> list[dict]:
    return db.list_templates()


@app.post("/api/templates", status_code=201)
def save_template(body: TemplateIn) -> list[dict]:
    options = body.options.model_dump(exclude=SOURCE_SPECIFIC_OPTIONS)
    db.save_template(body.name.strip(), options, body.is_default)
    return db.list_templates()


@app.delete("/api/templates/{tid}", status_code=204)
def delete_template(tid: str) -> None:
    if not db.delete_template(tid):
        raise HTTPException(404, "Template not found")


@app.post("/api/projects", status_code=201)
async def create_project(
    options: Annotated[str, Form()] = "{}",
    url: Annotated[str | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
    title: Annotated[str | None, Form()] = None,
    srt: Annotated[UploadFile | None, File()] = None,
) -> dict:
    try:
        opts = ProjectOptions.model_validate_json(options)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False)) from e

    url = (url or "").strip() or None
    if bool(url) == bool(file):
        raise HTTPException(422, "Provide exactly one of: url, file")

    imported = None
    if srt is not None:
        try:
            imported = parse_srt((await srt.read()).decode("utf-8", errors="replace"), opts.language)
        except SrtError as e:
            raise HTTPException(422, str(e)) from e

    pid = db.new_id()
    work = runner.project_dir(pid)
    work.mkdir(parents=True)

    if url:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            shutil.rmtree(work)
            raise HTTPException(422, "URL must be an http(s) link")
        default_title, source_type, source = parsed.netloc + parsed.path, "url", url
    else:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in ALLOWED_UPLOAD_SUFFIXES:
            shutil.rmtree(work)
            raise HTTPException(422, f"Unsupported file type {suffix or '(none)'}")
        with (work / f"source{suffix}").open("wb") as out:
            shutil.copyfileobj(file.file, out, length=8 * 1024 * 1024)
        default_title = re.sub(r"\.[^.]+$", "", file.filename or "Untitled")
        source_type, source = "upload", file.filename or ""

    if imported is not None:
        (work / "transcript.json").write_text(imported.model_dump_json())
    title = (title or "").strip() or default_title
    db.create_project(pid, title[:120], source_type, source, opts.model_dump())
    runner.submit(pid, url)
    return _live(db.get_project(pid))


@app.get("/api/projects/{pid}")
def get_project(pid: str) -> dict:
    project = db.get_project(pid)
    if project is None:
        raise HTTPException(404, "Project not found")
    return _live(project)


@app.patch("/api/projects/{pid}")
def rename_project(pid: str, body: ProjectUpdate) -> dict:
    if db.get_project(pid) is None:
        raise HTTPException(404, "Project not found")
    db.update_project(pid, title=body.title.strip())
    return _live(db.get_project(pid))


def _safe_name(text: str, fallback: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", text).strip().strip(".")
    return re.sub(r"\s+", " ", name)[:80] or fallback


@app.get("/api/projects/{pid}/download")
def download_all(pid: str) -> FileResponse:
    """Every ready clip as one zip, numbered by score, plus a text file of titles and hashtags."""
    project = db.get_project(pid)
    if project is None:
        raise HTTPException(404, "Project not found")
    clips = [c for c in db.list_clips(pid) if c["status"] != "rendering" and c["video"]]
    if not clips:
        raise HTTPException(404, "This project has no clips to download yet")

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    notes = []
    used: set[str] = set()
    # MP4 is already compressed; storing avoids burning CPU for no size gain.
    with zipfile.ZipFile(tmp.name, "w", compression=zipfile.ZIP_STORED) as zf:
        for n, clip in enumerate(clips, 1):
            path = PROJECTS_DIR / clip["video"].removeprefix("/media/")
            if not path.exists():
                continue
            name = f"{n:02d} - {_safe_name(clip['title'], f'clip {n}')}.mp4"
            while name in used:
                name = name.replace(".mp4", " (2).mp4")
            used.add(name)
            zf.write(path, name)
            tags = " ".join(f"#{h}" for h in clip["hashtags"])
            notes.append(f"{name}\n{clip['title']}\n{tags}\nScore {clip['score']} · "
                         f"{clip['start']:.1f}s–{clip['end']:.1f}s of the source\n")
        zf.writestr("captions.txt", "\n".join(notes))
    filename = f"{_safe_name(project['title'], 'clips')}.zip"
    return FileResponse(tmp.name, media_type="application/zip", filename=filename,
                        background=BackgroundTask(os.unlink, tmp.name))


@app.post("/api/projects/{pid}/retry")
def retry_project(pid: str) -> dict:
    project = db.get_project(pid)
    if project is None:
        raise HTTPException(404, "Project not found")
    if project["status"] not in ("failed", "cancelled"):
        raise HTTPException(409, "Only failed or cancelled projects can be retried")
    db.update_project(pid, status="queued", stage=None, progress=0, error=None)
    runner.submit(pid, project["source"] if project["source_type"] == "url" else None)
    return _live(db.get_project(pid))


@app.post("/api/projects/{pid}/cancel")
def cancel_project(pid: str) -> dict:
    """Stop processing. The download and transcript are kept, so a retry resumes from there."""
    project = db.get_project(pid)
    if project is None:
        raise HTTPException(404, "Project not found")
    if project["status"] not in ("queued", "processing"):
        raise HTTPException(409, "Only queued or processing projects can be cancelled")
    if not runner.cancel_project(pid):  # no job on this server for it: nothing left to stop
        db.mark_cancelled(pid)
    return _live(db.get_project(pid))


@app.post("/api/projects/{pid}/limit-choice")
def limit_choice(pid: str, body: LimitChoice) -> dict:
    """Answer a usage-limit question: wait for the reset, or switch to another service."""
    if db.get_project(pid) is None:
        raise HTTPException(404, "Project not found")
    try:
        runner.choose_on_limit(pid, body.action, body.option)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return _live(db.get_project(pid))


@app.delete("/api/projects/{pid}", status_code=204)
def delete_project(pid: str) -> None:
    project = db.get_project(pid)
    if project is None:
        raise HTTPException(404, "Project not found")
    if project["status"] in ("queued", "processing"):
        raise HTTPException(409, "Project is still processing")
    db.delete_project(pid)
    shutil.rmtree(runner.project_dir(pid), ignore_errors=True)


@app.get("/api/projects/{pid}/clips")
def list_clips(pid: str) -> list[dict]:
    if db.get_project(pid) is None:
        raise HTTPException(404, "Project not found")
    return db.list_clips(pid)


@app.get("/api/projects/{pid}/transcript")
def get_transcript(pid: str) -> dict:
    transcript = runner.load_transcript(pid)
    if transcript is None:
        raise HTTPException(404, "Transcript not available yet")
    return transcript.model_dump()


EDITOR_CONTEXT_SECONDS = 90


@app.get("/api/clips/{cid}")
def get_clip(cid: str) -> dict:
    """Clip plus the transcript words around it, for trimming and caption editing."""
    clip = db.get_clip(cid)
    if clip is None:
        raise HTTPException(404, "Clip not found")
    project = db.get_project(clip["project_id"])
    transcript = runner.load_transcript(clip["project_id"])
    lo, hi = clip["start"] - EDITOR_CONTEXT_SECONDS, clip["end"] + EDITOR_CONTEXT_SECONDS
    context = [w.model_dump() for w in runner.transcript_words(transcript) if lo <= w.start <= hi] if transcript else []
    source = runner.find_source(clip["project_id"])
    return {**clip, "project": project, "context_words": context,
            "source_video": runner.media_url(source) if source else None}


@app.post("/api/clips/{cid}/render", status_code=202)
def rerender_clip(cid: str, body: ClipRender) -> dict:
    clip = db.get_clip(cid)
    if clip is None:
        raise HTTPException(404, "Clip not found")
    if clip["status"] == "rendering":
        raise HTTPException(409, "Clip is already rendering")
    project = db.get_project(clip["project_id"])
    if project["duration"] is not None and body.start >= project["duration"]:
        raise HTTPException(422, "Clip starts after the end of the video")
    db.update_clip(cid, status="rendering", error=None)
    runner.submit_rerender(cid, body)
    return db.get_clip(cid)


@app.patch("/api/clips/{cid}")
def update_clip(cid: str, body: ClipUpdate) -> dict:
    clip = db.update_clip(cid, **body.model_dump(exclude_none=True))
    if clip is None:
        raise HTTPException(404, "Clip not found")
    return clip


# --- Voice Studio -----------------------------------------------------------------


def _tts(call, *args):
    try:
        return call(*args)
    except tts.TTSError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/tts/providers")
def tts_providers() -> list[dict]:
    return tts.providers()


@app.get("/api/tts/models")
def tts_models(provider: Literal["elevenlabs", "local"]) -> list[dict]:
    return _tts(tts.models, provider)


@app.get("/api/tts/voices")
def tts_voices(
    provider: Literal["elevenlabs", "local"],
    source: Literal["default", "mine", "library"] = "default",
    search: str | None = None,
    gender: str | None = None,
    age: str | None = None,
    accent: str | None = None,
    language: str | None = None,
    use_case: str | None = None,
    category: Literal["professional", "famous", "high_quality"] | None = None,
    sort: Literal["cloned_by_count", "trending", "usage_character_count_1y", "created_date"] | None = None,
    page: str | None = None,
) -> dict:
    filters = {"search": search, "gender": gender, "age": age, "accent": accent, "language": language,
               "use_case": use_case, "category": category, "sort": sort}
    return _tts(tts.voices, provider, source, filters, page)


@app.get("/api/tts/voices/local/{voice_id}/preview")
def tts_local_preview(voice_id: str) -> FileResponse:
    if voice_id not in tts.local.VOICES:
        raise HTTPException(404, "Unknown voice")
    return FileResponse(_tts(tts.local_sample, voice_id), media_type="audio/mpeg")


@app.post("/api/tts/voices/library")
def tts_add_library_voice(body: AddLibraryVoice) -> dict:
    """Add a community library voice to the user's ElevenLabs account so it can speak."""
    return {"voice_id": _tts(tts.elevenlabs.add_shared_voice, body.public_owner_id, body.voice_id, body.name)}


@app.get("/api/tts/usage")
def tts_usage() -> dict | None:
    return _tts(tts.elevenlabs.usage) if tts.elevenlabs.available() else None


@app.post("/api/tts/local/download")
def tts_local_download() -> dict:
    return tts.local.start_download()


@app.post("/api/tts/generate", status_code=201)
def tts_generate(body: SpeechRequest) -> dict:
    return _tts(tts.generate, body.provider, body.voice_id, body.voice_name, body.model_id, body.text,
                body.settings.model_dump())


@app.get("/api/tts/history")
def tts_history() -> list[dict]:
    return tts.history()


@app.delete("/api/tts/history/{gid}", status_code=204)
def tts_delete(gid: str) -> None:
    if not tts.delete(gid):
        raise HTTPException(404, "Generation not found")


# --- Video maker -----------------------------------------------------------------------


@app.get("/api/maker/options")
def maker_options() -> dict:
    """What the video maker can use: script-writing services, stock footage sites and music."""
    try:
        default = default_service()
    except HighlightError:
        default = None
    services = [o.public() for o in highlight_options("")]
    return {"services": services, "default_service": default, "footage": footage.available(),
            "music": make.music_library()}


@app.post("/api/maker/draft")
def maker_draft(body: DraftRequest) -> dict:
    """Write the script (or split and tag the user's). Shown for review before the video is made.
    When the service hits a usage limit, answers 429 with the limit and the other services."""
    try:
        return script.draft(body)
    except limits.LimitReached as e:
        current = body.service or default_service()
        raise HTTPException(429, {"message": str(e), "limit": e.limit.public(),
                                  "options": [o.public() for o in highlight_options(current)]}) from e
    except HighlightError as e:
        raise HTTPException(400, str(e)) from e


def _video_live(video: dict | None) -> dict | None:
    return None if video is None else {**video, **runner.live_status(video["id"])}


def _video_or_404(vid: str) -> dict:
    video = db.get_video(vid)
    if video is None:
        raise HTTPException(404, "Video not found")
    return video


@app.get("/api/maker/videos")
def maker_videos() -> list[dict]:
    return [_video_live(v) for v in db.list_videos()]


@app.post("/api/maker/videos", status_code=201)
def maker_create(body: VideoRequest) -> dict:
    if body.music:
        try:
            make._music(body.music)
        except media.MediaError as e:
            raise HTTPException(422, str(e)) from e
    try:
        footage.providers_for(body.footage)
    except footage.FootageError as e:
        raise HTTPException(422, str(e)) from e
    vid = db.new_id()
    db.create_video(vid, body.title.strip(), body.model_dump())
    make.submit(vid)
    return _video_live(db.get_video(vid))


@app.get("/api/maker/videos/{vid}")
def maker_video(vid: str) -> dict:
    return _video_live(_video_or_404(vid))


@app.post("/api/maker/videos/{vid}/cancel")
def maker_cancel(vid: str) -> dict:
    video = _video_or_404(vid)
    if video["status"] not in ("queued", "processing"):
        raise HTTPException(409, "Only queued or processing videos can be cancelled")
    if not runner.cancel_project(vid):
        db.mark_video_cancelled(vid)
    return _video_live(db.get_video(vid))


@app.post("/api/maker/videos/{vid}/retry")
def maker_retry(vid: str) -> dict:
    """Make the video again; narration already generated is reused."""
    video = _video_or_404(vid)
    if video["status"] in ("queued", "processing"):
        raise HTTPException(409, "This video is still being made")
    db.update_video(vid, status="queued", stage=None, progress=0, error=None)
    make.submit(vid)
    return _video_live(db.get_video(vid))


@app.post("/api/maker/videos/{vid}/limit-choice")
def maker_limit_choice(vid: str, body: LimitChoice) -> dict:
    _video_or_404(vid)
    try:
        runner.choose_on_limit(vid, body.action, body.option)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return _video_live(db.get_video(vid))


@app.delete("/api/maker/videos/{vid}", status_code=204)
def maker_delete(vid: str) -> None:
    video = _video_or_404(vid)
    if video["status"] in ("queued", "processing"):
        raise HTTPException(409, "This video is still being made")
    db.delete_video(vid)
    shutil.rmtree(make.video_dir(vid), ignore_errors=True)


@app.get("/api/maker/music")
def maker_music() -> list[dict]:
    return make.music_library()


@app.post("/api/maker/music", status_code=201)
async def maker_music_upload(file: Annotated[UploadFile, File()]) -> list[dict]:
    try:
        name = make.music_name(file.filename or "")
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    with (MUSIC_DIR / name).open("wb") as out:
        shutil.copyfileobj(file.file, out, length=4 * 1024 * 1024)
    return make.music_library()


@app.delete("/api/maker/music/{name}", status_code=204)
def maker_music_delete(name: str) -> None:
    path = MUSIC_DIR / name
    if path.parent != MUSIC_DIR or not path.is_file():
        raise HTTPException(404, "Track not found")
    path.unlink()
