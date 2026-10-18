"""The picture track of a video-maker video: which shot shows what, and each shot as a small mp4.

Each scene opens with its cards (a key fact, a quote) when it has them, then cuts its footage into
shots no longer than clip_seconds. Footage is, in order of preference:
1. the clips chosen in the footage review, in their order;
2. a photo of the scene's named place (Wikimedia Commons), for its first footage shot;
3. stock clips that match the scene's searches, taking the searches in the order the narration
   mentions them so each shot matches what is said at that moment;
4. a matching clip already used in this scene or a neighbouring one (from another point in it);
5. a plain coloured background. A clip that does not match is never used.
"""

import logging
import math
import random
import shutil
import textwrap
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict, Field

from ..config import FFMPEG, FFPROBE
from ..pipeline import cancel, limits, media
from ..presets import APP_FONT_DIR
from ..schemas import FootagePlanRequest, Scene, footage_sources
from . import footage, uploads
from .footage import RELEVANT, StockClip
from .script import WORDS_PER_MINUTE, fallback_terms

log = logging.getLogger(__name__)

FPS = 30
FADE = 0.3              # fade between scenes
CARD_SECONDS = 3.2
PARALLEL_SHOTS = 3      # shots fetched and encoded at once; mostly waiting on the network
SEARCH_THREADS = 4
CANDIDATES = 24         # suggestions per scene in the footage review
VET_CLIPS = 16          # matching clips per scene the AI checks against the narration
VET_SCENES = 6          # scenes per AI request

VET_SYSTEM = """You check stock footage for a narrated news or documentary video.
For each scene you get the narration and numbered stock clips, each described by the tags its site gave it.
Keep a clip only if what it shows could plausibly be on screen while that narration is spoken. Reject clips about \
a different subject that merely share a word with the narration (a fitness clip for "body", a halloween skull for \
"crime", a cooking clip for "reaction"), and cartoons or obviously staged ads. Always reject graphic material: \
violence being shown, injured or dead people, weapons fired at people. Prefer real-world footage of the people, \
places, objects and actions the narration describes."""


class _SceneKeep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene: int = Field(description="The scene number")
    keep: list[int] = Field(description="Numbers of the clips that fit this scene")


class _Vetting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenes: list[_SceneKeep]
# Plain-background colours, one pair per scene in turn.
GRADIENTS = [("0x1e3a8a", "0x0f172a"), ("0x4c1d95", "0x111827"), ("0x065f46", "0x0f172a"),
             ("0x9a3412", "0x1c1917"), ("0x155e75", "0x0f172a"), ("0x831843", "0x18181b")]


@dataclass
class Span:
    start: float        # in the narration
    spoken: float       # seconds of speech
    end: float          # start of the next scene


@dataclass
class Shot:
    scene: int
    start: float
    frames: int
    fade_in: bool
    fade_out: bool
    kind: str = "footage"   # footage | card | quote
    n: int = 0              # footage shots: this one's number in its scene, and how many there are
    of: int = 1

    @property
    def seconds(self) -> float:
        return self.frames / FPS


def plan(scenes: list[Scene], spans: list[Span], clip_seconds: float, cards: bool, fade: bool) -> list[Shot]:
    """Cards, then footage shots no longer than clip_seconds, per scene. Counted in whole frames
    from the start of the video, so the picture never drifts from the narration."""
    times: list[tuple[int, float, float, str, int, int]] = []  # scene, start, end, kind, n, of
    for i, (scene, span) in enumerate(zip(scenes, spans)):
        length = span.end - span.start
        kinds = [k for k, text in (("card", scene.card_text), ("quote", scene.quote)) if cards and text.strip()]
        card_len = min(CARD_SECONDS, length * 0.45)
        kinds = kinds[:int(length * 0.6 // card_len)] if card_len >= 1.5 else []
        t = span.start
        for kind in kinds:
            times.append((i, t, t + card_len, kind, 0, 1))
            t += card_len
        rest = span.end - t
        n = max(1, math.ceil(rest / clip_seconds - 0.15))
        for k in range(n):
            times.append((i, t + rest * k / n, t + rest * (k + 1) / n, "footage", k, n))
    shots = []
    for idx, (i, t0, t1, kind, k, n) in enumerate(times):
        frames = round(t1 * FPS) - round(t0 * FPS)
        if frames <= 0:
            continue
        first = idx == 0 or times[idx - 1][0] != i
        last = idx == len(times) - 1 or times[idx + 1][0] != i
        shots.append(Shot(i, t0, frames, fade and first, fade and last and i < len(scenes) - 1, kind, k, n))
    return shots


def estimated_spans(scenes: list[Scene], speed: float, gap: float, tail: float) -> list[Span]:
    """Scene timings before any narration exists, from word counts."""
    spans, t = [], 0.0
    for i, s in enumerate(scenes):
        spoken = max(1.0, len(s.text.split()) / WORDS_PER_MINUTE * 60 / speed)
        end = t + spoken + (tail if i == len(scenes) - 1 else gap)
        spans.append(Span(t, spoken, end))
        t = end
    return spans


# --- Choosing footage --------------------------------------------------------------------


class Picker:
    """Finds matching stock media for shots; see the module docstring for the order of preference."""

    def __init__(self, providers: list[str], orientation: str, short_side: int, photos: bool,
                 service: str | None = None):
        self.providers = providers
        self.service = service  # the AI that checks footage; None uses the default
        self.orientation = orientation
        self.short_side = short_side
        self.photos = photos
        self.used: set[str] = set()
        self.scene_used: dict[int, list[StockClip]] = {}
        self.rejected: dict[int, set[str]] = {}  # per scene: clips the AI check found off-topic
        self.approved: dict[int, set[str]] = {}  # per scene, once checked: the only clips it may use

    def search(self, provider: str, term: str, page: int = 1) -> list[StockClip]:
        while True:
            try:
                return footage.search(provider, term, self.orientation, self.short_side, page)
            except limits.LimitReached as e:
                if not self._on_limit(provider, e.limit):
                    return []
            except footage.FootageError as e:
                if provider != "wikimedia":
                    raise
                log.warning("photo search failed: %s", e)  # photos are optional
                return []

    def _on_limit(self, provider: str, limit: limits.Limit) -> bool:
        """Wait or switch as the user decides; False if this provider is no longer used."""
        if provider == "wikimedia":
            return False
        options = [limits.Option(f"{p}:stock", f"{limits.SERVICE_LABELS[p]} stock video",
                                 "Free stock video; has its own limits.", footage.available()[p],
                                 None if footage.available()[p] else
                                 f"Add {footage.KEY_NAME[p]} to backend/.env (free key at {footage.SIGNUP[p]})")
                   for p in ("pexels", "pixabay") if p != provider]
        options.append(limits.Option("none:plain", "Plain background",
                                     "Coloured background for the remaining shots; no limits.", True))
        try:
            choice = limits.handle("footage", limit, options)
        except limits.LimitReached as again:  # nobody to ask, e.g. the footage review
            raise footage.FootageError(str(again)) from again
        if choice is None:
            return True
        service = choice.partition(":")[0]
        self.providers = [] if service == "none" else [service]
        return False

    def prefetch(self, scenes: list[Scene]) -> None:
        """Run every scene's searches at once; picking then reads them from the cache."""
        jobs = [(p, t) for s in scenes for t in terms_of(s) for p in self.providers]
        jobs += [("wikimedia", s.place) for s in scenes if self.photos and s.place.strip()]
        job = cancel.current()

        def run(item: tuple[str, str]) -> None:
            with cancel.bound(job) if job else nullcontext():
                try:
                    footage.search(item[0], item[1], self.orientation, self.short_side)
                except (limits.LimitReached, footage.FootageError):
                    pass  # met again, and handled, when picking
        with ThreadPoolExecutor(max_workers=SEARCH_THREADS) as pool:
            list(pool.map(run, dict.fromkeys(jobs)))

    def place_photo(self, scene_no: int, scene: Scene) -> StockClip | None:
        if not (self.photos and scene.place.strip()):
            return None
        for photo in self.search("wikimedia", scene.place)[:3]:
            if photo.score >= RELEVANT and photo.key not in self.used and self.allowed(scene_no, photo):
                return photo
        return None

    def pick(self, scene_no: int, scene: Scene, shot: Shot) -> StockClip | None:
        terms = terms_of(scene)
        first = int(shot.n * len(terms) / max(1, shot.of)) if terms else 0
        ordered = terms[first:] + terms[:first]
        for term in ordered:
            for clip in self.merged(term):
                if (clip.score >= RELEVANT and clip.long_enough(shot.seconds) and clip.key not in self.used
                        and self.allowed(scene_no, clip)):
                    return self.take(scene_no, clip)
        # The same subject again from another moment: this scene's clips, then the neighbouring scenes'.
        for near in (scene_no, scene_no - 1, scene_no + 1):
            for clip in self.scene_used.get(near, []):
                if clip.long_enough(shot.seconds):
                    return clip
        return None

    def allowed(self, scene_no: int, clip: StockClip) -> bool:
        """Not rejected by the AI check; and when the scene was checked, among the clips it approved."""
        if scene_no in self.approved:
            return clip.key in self.approved[scene_no]
        return clip.key not in self.rejected.get(scene_no, ())

    def merged(self, term: str) -> list[StockClip]:
        """Every source's results for a search, best match first; video before photos, then source order."""
        found = [(n, c) for p in list(self.providers) for n, c in enumerate(self.search(p, term))]
        rank = {p: i for i, p in enumerate(footage.SOURCES)}
        return [c for _, c in sorted(found, key=lambda nc: (-nc[1].score, nc[1].kind == "photo",
                                                               rank.get(nc[1].provider, 9), nc[0]))]

    def take(self, scene_no: int, clip: StockClip) -> StockClip:
        self.used.add(clip.key)
        self.scene_used.setdefault(scene_no, []).append(clip)
        return clip

    def matching(self, scene: Scene) -> list[StockClip]:
        """The scene's word-matched clips, in the order its searches find them."""
        seen: dict[str, StockClip] = {}
        if self.photos and scene.place.strip():
            for photo in self.search("wikimedia", scene.place)[:3]:
                if photo.score >= RELEVANT:
                    seen.setdefault(photo.key, photo)
        for term in terms_of(scene):
            for provider in self.providers:
                for clip in self.search(provider, term):
                    if clip.score >= RELEVANT:
                        seen.setdefault(clip.key, clip)
        return list(seen.values())

    def vet(self, scenes: dict[int, Scene]) -> None:
        """Ask the AI which word-matched clips really fit each scene; the others are not used for it.
        Skipped (word matching only) when the AI service is unavailable."""
        from ..pipeline.highlights import HighlightError, ask_once, default_service
        items = [(i, scene, self.matching(scene)[:VET_CLIPS]) for i, scene in scenes.items()]
        items = [it for it in items if it[2]]
        for b in range(0, len(items), VET_SCENES):
            batch = items[b:b + VET_SCENES]
            prompt = "\n\n".join(
                f"Scene {i + 1}: {scene.text}\n" + "\n".join(f"  [{n + 1}] {c.text[:160]}" for n, c in enumerate(clips))
                for i, scene, clips in batch)
            while True:
                try:
                    answer = ask_once(self.service or default_service(), VET_SYSTEM, prompt, _Vetting)
                    break
                except limits.LimitReached as e:
                    if not e.limit.short:  # a daily limit: carry on with word matching only
                        log.warning("footage check skipped: %s", e)
                        return
                    cancel.sleep(e.limit.retry_after + 0.5)  # per-minute limits clear within seconds
                except HighlightError as e:
                    log.warning("footage check skipped: %s", e)
                    return
            keep = {k.scene - 1: set(k.keep) for k in answer.scenes}
            for i, _, clips in batch:
                if i in keep:
                    self.rejected[i] = {c.key for n, c in enumerate(clips) if n + 1 not in keep[i]}
                    self.approved[i] = {c.key for n, c in enumerate(clips) if n + 1 in keep[i]}

    def candidates(self, scene: Scene, scene_no: int) -> list[StockClip]:
        """Suggestions for the footage review: matching clips for every search, then the closest others."""
        seen: dict[str, StockClip] = {}
        weak: dict[str, StockClip] = {}
        sources = [("wikimedia", scene.place)] if self.photos and scene.place.strip() else []
        sources += [(p, t) for t in terms_of(scene) for p in self.providers]
        for provider, term in sources:
            for clip in self.search(provider, term):
                bucket = seen if clip.score >= RELEVANT else weak
                bucket.setdefault(clip.key, clip)
        rejected = self.rejected.get(scene_no, set())
        out = [c for c in seen.values() if c.key not in rejected][:CANDIDATES]
        if len(out) < 8:
            out += sorted(weak.values(), key=lambda c: -c.score)[:8 - len(out)]
        return out


def terms_of(scene: Scene) -> list[str]:
    return [t for t in scene.search_terms if t.strip()] or fallback_terms(scene.text)


def choose(scenes: list[Scene], shots: list[Shot], picker: Picker) -> list[StockClip | None]:
    """Footage for every shot (None for cards and for shots left plain)."""
    picker.prefetch([s for s in scenes if not s.clips])
    picker.vet({i: s for i, s in enumerate(scenes) if not s.clips})
    chosen: list[StockClip | None] = []
    for shot in shots:
        scene = scenes[shot.scene]
        if shot.kind != "footage":
            chosen.append(None)
            continue
        if shot.n < len(scene.clips):
            clip = picker.take(shot.scene, footage.clip_from(scene.clips[shot.n].model_dump()))
        elif shot.n == 0 and not scene.clips and (photo := picker.place_photo(shot.scene, scene)):
            clip = picker.take(shot.scene, photo)
        else:
            clip = picker.pick(shot.scene, scene, shot) if picker.providers or scene.clips else None
        chosen.append(clip)
    return chosen


def plan_footage(req: FootagePlanRequest, gap: float, tail: float) -> dict:
    """The footage review: for each scene, the clip each shot would use and other suggestions."""
    from ..pipeline.render import output_size
    w, h = output_size(req.aspect, req.resolution)
    providers = footage.providers_for(footage_sources(req))
    picker = Picker(providers, footage.orientation_of(req.aspect), min(w, h), req.photos, req.ai_service)
    scenes = [s.model_copy(update={"clips": []}) for s in req.scenes]  # suggest afresh
    shots = plan(scenes, estimated_spans(scenes, req.speed, gap, tail), req.clip_seconds, req.cards, False)
    wanted = range(len(scenes)) if req.only is None else [req.only]
    picker.prefetch([scenes[i] for i in wanted if i < len(scenes)])
    picker.vet({i: scenes[i] for i in wanted if i < len(scenes)})
    out = []
    for i, scene in enumerate(scenes):
        if i not in wanted:
            out.append(None)
            continue
        picks = []
        for shot in (s for s in shots if s.scene == i and s.kind == "footage"):
            clip = picker.place_photo(i, scene) if shot.n == 0 else None
            clip = picker.take(i, clip) if clip else picker.pick(i, scene, shot)
            if clip is not None and clip.key not in {c["key"] for c in picks}:
                picks.append({**clip.public(), "key": clip.key})
        out.append({"shots": len([s for s in shots if s.scene == i and s.kind == "footage"]),
                     "clips": picks,
                     "candidates": [{**c.public(), "key": c.key} for c in picker.candidates(scene, i)]})
    return {"scenes": out}


def search_media(sources: list[str], term: str, aspect: str, resolution: str) -> list[dict]:
    """A search typed in the footage review; "photos" are Wikimedia Commons photos."""
    from ..pipeline.render import output_size
    w, h = output_size(aspect, resolution)
    providers = (["wikimedia"] if "photos" in sources else []) + footage.providers_for([s for s in sources if s != "photos"])
    picker = Picker(providers, footage.orientation_of(aspect), min(w, h), True)
    results: dict[str, StockClip] = {}
    for p in providers:
        for clip in picker.search(p, term):
            results.setdefault(clip.key, clip)
    return [{**c.public(), "key": c.key} for c in sorted(results.values(), key=lambda c: -c.score)]


# --- Rendering shots ------------------------------------------------------------------------


@dataclass
class _ShotFile:
    out: Path
    shot: Shot
    clip: StockClip | None
    offset: float
    card: Path | None = None
    used_clip: bool = field(default=False)


def shoot(vid: str, scenes: list[Scene], shots: list[Shot], chosen: list[StockClip | None], size: tuple[int, int],
          work: Path, accent: str, on_progress) -> tuple[list[Path], list[dict]]:
    """Every shot as a small silent mp4 of exactly its frame count, and credits for the media used."""
    rng = random.Random(vid)
    shots_dir = work / "shots"
    shots_dir.mkdir(exist_ok=True)
    files: list[_ShotFile] = []
    for n, (shot, clip) in enumerate(zip(shots, chosen)):
        offset = 0.0
        if clip is not None and clip.kind == "video":
            slack = clip.duration - shot.seconds - 0.2
            offset = round(rng.uniform(0, min(slack, 4.0)), 2) if slack > 0 else 0.0
        card = None
        if shot.kind in ("card", "quote"):
            card = shots_dir / f"{n:04d}-{shot.kind}.png"
            draw_card(scenes[shot.scene], shot.kind, size, accent).save(card)
        label = shot.kind if card else (clip.key if clip else "plain")
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in label)[:60]
        name = f"{n:04d}-{safe}-{offset:.2f}-{shot.frames}{'i' if shot.fade_in else ''}{'o' if shot.fade_out else ''}"
        files.append(_ShotFile(shots_dir / f"{name}.mp4", shot, clip, offset, card))
    on_progress(0.05)

    job = cancel.current()
    downloads = _Downloads(work / "downloads")
    done = 0

    def encode(f: _ShotFile) -> None:
        with cancel.bound(job) if job else nullcontext():
            if f.out.exists() and video_frames(f.out) == f.shot.frames:
                f.used_clip = f.clip is not None
                return
            partial = f.out.with_suffix(".part.mp4")
            if f.card is not None:
                _encode_still(f, f.card, size, partial, zoom=0.0004)
            elif f.clip is not None and f.clip.kind == "photo":
                try:
                    image = uploads.path_of(f.clip.id) if f.clip.provider == "upload" else downloads.get(f.clip)
                    _encode_still(f, image, size, partial, zoom=0.0009)
                    f.used_clip = True
                except (media.MediaError, footage.FootageError) as e:
                    log.warning("shot %s: photo failed (%s); plain background", f.out.name, str(e).splitlines()[-1])
            elif f.clip is not None and f.clip.provider == "upload":
                try:
                    _encode_video(f, ["-i", str(uploads.path_of(f.clip.id))], size, partial)
                    f.used_clip = True
                except (media.MediaError, uploads.UploadError) as e:
                    log.warning("shot %s: your clip failed (%s); plain background", f.out.name, str(e).splitlines()[-1])
            elif f.clip is not None:
                link = (footage.coverr_link(f.clip.id) or f.clip.link) if f.clip.provider == "coverr" else f.clip.link
                stream = ["-user_agent", footage.USER_AGENT, "-reconnect", "1", "-reconnect_streamed", "1",
                          "-reconnect_delay_max", "5", "-i", link]
                try:  # only the seconds needed are read from the site; its CDN now and then sends nothing, so twice
                    try:
                        _encode_video(f, stream, size, partial)
                    except media.MediaError:
                        _encode_video(f, stream, size, partial)
                    f.used_clip = True
                except media.MediaError as e:
                    log.warning("shot %s: reading %s failed (%s); downloading it", f.out.name, f.clip.link,
                                str(e).splitlines()[-1])
                    try:
                        _encode_video(f, ["-i", str(downloads.get(f.clip))], size, partial)
                        f.used_clip = True
                    except (media.MediaError, footage.FootageError) as e2:
                        log.warning("shot %s: plain background (%s)", f.out.name, str(e2).splitlines()[-1])
            if f.card is None and not f.used_clip:
                _encode_video(replace(f, clip=None), [], size, partial)
            partial.rename(f.out)

    with ThreadPoolExecutor(max_workers=PARALLEL_SHOTS) as pool:
        for _ in pool.map(encode, files):
            done += 1
            on_progress(0.05 + 0.95 * done / len(files))
    shutil.rmtree(work / "downloads", ignore_errors=True)
    credits: dict[str, dict] = {}
    for f in files:
        if f.used_clip and f.clip and f.clip.provider != "upload":
            c = f.clip
            if c.key not in credits:
                footage.register_use(c)
            credits.setdefault(c.key, {"provider": c.provider, "author": c.author, "page": c.page,
                                       **({"license": c.license} if c.license else {})})
    return [f.out for f in files], list(credits.values())


def _fades(shot: Shot) -> list[str]:
    fades = [f"fade=t=in:st=0:d={FADE}"] if shot.fade_in else []
    if shot.fade_out:
        fades.append(f"fade=t=out:st={max(0.0, shot.seconds - FADE):.3f}:d={FADE}")
    return fades


def _encode(inputs: list[str], vf: list[str], f: _ShotFile, dest: Path) -> None:
    media.run([FFMPEG, "-y", "-v", "error", *inputs, "-vf", ",".join(vf + _fades(f.shot)),
               "-frames:v", str(f.shot.frames), "-an", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
               "-crf", "18", "-pix_fmt", "yuv420p", "-f", "mp4", str(dest)])
    # A link that breaks off mid-read can still leave a playable but short file; joined, it would cut the video short.
    got = video_frames(dest)
    if got != f.shot.frames:
        raise media.MediaError(f"shot has {got} of {f.shot.frames} frames")


def _encode_video(f: _ShotFile, source: list[str], size: tuple[int, int], dest: Path) -> None:
    w, h = size
    if f.clip is None:
        c0, c1 = GRADIENTS[f.shot.scene % len(GRADIENTS)]
        _encode(["-f", "lavfi", "-i", f"gradients=s={w}x{h}:c0={c0}:c1={c1}:speed=0.004:rate={FPS}"],
                ["setsar=1"], f, dest)
    else:
        _encode(["-ss", f"{f.offset:.2f}", "-t", f"{f.shot.seconds + 1:.3f}", *source],
                [f"scale={w}:{h}:force_original_aspect_ratio=increase", f"crop={w}:{h}", "setsar=1", f"fps={FPS}",
                 "tpad=stop_mode=clone:stop_duration=5"], f, dest)


def _encode_still(f: _ShotFile, image: Path, size: tuple[int, int], dest: Path, zoom: float) -> None:
    """A photo or card with a slow push-in (drawn at twice the size so the motion is smooth)."""
    w, h = size
    frames = f.shot.frames
    _encode(["-i", str(image)],
            [f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase", f"crop={w * 2}:{h * 2}",
             f"zoompan=z='min(1+{zoom}*on,1.2)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={FPS}",
             "setsar=1"], f, dest)


def video_frames(path: Path) -> int:
    out = media.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_packets",
                     "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)])
    try:
        return int(out.strip().split(",")[0])
    except ValueError:
        return 0


class _Downloads:
    """Whole-file downloads: photos, and clips ffmpeg cannot read in parts. One download per item."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.locks: dict[str, threading.Lock] = {}
        self.lock = threading.Lock()

    def get(self, clip: StockClip) -> Path:
        with self.lock:
            lock = self.locks.setdefault(clip.key, threading.Lock())
        with lock:
            safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in clip.key)[:80]
            path = self.folder / f"{safe}{'.jpg' if clip.kind == 'photo' else '.mp4'}"
            if path.exists():
                return path
            self.folder.mkdir(parents=True, exist_ok=True)
            part = path.with_suffix(".part")
            try:
                with requests.get(clip.link, stream=True, timeout=60,
                                  headers={"User-Agent": footage.USER_AGENT}) as r:
                    r.raise_for_status()
                    with part.open("wb") as out:
                        for chunk in r.iter_content(1 << 20):
                            cancel.check()
                            out.write(chunk)
            except requests.RequestException as e:
                part.unlink(missing_ok=True)
                raise footage.FootageError(f"download failed: {e.__class__.__name__}") from e
            part.rename(path)
            return path


# --- Cards ------------------------------------------------------------------------------------


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(f"{APP_FONT_DIR}/{name}", size)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def draw_card(scene: Scene, kind: str, size: tuple[int, int], accent: str) -> Image.Image:
    """A full-frame title card: a key fact in large type, or a quote with who said it."""
    w, h = size
    img = Image.new("RGB", size)
    draw = ImageDraw.Draw(img)
    top, bottom = (17, 24, 39), (3, 7, 18)
    for y in range(h):
        k = y / h
        draw.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * k) for a, b in zip(top, bottom)))
    color = _rgb(accent)
    short = min(w, h)
    margin = int(w * 0.1)
    if kind == "card":
        text = scene.card_text.strip().upper()
        size_px = int(short * 0.16)
        while True:
            font = _font("Anton-400.ttf", size_px)
            lines = _wrap(draw, text, font, w - 2 * margin)
            if (len(lines) <= 3 and all(draw.textlength(l, font=font) <= w - 2 * margin for l in lines)) or size_px < 30:
                break
            size_px -= 6
        line_h = int(size_px * 1.15)
        y = (h - line_h * len(lines)) // 2
        bar_w = int(short * 0.12)
        draw.rectangle([(w - bar_w) // 2, y - int(size_px * 0.55), (w + bar_w) // 2, y - int(size_px * 0.45)], fill=color)
        for line in lines:
            draw.text((w / 2, y), line, font=font, fill=(255, 255, 255), anchor="ma")
            y += line_h
    else:
        quote = scene.quote.strip().strip('"“”')
        size_px = int(short * 0.1)
        while True:
            font = _font("PoltawskiNowy-400.ttf", size_px)
            lines = _wrap(draw, quote, font, w - 2 * margin)
            if len(lines) <= 5 or size_px < 24:
                break
            size_px -= 4
        line_h = int(size_px * 1.3)
        by = _font("Poppins-600.ttf", int(size_px * 0.6))
        block = line_h * len(lines) + (int(size_px * 1.4) if scene.quote_by else 0)
        y = (h - block) // 2
        mark = _font("PoltawskiNowy-400.ttf", int(size_px * 3))
        draw.text((margin - int(size_px * 0.2), y - int(size_px * 1.2)), "“", font=mark, fill=color)
        for line in lines:
            draw.text((margin, y), line, font=font, fill=(255, 255, 255))
            y += line_h
        if scene.quote_by.strip():
            draw.text((margin, y + int(size_px * 0.5)), f"— {scene.quote_by.strip()}", font=by, fill=color)
    return img


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: float) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if line and draw.textlength(trial, font=font) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines or textwrap.wrap(text, 20)
