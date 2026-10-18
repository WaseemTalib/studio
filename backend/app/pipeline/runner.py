"""Background job runner: ingest -> transcribe -> highlights -> render."""

import logging
import re
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import db
from ..config import PROJECTS_DIR
from ..schemas import ClipRender, LookOptions, ProjectOptions, Transcript, Word
from . import cancel, limits, media
from .highlights import clip_transcript, pick_highlights, whole_video
from .render import render_clip
from .transcribe import transcribe

log = logging.getLogger(__name__)

# Whisper and ffmpeg are CPU-heavy; process one project at a time.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline")
_light_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="light-jobs")

SOURCE_NAME = re.compile(r"source\.(mp4|mov|m4v|mkv|webm|avi)")

# Overall progress share for each stage.
STAGES = {"ingest": (0.0, 0.1), "transcribe": (0.1, 0.5), "highlights": (0.5, 0.6), "render": (0.6, 1.0)}

# Share of the ingest stage spent downloading; probing and the thumbnail take the rest.
DOWNLOAD_SHARE = 0.9

# A stage that finishes this fast reused earlier work (a finished download or transcript);
# its share of progress says nothing about how fast the rest will go.
REUSED_STAGE_SECONDS = 3.0
MIN_ESTIMATE_SECONDS = 5.0


class _Decider:
    """What the user decided about usage limits during one job (see limits.handle)."""

    def __init__(self, job: cancel.Job):
        self.job = job
        self.overrides: dict[str, str] = {}
        self.patient: set[tuple] = set()
        self.paused = 0.0                  # seconds spent waiting on limits, left out of time estimates
        self._lock = threading.Condition()
        self._state: dict | None = None    # what the project page shows
        self._choice: limits.Choice | None = None

    def ask(self, stage: str, limit: limits.Limit, options: list[limits.Option]) -> limits.Choice:
        log.info("%s: %s; asking the user", stage, limit.message)
        choice = self._await("ask", stage, limit, options, None)
        assert choice is not None
        return choice

    def wait(self, stage: str, limit: limits.Limit, options: list[limits.Option]) -> limits.Choice | None:
        """Wait for the limit to free up; the user may still switch meanwhile."""
        left = max(0.0, limit.retry_after - (time.time() - limit.seen_at)) + 1.0
        return self._await("wait", stage, limit, options, time.time() + left)

    def notice(self, limit: limits.Limit) -> None:
        with self._lock:
            self._state = {"mode": "auto_wait", "stage": None, "limit": limit, "options": [],
                           "until": time.time() + limit.retry_after + 0.5, "since": time.time()}

    def clear(self) -> None:
        with self._lock:
            if self._state:
                self.paused += time.time() - self._state["since"]
            self._state = None

    def _await(self, mode: str, stage: str, limit: limits.Limit, options: list[limits.Option],
               until: float | None) -> limits.Choice | None:
        with self._lock:
            self._state = {"mode": mode, "stage": stage, "limit": limit, "options": options, "until": until,
                           "since": time.time()}
            self._choice = None
        try:
            with self._lock:
                while self._choice is None:
                    if self.job.cancelled:
                        raise cancel.Cancelled()
                    if until is not None and time.time() >= until:
                        return None
                    self._lock.wait(0.5)
                return self._choice
        finally:
            self.clear()

    def choose(self, action: str, option: str | None) -> None:
        """From the API: the user's answer to the open question or ongoing wait."""
        with self._lock:
            state = self._state
            if state is None or state["mode"] not in ("ask", "wait"):
                raise ValueError("There is no usage-limit question open for this project")
            if action == "switch":
                allowed = {o.id for o in state["options"] if o.available}
                if option not in allowed:
                    raise ValueError("That option is not available")
            elif action != "wait":
                raise ValueError("Unknown action")
            self._choice = limits.Choice(action, option if action == "switch" else None)
            self._lock.notify_all()

    def public(self) -> dict | None:
        with self._lock:
            state = self._state
            if state is None:
                return None
            return {
                "mode": state["mode"], "stage": state["stage"], "limit": state["limit"].public(),
                "options": [o.public() for o in state["options"]],
                "seconds_left": None if state["until"] is None else max(0.0, state["until"] - time.time()),
            }

    def paused_now(self) -> float:
        with self._lock:
            return self.paused + (time.time() - self._state["since"] if self._state else 0.0)


@dataclass
class _Active:
    """A submitted project job, with the timing that the time-left estimate is based on.

    Importing is bound by the network and the other stages by the CPU and the API, so the pace
    of an import says nothing about the rest. While importing, only the time left for the
    import is estimated; afterwards, later stages are estimated from the pace since the import."""

    stages: dict[str, tuple[float, float]] = field(default_factory=lambda: STAGES)
    record: Callable[..., None] = lambda jid, **fields: None  # saves status/stage/progress/error
    job: cancel.Job = field(default_factory=cancel.Job)
    decider: _Decider = field(init=False)
    started_at: float = 0.0
    stage: str | None = None
    stage_started_at: float = 0.0
    progress: float = 0.0
    work_started_at: float | None = None  # when the first stage after importing began
    work_start_progress: float = 0.0
    reused: float = 0.0  # progress after importing that came from reused work rather than this run

    def __post_init__(self) -> None:
        self.decider = _Decider(self.job)

    def clock(self) -> float:
        """Time that stops while the job waits on a usage limit, so waits don't skew estimates."""
        return time.time() - self.decider.paused_now()

    def enter(self, stage: str, progress: float, now: float) -> None:
        if self.stage != stage:
            if self.work_started_at is None and stage != "ingest":
                self.work_started_at, self.work_start_progress = now, progress
            elif self.stage not in (None, "ingest") and now - self.stage_started_at < REUSED_STAGE_SECONDS:
                self.reused += progress - self.stages[self.stage][0]
            self.stage, self.stage_started_at = stage, now
        self.progress = progress

    def estimate(self, now: float) -> tuple[float, str] | None:
        """(seconds left, "import" or "total"), or None until enough has been measured.
        The current stage runs at its own measured pace, later stages at the pace since importing."""
        if self.stage is None:
            return None
        lo, hi = self.stages[self.stage]
        in_stage = now - self.stage_started_at
        fraction = (self.progress - lo) / (hi - lo)
        stage_left = in_stage * (1 - fraction) / fraction if fraction >= 0.05 and in_stage >= MIN_ESTIMATE_SECONDS else None
        if self.stage == "ingest":
            return None if stage_left is None else (stage_left, "import")

        rate = None
        if self.work_started_at is not None:
            elapsed = now - self.work_started_at
            earned = self.progress - self.work_start_progress - self.reused
            if elapsed >= MIN_ESTIMATE_SECONDS and earned >= 0.01:
                rate = earned / elapsed
        if stage_left is None:
            if rate is None:
                return None
            stage_left = (hi - self.progress) / rate
        if hi >= 1.0:
            return stage_left, "total"
        return None if rate is None else (stage_left + (1.0 - hi) / rate, "total")


_active: dict[str, _Active] = {}
_active_lock = threading.Lock()


def project_dir(pid: str) -> Path:
    return PROJECTS_DIR / pid


def media_url(path: Path) -> str:
    return "/media/" + path.relative_to(PROJECTS_DIR).as_posix()


def submit(pid: str, source_url: str | None) -> None:
    submit_job(pid, lambda: _process(pid, source_url), STAGES, db.update_project)


def submit_job(jid: str, process: Callable[[], Any], stages: dict[str, tuple[float, float]],
               record: Callable[..., None], light: bool = False) -> None:
    """Queue a job (a project, a video-maker video, a stickman plan) on the shared worker. `record(jid, **fields)`
    saves its status; process() reports progress through stage(). Light jobs (AI requests only, no
    rendering) run on their own small pool so they never wait behind a render."""
    active = _Active(stages=stages, record=record)
    with _active_lock:
        _active[jid] = active
    (_light_executor if light else _executor).submit(_run, jid, process, active)


def cancel_project(pid: str) -> bool:
    """Stop a queued or running job. False if this server has no job for it."""
    with _active_lock:
        active = _active.get(pid)
    if active is None:
        return False
    if not active.job.cancel():  # still queued: it will never start, so record the outcome here
        active.record(pid, status="cancelled", stage=None, error=None)
    return True


def live_status(pid: str) -> dict:
    """In-memory job state the database does not hold: cancellation in progress, time left, and
    a usage limit the job is waiting on or asking about."""
    with _active_lock:
        active = _active.get(pid)
    if active is None:
        return {"cancelling": False, "eta_seconds": None, "eta_scope": None, "limit": None}
    limit = active.decider.public()
    estimate = None if active.job.cancelled or limit else active.estimate(active.clock())
    return {"cancelling": active.job.cancelled,
            "eta_seconds": None if estimate is None else round(estimate[0]),
            "eta_scope": None if estimate is None else estimate[1],
            "limit": limit}


def choose_on_limit(pid: str, action: str, option: str | None) -> None:
    """The user's answer when a project waits on a usage limit: "wait" or "switch" to `option`."""
    with _active_lock:
        active = _active.get(pid)
    if active is None:
        raise ValueError("This project is not running")
    active.decider.choose(action, option)


def stage(jid: str, name: str, fraction: float = 0.0) -> None:
    """Report a running job's stage and how far into it it is (0..1)."""
    cancel.check()
    with _active_lock:
        active = _active.get(jid)
    if active is None:
        return
    lo, hi = active.stages[name]
    progress = round(lo + (hi - lo) * fraction, 4)
    active.enter(name, progress, active.clock())
    active.record(jid, status="processing", stage=name, progress=progress)



def _run(pid: str, process: Callable[[], Any], active: _Active) -> None:
    try:
        if not active.job.start():
            return
        active.started_at = time.time()
        with cancel.bound(active.job), limits.bound(active.decider):
            process()
    except Exception as e:  # surface every failure on the job instead of losing it in a thread
        if active.job.cancelled:  # includes errors from requests and processes cut off by the cancel
            log.info("job %s cancelled", pid)
            active.record(pid, status="cancelled", stage=None, error=None)
        else:
            log.error("job %s failed:\n%s", pid, traceback.format_exc())
            active.record(pid, status="failed", error=str(e) or e.__class__.__name__)
    finally:
        with _active_lock:
            if _active.get(pid) is active:
                del _active[pid]


def _process(pid: str, source_url: str | None) -> None:
    project = db.get_project(pid)
    if project is None:
        return
    options = ProjectOptions(**project["options"])
    work = project_dir(pid)

    # A retry reuses the finished download and transcript; partial yt-dlp files never match.
    stage(pid, "ingest")
    src = find_source(pid)
    if src is None:
        if not source_url:
            raise media.MediaError("Uploaded source file is missing")
        src = media.download(source_url, work, lambda f: stage(pid, "ingest", DOWNLOAD_SHARE * f))
    info = media.probe(src)
    thumb = work / "thumbnail.jpg"
    media.thumbnail(src, min(info["duration"] * 0.1, 5.0), thumb)
    db.update_project(pid, duration=info["duration"], thumbnail=media_url(thumb))

    stage(pid, "transcribe")
    transcript = load_transcript(pid)
    if transcript is not None and transcript.duration < info["duration"]:
        transcript.duration = info["duration"]  # imported subtitles end before the video does
    if transcript is None:
        audio = work / "audio.wav"
        media.extract_audio(src, audio)
        transcript = transcribe(audio, info["duration"], options.language,
                                lambda f: stage(pid, "transcribe", f))
        (work / "transcript.json").write_text(transcript.model_dump_json())
        audio.unlink(missing_ok=True)

    stage(pid, "highlights")
    if options.mode == "full":
        highlights = [whole_video(transcript, options, project["title"])]
    else:
        highlights = pick_highlights(transcript, options, lambda f: stage(pid, "highlights", f))

    clips_dir = work / "clips"
    shutil.rmtree(clips_dir, ignore_errors=True)
    clips_dir.mkdir()
    look = LookOptions(**options.model_dump(include=set(LookOptions.model_fields)))
    words = transcript_words(transcript)
    clips = []
    for i, h in enumerate(highlights):
        stage(pid, "render", i / len(highlights))
        cid = db.new_id()
        video, cover, layout = _render_files(src, (info["width"], info["height"]), words, h.start, h.end,
                                             look, h.title, clips_dir, cid, version=1)
        clips.append({
            "id": cid, "rank": i, "title": h.title, "topic": h.topic, "hook": h.hook, "reason": h.reason,
            "score": h.score, "hashtags": h.hashtags, "start": h.start, "end": h.end,
            "transcript": clip_transcript(transcript, h.start, h.end),
            "video": media_url(video), "thumbnail": media_url(cover),
            "look": look.model_dump(), "layout": layout,
        })
    cancel.check()
    db.replace_clips(pid, clips)
    db.update_project(pid, status="done", stage=None, progress=1.0, error=None)


def _render_files(src: Path, src_size: tuple[int, int], words: list[Word], start: float, end: float,
                  look: LookOptions, title: str, clips_dir: Path, cid: str, version: int) -> tuple[Path, Path, str]:
    # Versioned file names: the editor shows the new render while the old file can still be served.
    video = clips_dir / f"{cid}_v{version}.mp4"
    layout = render_clip(src, src_size, words, start, end, look, title, video)
    shutil.rmtree(clips_dir / f"{video.stem}_overlay", ignore_errors=True)
    cover = clips_dir / f"{cid}_v{version}.jpg"
    media.thumbnail(video, min(1.0, (end - start) / 2), cover, width=480)
    return video, cover, layout


def transcript_words(transcript: Transcript) -> list[Word]:
    return [w for s in transcript.segments for w in s.words]


def find_source(pid: str) -> Path | None:
    return next((p for p in project_dir(pid).glob("source.*") if SOURCE_NAME.fullmatch(p.name)), None)


def submit_rerender(cid: str, request: ClipRender) -> None:
    _executor.submit(_rerender, cid, request)


def _rerender(cid: str, request: ClipRender) -> None:
    clip = db.get_clip(cid)
    if clip is None:
        return
    try:
        pid = clip["project_id"]
        src = find_source(pid)
        transcript = load_transcript(pid)
        if src is None or transcript is None:
            raise media.MediaError("Project source video or transcript is missing")
        info = media.probe(src)
        end = min(request.end, info["duration"])
        words = request.words if request.words is not None else transcript_words(transcript)
        version = clip["version"] + 1
        old = [clip["video"], clip["thumbnail"]]
        video, cover, layout = _render_files(src, (info["width"], info["height"]), words, request.start, end,
                                             request.look, request.title, project_dir(pid) / "clips", cid, version)
        db.update_clip(
            cid, status="ready", error=None, version=version, start=request.start, end=end,
            title=request.title, look=request.look.model_dump(), layout=layout,
            words=[w.model_dump() for w in request.words] if request.words is not None else None,
            transcript=" ".join(w.text for w in words if w.start >= request.start - 0.2 and w.end <= end + 0.2),
            video=media_url(video), thumbnail=media_url(cover),
        )
        for url in old:
            if url:
                (PROJECTS_DIR / url.removeprefix("/media/")).unlink(missing_ok=True)
    except Exception as e:
        log.error("clip %s re-render failed:\n%s", cid, traceback.format_exc())
        db.update_clip(cid, status="failed", error=str(e) or e.__class__.__name__)


def load_transcript(pid: str) -> Transcript | None:
    path = project_dir(pid) / "transcript.json"
    return Transcript.model_validate_json(path.read_text()) if path.exists() else None
