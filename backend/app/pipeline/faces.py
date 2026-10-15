"""Find where people are in a clip so reframing can keep them in shot.

Splits the clip into camera shots (ffmpeg scene detection), samples frames in each shot,
detects frontal and profile faces with OpenCV's bundled Haar cascades (no model download),
and summarises each shot as no one, one subject or a pair. Podcasts often cut between a
two-shot and close-ups, so one summary for the whole clip would frame most of it wrong.
How a pair is shown (shared shot, stacked, side by side) is decided by the renderer.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from ..config import FFMPEG
from .cancel import check, run_process

SAMPLE_EVERY = 0.5        # seconds between sampled frames
MAX_SAMPLES = 80
DETECT_WIDTH = 640        # frames are downscaled to this width before detection
MIN_FACE_FRACTION = 0.045  # ignore faces narrower than this fraction of the frame
PAIR_SEPARATION = 0.12    # two faces this far apart (fraction of width) count as two people
PAIR_MIN_SHARE = 0.35     # share of face-bearing frames that must show the pair
# Two people are often not detected in the same frame (one turns away). Two stable face
# positions this far apart, each seen in this share of frames, also count as a pair.
CLUSTER_SEPARATION = 0.25
CLUSTER_MIN_SHARE = 0.25

SCENE_THRESHOLD = 0.15    # ffmpeg scene score that counts as a camera cut (dark studio cuts score ~0.2)
MIN_SHOT = 0.8            # seconds; shorter shots are merged into their neighbour
MAX_SHOTS = 40
SHOT_SAMPLES = 10         # target face samples per shot
MIN_SHOT_STEP = 0.2


@dataclass(frozen=True)
class Face:
    cx: float   # centre, as a fraction of frame width / height
    cy: float
    h: float    # face height as a fraction of frame height


@dataclass(frozen=True)
class Subjects:
    kind: str                 # "none" | "single" | "pair"
    faces: tuple[Face, ...]   # one face for single, left then right for pair


@dataclass(frozen=True)
class Shot:
    start: float              # seconds from the clip start
    end: float
    subjects: Subjects


def _detectors():
    import cv2

    def load(name: str):
        return cv2.CascadeClassifier(cv2.data.haarcascades + name)

    return load("haarcascade_frontalface_default.xml"), load("haarcascade_profileface.xml")


def _detect(gray, frontal, profile, min_side: int) -> list[tuple[int, int, int, int]]:
    """Frontal faces plus left- and right-facing profiles, without duplicates."""
    import cv2

    kw = dict(scaleFactor=1.1, minNeighbors=6, minSize=(min_side, min_side))
    found = [tuple(int(v) for v in r) for r in frontal.detectMultiScale(gray, **kw)]
    width = gray.shape[1]
    profiles = [tuple(int(v) for v in r) for r in profile.detectMultiScale(gray, **kw)]
    # The profile cascade only finds faces looking one way; mirror the frame for the other.
    for x, y, w, h in profile.detectMultiScale(cv2.flip(gray, 1), **kw):
        profiles.append((int(width - x - w), int(y), int(w), int(h)))
    for x, y, w, h in profiles:
        cx, cy = x + w / 2, y + h / 2
        if all(abs(cx - (fx + fw / 2)) > fw * 0.6 or abs(cy - (fy + fh / 2)) > fh * 0.6 for fx, fy, fw, fh in found):
            found.append((x, y, w, h))
    return found


def sample_faces(src: Path, start: float, end: float, step: float | None = None) -> list[list[Face]]:
    import cv2

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return []
    frontal, profile = _detectors()
    step = step or max(SAMPLE_EVERY, (end - start) / MAX_SAMPLES)
    samples: list[list[Face]] = []
    t = start + step / 2
    try:
        while t < end:
            check()
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            t += step
            if not ok:
                continue
            h, w = frame.shape[:2]
            scale = DETECT_WIDTH / w
            small = cv2.resize(frame, (DETECT_WIDTH, int(h * scale)))
            gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            min_side = int(DETECT_WIDTH * MIN_FACE_FRACTION)
            found = _detect(gray, frontal, profile, min_side)
            sh, sw = gray.shape
            samples.append([Face((x + fw / 2) / sw, (y + fh / 2) / sh, fh / sh) for x, y, fw, fh in found])
    finally:
        cap.release()
    return samples


def summarize(samples: list[list[Face]]) -> Subjects:
    with_faces = [s for s in samples if s]
    if not samples or len(with_faces) < max(2, len(samples) * 0.3):
        return Subjects("none", ())

    pairs = []
    for faces in with_faces:
        ordered = sorted(faces, key=lambda f: f.cx)
        left, right = ordered[0], ordered[-1]
        if right.cx - left.cx >= PAIR_SEPARATION:
            pairs.append((left, right))

    if len(pairs) >= len(with_faces) * PAIR_MIN_SHARE:
        lefts, rights = [p[0] for p in pairs], [p[1] for p in pairs]
        return Subjects("pair", (_median_face(lefts), _median_face(rights)))

    clustered = _two_positions(with_faces)
    if clustered:
        return Subjects("pair", clustered)

    largest = [max(faces, key=lambda f: f.h) for faces in with_faces]
    return Subjects("single", (_median_face(largest),))


def _two_positions(frames: list[list[Face]]) -> tuple[Face, Face] | None:
    """Left and right person when they show up in different frames, split at the widest gap."""
    typical = median(f.h for faces in frames for f in faces)
    faces = sorted((f for fs in frames for f in fs if f.h >= typical * 0.6), key=lambda f: f.cx)
    if len(faces) < 4:
        return None
    gap, k = max((faces[i + 1].cx - faces[i].cx, i + 1) for i in range(len(faces) - 1))
    left, right = faces[:k], faces[k:]
    lf, rf = _median_face(left), _median_face(right)
    if rf.cx - lf.cx < CLUSTER_SEPARATION:
        return None
    split = (faces[k - 1].cx + faces[k].cx) / 2
    seen_left = sum(1 for fs in frames if any(f.cx < split for f in fs))
    seen_right = sum(1 for fs in frames if any(f.cx >= split for f in fs))
    if min(seen_left, seen_right) < len(frames) * CLUSTER_MIN_SHARE:
        return None
    return lf, rf


def _median_face(faces: list[Face]) -> Face:
    return Face(median(f.cx for f in faces), median(f.cy for f in faces), median(f.h for f in faces))


def find_subjects(src: Path, start: float, end: float) -> Subjects:
    return summarize(sample_faces(src, start, end))


def find_cuts(src: Path, start: float, end: float) -> list[float]:
    """Camera cut times in seconds from `start`, from ffmpeg's scene change score."""
    cmd = [FFMPEG, "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
           "-an", "-sn", "-vf", f"scale=256:-2,select='gt(scene,{SCENE_THRESHOLD})',showinfo", "-f", "null", "-"]
    try:
        proc = run_process(cmd, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return sorted(float(t) for t in re.findall(r"pts_time:\s*([0-9.]+)", proc.stderr))


def _shot_bounds(cuts: list[float], length: float) -> list[tuple[float, float]]:
    bounds = [0.0]
    for c in cuts:
        if c - bounds[-1] >= MIN_SHOT and length - c >= MIN_SHOT:
            bounds.append(c)
    bounds.append(length)
    shots = list(zip(bounds, bounds[1:]))
    while len(shots) > MAX_SHOTS:  # merge the shortest shot into its shorter neighbour
        i = min(range(len(shots)), key=lambda j: shots[j][1] - shots[j][0])
        j = i - 1 if i == len(shots) - 1 or (i > 0 and shots[i - 1][1] - shots[i - 1][0] <
                                               shots[i + 1][1] - shots[i + 1][0]) else i + 1
        a, b = min(i, j), max(i, j)
        shots[a:b + 1] = [(shots[a][0], shots[b][1])]
    return shots


def _same_framing(a: Subjects, b: Subjects) -> bool:
    return a.kind == b.kind and all(abs(x.cx - y.cx) < 0.04 and abs(x.cy - y.cy) < 0.05
                                    for x, y in zip(a.faces, b.faces))


def find_shots(src: Path, start: float, end: float) -> list[Shot]:
    """Camera shots of the clip, each with the people in it. Neighbours framed alike are merged."""
    shots: list[Shot] = []
    for a, b in _shot_bounds(find_cuts(src, start, end), end - start):
        step = min(SAMPLE_EVERY, max(MIN_SHOT_STEP, (b - a) / SHOT_SAMPLES))
        subjects = summarize(sample_faces(src, start + a, start + b, step))
        if shots and _same_framing(shots[-1].subjects, subjects):
            shots[-1] = Shot(shots[-1].start, b, shots[-1].subjects)
        else:
            shots.append(Shot(a, b, subjects))
    return shots
