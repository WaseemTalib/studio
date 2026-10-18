"""SQLite persistence for projects and clips."""

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from .config import DB_PATH

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL,          -- 'upload' | 'url'
    source TEXT NOT NULL,               -- original filename or URL
    options TEXT NOT NULL,              -- JSON ProjectOptions
    status TEXT NOT NULL,               -- queued | processing | done | failed | cancelled
    stage TEXT,                         -- ingest | transcribe | highlights | render
    progress REAL NOT NULL DEFAULT 0,   -- 0..1
    error TEXT,
    duration REAL,
    thumbnail TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL,
    title TEXT NOT NULL,
    topic TEXT NOT NULL DEFAULT '',
    hook TEXT NOT NULL,
    reason TEXT NOT NULL,
    score INTEGER NOT NULL,
    hashtags TEXT NOT NULL,             -- JSON list
    start REAL NOT NULL,
    end REAL NOT NULL,
    transcript TEXT NOT NULL,
    video TEXT,
    thumbnail TEXT,
    look TEXT NOT NULL DEFAULT '{}',    -- JSON LookOptions used for this clip
    layout TEXT NOT NULL DEFAULT '',    -- layout actually rendered (auto resolved)
    words TEXT,                         -- JSON caption words; NULL means use the transcript
    status TEXT NOT NULL DEFAULT 'ready',  -- ready | rendering | failed
    error TEXT,
    version INTEGER NOT NULL DEFAULT 1,    -- bumps on every render, busts browser caches
    created_at REAL NOT NULL
);
"""

TTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tts_generations (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,             -- elevenlabs | local
    voice_id TEXT NOT NULL,
    voice_name TEXT NOT NULL,
    model_id TEXT NOT NULL,
    text TEXT NOT NULL,
    settings TEXT NOT NULL,             -- JSON voice settings used
    audio TEXT NOT NULL,                -- URL under /voice-media
    duration REAL,
    created_at REAL NOT NULL
);
"""

TEMPLATES_SCHEMA = """
CREATE TABLE IF NOT EXISTS templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    options TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
"""

VIDEOS_SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    request TEXT NOT NULL,              -- JSON VideoRequest: script, voice and look
    status TEXT NOT NULL,               -- queued | processing | done | failed | cancelled
    stage TEXT,                         -- voice | captions | footage | render
    progress REAL NOT NULL DEFAULT 0,   -- 0..1
    error TEXT,
    video TEXT,                         -- URL under /video-media
    thumbnail TEXT,
    duration REAL,
    credits TEXT NOT NULL DEFAULT '[]', -- JSON list of stock clips used: provider, author, page
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""

PLANS_SCHEMA = """
CREATE TABLE IF NOT EXISTS director_plans (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    settings TEXT NOT NULL,             -- JSON DirectorSettings
    status TEXT NOT NULL,               -- queued | processing | done | failed | cancelled (the current job)
    step TEXT NOT NULL,                 -- proposal | prompts: what the current or last job writes
    stage TEXT,
    progress REAL NOT NULL DEFAULT 0,
    error TEXT,
    proposal TEXT,                      -- JSON director's proposal (Phase A)
    approved INTEGER NOT NULL DEFAULT 0,
    package TEXT,                       -- JSON production package (Phase B)
    revision TEXT,                      -- JSON revision the running job applies
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""

# Columns added after the first release, applied to existing databases on startup.
CLIP_MIGRATIONS = {
    "topic": "TEXT NOT NULL DEFAULT ''",
    "look": "TEXT NOT NULL DEFAULT '{}'",
    "layout": "TEXT NOT NULL DEFAULT ''",
    "words": "TEXT",
    "status": "TEXT NOT NULL DEFAULT 'ready'",
    "error": "TEXT",
    "version": "INTEGER NOT NULL DEFAULT 1",
}
JSON_CLIP_FIELDS = ("hashtags", "look", "words")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    with _lock:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.executescript(TEMPLATES_SCHEMA)
        conn.executescript(TTS_SCHEMA)
        conn.executescript(VIDEOS_SCHEMA)
        conn.executescript(PLANS_SCHEMA)
        clip_columns = {r["name"] for r in conn.execute("PRAGMA table_info(clips)")}
        for name, decl in CLIP_MIGRATIONS.items():
            if name not in clip_columns:
                conn.execute(f"ALTER TABLE clips ADD COLUMN {name} {decl}")
        conn.execute("UPDATE clips SET status='failed', error='Interrupted by server restart' WHERE status='rendering'")
        # Jobs do not survive a restart; surface that instead of leaving them spinning.
        conn.execute(
            "UPDATE projects SET status='failed', error='Interrupted by server restart', updated_at=? "
            "WHERE status IN ('queued', 'processing')",
            (time.time(),),
        )
        conn.execute(
            "UPDATE videos SET status='failed', error='Interrupted by server restart', updated_at=? "
            "WHERE status IN ('queued', 'processing')",
            (time.time(),),
        )
        conn.execute(
            "UPDATE director_plans SET status='failed', error='Interrupted by server restart', updated_at=? "
            "WHERE status IN ('queued', 'processing')",
            (time.time(),),
        )


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _project(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["options"] = json.loads(d["options"])
    return d


def _clip(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for field in JSON_CLIP_FIELDS:
        if d[field] is not None:
            d[field] = json.loads(d[field])
    return d


def create_project(pid: str, title: str, source_type: str, source: str, options: dict) -> None:
    now = time.time()
    with connect() as conn:
        conn.execute(
            "INSERT INTO projects (id, title, source_type, source, options, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)",
            (pid, title, source_type, source, json.dumps(options), now, now),
        )


def update_project(pid: str, **fields: Any) -> None:
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE projects SET {cols} WHERE id = ?", (*fields.values(), pid))


def mark_cancelled(pid: str) -> None:
    """Record a cancel, unless the job already finished on its own."""
    with connect() as conn:
        conn.execute(
            "UPDATE projects SET status='cancelled', stage=NULL, error=NULL, updated_at=? "
            "WHERE id = ? AND status IN ('queued', 'processing')",
            (time.time(), pid),
        )


def get_project(pid: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
        if row is None:
            return None
        project = _project(row)
        project["clip_count"] = conn.execute(
            "SELECT COUNT(*) FROM clips WHERE project_id = ?", (pid,)
        ).fetchone()[0]
        return project


def list_projects() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM clips c WHERE c.project_id = p.id) AS clip_count "
            "FROM projects p ORDER BY created_at DESC"
        ).fetchall()
        return [_project(r) for r in rows]


def delete_project(pid: str) -> bool:
    with connect() as conn:
        return conn.execute("DELETE FROM projects WHERE id = ?", (pid,)).rowcount > 0


def replace_clips(pid: str, clips: list[dict[str, Any]]) -> None:
    now = time.time()
    with connect() as conn:
        conn.execute("DELETE FROM clips WHERE project_id = ?", (pid,))
        conn.executemany(
            "INSERT INTO clips (id, project_id, rank, title, topic, hook, reason, score, hashtags, start, end, "
            "transcript, video, thumbnail, look, layout, created_at) VALUES "
            "(:id, :project_id, :rank, :title, :topic, :hook, :reason, :score, :hashtags, :start, :end, "
            ":transcript, :video, :thumbnail, :look, :layout, :created_at)",
            [{**c, "project_id": pid, "hashtags": json.dumps(c["hashtags"]), "look": json.dumps(c["look"]),
              "created_at": now} for c in clips],
        )


def list_clips(pid: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM clips WHERE project_id = ? ORDER BY score DESC, rank", (pid,)
        ).fetchall()
        return [_clip(r) for r in rows]


def get_clip(cid: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (cid,)).fetchone()
        return _clip(row) if row else None


def update_clip(cid: str, **fields: Any) -> dict[str, Any] | None:
    for field in JSON_CLIP_FIELDS:
        if field in fields and fields[field] is not None:
            fields[field] = json.dumps(fields[field])
    with connect() as conn:
        if fields:
            cols = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE clips SET {cols} WHERE id = ?", (*fields.values(), cid))
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (cid,)).fetchone()
        return _clip(row) if row else None


def list_templates() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM templates ORDER BY is_default DESC, created_at DESC").fetchall()
        return [{**dict(r), "options": json.loads(r["options"]), "is_default": bool(r["is_default"])} for r in rows]


def save_template(name: str, options: dict, is_default: bool) -> str:
    """Create a template, or replace the one with the same name. Only one template is the default."""
    now = time.time()
    with connect() as conn:
        if is_default:
            conn.execute("UPDATE templates SET is_default = 0")
        existing = conn.execute("SELECT id FROM templates WHERE name = ?", (name,)).fetchone()
        tid = existing["id"] if existing else new_id()
        conn.execute(
            "INSERT OR REPLACE INTO templates (id, name, options, is_default, created_at) VALUES (?, ?, ?, ?, ?)",
            (tid, name, json.dumps(options), int(is_default), now),
        )
        return tid


def delete_template(tid: str) -> bool:
    with connect() as conn:
        return conn.execute("DELETE FROM templates WHERE id = ?", (tid,)).rowcount > 0


def add_generation(g: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO tts_generations (id, provider, voice_id, voice_name, model_id, text, settings, audio, "
            "duration, created_at) VALUES (:id, :provider, :voice_id, :voice_name, :model_id, :text, :settings, "
            ":audio, :duration, :created_at)",
            {**g, "settings": json.dumps(g["settings"])},
        )


def list_generations(limit: int = 200) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM tts_generations ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "settings": json.loads(r["settings"])} for r in rows]


def get_generation(gid: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM tts_generations WHERE id = ?", (gid,)).fetchone()
        return {**dict(row), "settings": json.loads(row["settings"])} if row else None


def delete_generation(gid: str) -> bool:
    with connect() as conn:
        return conn.execute("DELETE FROM tts_generations WHERE id = ?", (gid,)).rowcount > 0


# --- Video maker -------------------------------------------------------------------


def _video(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["request"] = json.loads(d["request"])
    d["credits"] = json.loads(d["credits"])
    return d


def create_video(vid: str, title: str, request: dict) -> None:
    now = time.time()
    with connect() as conn:
        conn.execute("INSERT INTO videos (id, title, request, status, created_at, updated_at) "
                     "VALUES (?, ?, ?, 'queued', ?, ?)", (vid, title, json.dumps(request), now, now))


def update_video(vid: str, **fields: Any) -> None:
    if "credits" in fields:
        fields["credits"] = json.dumps(fields["credits"])
    if "request" in fields:
        fields["request"] = json.dumps(fields["request"])
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE videos SET {cols} WHERE id = ?", (*fields.values(), vid))


def mark_video_cancelled(vid: str) -> None:
    """Record a cancel, unless the job already finished on its own."""
    with connect() as conn:
        conn.execute("UPDATE videos SET status='cancelled', stage=NULL, error=NULL, updated_at=? "
                     "WHERE id = ? AND status IN ('queued', 'processing')", (time.time(), vid))


def get_video(vid: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM videos WHERE id = ?", (vid,)).fetchone()
        return _video(row) if row else None


def list_videos() -> list[dict[str, Any]]:
    with connect() as conn:
        return [_video(r) for r in conn.execute("SELECT * FROM videos ORDER BY created_at DESC").fetchall()]


def delete_video(vid: str) -> bool:
    with connect() as conn:
        return conn.execute("DELETE FROM videos WHERE id = ?", (vid,)).rowcount > 0


# --- Stickman Director ------------------------------------------------------------------------

_PLAN_JSON = ("settings", "proposal", "package", "revision")


def _plan(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for k in _PLAN_JSON:
        d[k] = json.loads(d[k]) if d[k] else None
    d["approved"] = bool(d["approved"])
    return d


def create_plan(pid: str, title: str, settings: dict) -> None:
    now = time.time()
    with connect() as conn:
        conn.execute("INSERT INTO director_plans (id, title, settings, status, step, created_at, updated_at) "
                     "VALUES (?, ?, ?, 'queued', 'proposal', ?, ?)", (pid, title, json.dumps(settings), now, now))


def update_plan(pid: str, **fields: Any) -> None:
    for k in _PLAN_JSON:
        if k in fields and fields[k] is not None:
            fields[k] = json.dumps(fields[k])
    if "approved" in fields:
        fields["approved"] = int(bool(fields["approved"]))
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE director_plans SET {cols} WHERE id = ?", (*fields.values(), pid))


def get_plan(pid: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM director_plans WHERE id = ?", (pid,)).fetchone()
        return _plan(row) if row else None


def list_plans() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT id, title, settings, status, step, stage, progress, error, approved, "
                            "package IS NOT NULL AS has_package, created_at, updated_at FROM director_plans "
                            "ORDER BY updated_at DESC").fetchall()
        return [{**dict(r), "settings": json.loads(r["settings"]), "approved": bool(r["approved"]),
                 "has_package": bool(r["has_package"])} for r in rows]


def delete_plan(pid: str) -> bool:
    with connect() as conn:
        return conn.execute("DELETE FROM director_plans WHERE id = ?", (pid,)).rowcount > 0
