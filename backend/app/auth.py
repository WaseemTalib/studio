"""Users, login sessions and per-tool permissions.

A session is a random token kept in an HttpOnly cookie; only its SHA-256 is stored. A session ends when the
user logs out or after SESSION_IDLE_HOURS without activity. Admins can do everything; other users only use
the tools in their permission list.
"""

import hashlib
import hmac
import json
import secrets
import threading
import time
from typing import Any

from anyio import to_thread
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from starlette.responses import JSONResponse

from . import db
from .config import AUTH_PASSWORD, AUTH_USER, SESSION_IDLE_HOURS

COOKIE = "studio_session"
IDLE_SECONDS = SESSION_IDLE_HOURS * 3600
# Requests with this header (background polling while nobody is using the page) do not count as activity.
BACKGROUND_HEADER = "x-studio-background"
TOUCH_EVERY = 60  # seconds between last_seen writes for one session

# Tool permissions. Admins have all of them plus user management.
PERMISSIONS = {
    "shorts": "Turn to Shorts",
    "voice": "Voice Studio",
    "video_maker": "Video Maker",
    "video_director": "Video Director",
    "stickman": "Stickman Director",
}
ROLES = ("admin", "user")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password TEXT NOT NULL,             -- pbkdf2_sha256$iterations$salt$hash
    role TEXT NOT NULL,                 -- admin | user
    permissions TEXT NOT NULL DEFAULT '[]',  -- JSON list of PERMISSIONS keys (ignored for admins)
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    last_seen REAL NOT NULL
);
"""

# --- Passwords --------------------------------------------------------------------------------------

_ITERATIONS = 300_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS).hex()
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
    except ValueError:
        return False
    return hmac.compare_digest(candidate, digest)


_DUMMY_HASH = hash_password(secrets.token_hex(8))  # equalises timing for unknown usernames


# --- Storage ----------------------------------------------------------------------------------------


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _public(row: Any) -> dict[str, Any]:
    d = dict(row)
    d.pop("password", None)
    d["disabled"] = bool(d["disabled"])
    perms = json.loads(d["permissions"])
    d["permissions"] = list(PERMISSIONS) if d["role"] == "admin" else [p for p in perms if p in PERMISSIONS]
    return d


def init() -> None:
    """Create the tables, drop idle sessions, and seed the first admin from SHORTCUT_AUTH_* if set."""
    with db.connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute("DELETE FROM sessions WHERE last_seen < ?", (time.time() - IDLE_SECONDS,))
        has_users = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    if not has_users and AUTH_PASSWORD:
        create_user(AUTH_USER or "admin", AUTH_PASSWORD, "admin", [])


def needs_setup() -> bool:
    with db.connect() as conn:
        return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None


def list_users() -> list[dict[str, Any]]:
    with db.connect() as conn:
        return [_public(r) for r in conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()]


def get_user(uid: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        return _public(row) if row else None


def create_user(username: str, password: str, role: str, permissions: list[str]) -> dict[str, Any]:
    uid = db.new_id()
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise HTTPException(409, "That username is taken")
        conn.execute(
            "INSERT INTO users (id, username, password, role, permissions, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (uid, username, hash_password(password), role, json.dumps(permissions), time.time()),
        )
    return get_user(uid)  # type: ignore[return-value]


def _active_admins(conn, excluding: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0 AND id != ?",
                        (excluding,)).fetchone()[0]


def update_user(uid: str, **fields: Any) -> dict[str, Any]:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if row is None:
            raise HTTPException(404, "User not found")
        demoted = fields.get("role", row["role"]) != "admin" or fields.get("disabled", bool(row["disabled"]))
        if row["role"] == "admin" and not row["disabled"] and demoted and _active_admins(conn, uid) == 0:
            raise HTTPException(400, "Keep at least one active admin")
        if "username" in fields and conn.execute("SELECT 1 FROM users WHERE username = ? AND id != ?",
                                                 (fields["username"], uid)).fetchone():
            raise HTTPException(409, "That username is taken")
        if "password" in fields:
            fields["password"] = hash_password(fields["password"])
        if "permissions" in fields:
            fields["permissions"] = json.dumps(fields["permissions"])
        if "disabled" in fields:
            fields["disabled"] = int(fields["disabled"])
        if fields:
            cols = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE users SET {cols} WHERE id = ?", (*fields.values(), uid))
        if fields.get("disabled") or "password" in fields:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
    return get_user(uid)  # type: ignore[return-value]


def delete_user(uid: str) -> None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if row is None:
            raise HTTPException(404, "User not found")
        if row["role"] == "admin" and _active_admins(conn, uid) == 0:
            raise HTTPException(400, "Keep at least one active admin")
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))


def _verify_login(username: str, password: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        check_password(password, _DUMMY_HASH)
        return None
    if not check_password(password, row["password"]) or row["disabled"]:
        return None
    return _public(row)


def _start_session(uid: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with db.connect() as conn:
        conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, last_seen) VALUES (?, ?, ?, ?)",
                     (_token_hash(token), uid, now, now))
    return token


def _end_session(token: str) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def session_user(token: str, active: bool) -> dict[str, Any] | None:
    """The user a session token belongs to, or None when it is unknown, idle too long or disabled.
    `active` requests push the idle deadline back."""
    now = time.time()
    th = _token_hash(token)
    with db.connect() as conn:
        row = conn.execute("SELECT s.last_seen, u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                           "WHERE s.token_hash = ?", (th,)).fetchone()
        if row is None:
            return None
        if now - row["last_seen"] > IDLE_SECONDS or row["disabled"]:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (th,))
            return None
        if active and now - row["last_seen"] > TOUCH_EVERY:
            conn.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, th))
        user = _public(row)
        user.pop("last_seen", None)
        user["session_expires_at"] = (now if active else row["last_seen"]) + IDLE_SECONDS
        return user


# --- Access rules -----------------------------------------------------------------------------------

PUBLIC_API = {"/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/setup", "/api/auth/logout"}
PROTECTED_PREFIXES = ("/api/", "/media/", "/voice-media/", "/video-media/")
ADMIN = "admin"

# First match wins; a path needs any one of the listed permissions. Unlisted /api paths need only a login.
RULES: list[tuple[str, tuple[str, ...]]] = [
    ("/api/users", (ADMIN,)),
    ("/api/projects", ("shorts",)),
    ("/api/clips", ("shorts",)),
    ("/api/templates", ("shorts",)),
    ("/api/source-info", ("shorts",)),
    ("/media/", ("shorts",)),
    ("/api/caption-preview.png", ("shorts", "video_maker")),
    ("/api/tts/generate", ("voice",)),
    ("/api/tts/history", ("voice",)),
    ("/voice-media/", ("voice",)),
    ("/api/tts", ("voice", "video_maker")),  # Video Maker picks its narrator voice here
    ("/api/maker", ("video_maker",)),
    ("/video-media/", ("video_maker",)),
    ("/api/video-director", ("video_director",)),
    ("/api/director", ("stickman",)),
]

TASK_PERMISSION = {"shorts": "shorts", "video-maker": "video_maker", "video-director": "video_director",
                   "stickman-director": "stickman"}


def allowed(user: dict[str, Any], needed: tuple[str, ...] | str) -> bool:
    if user["role"] == "admin":
        return True
    needed = (needed,) if isinstance(needed, str) else needed
    return any(p != ADMIN and p in user["permissions"] for p in needed)


def _required(path: str) -> tuple[str, ...] | None:
    for prefix, perms in RULES:
        if path == prefix.rstrip("/") or path.startswith(prefix.rstrip("/") + "/"):
            return perms
    return None


class AuthMiddleware:
    """Checks the session cookie on every API and media request, and the user's permission for it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path: str = scope["path"]
        if path in PUBLIC_API or not path.startswith(PROTECTED_PREFIXES) or scope["method"] == "OPTIONS":
            return await self.app(scope, receive, send)
        request = Request(scope)
        token = request.cookies.get(COOKIE)
        active = request.headers.get(BACKGROUND_HEADER) != "1"
        user = await to_thread.run_sync(session_user, token, active) if token else None
        if user is None:
            response = JSONResponse({"detail": "Please log in", "code": "unauthenticated"}, status_code=401)
            if token:
                response.delete_cookie(COOKIE, path="/")
            return await response(scope, receive, send)
        needed = _required(path)
        if needed and not allowed(user, needed):
            return await JSONResponse({"detail": "You do not have access to this tool", "code": "forbidden"},
                                      status_code=403)(scope, receive, send)
        scope.setdefault("state", {})["user"] = user
        token_var = db.current_user.set({"id": user["id"], "username": user["username"]})
        try:
            return await self.app(scope, receive, send)
        finally:
            db.current_user.reset(token_var)


def current_user(request: Request) -> dict[str, Any]:
    return request.state.user


# --- Login throttling -------------------------------------------------------------------------------

_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()
MAX_FAILURES, FAILURE_WINDOW = 10, 15 * 60


def _client(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")


def _throttled(key: str) -> bool:
    now = time.time()
    with _failures_lock:
        recent = [t for t in _failures.get(key, []) if now - t < FAILURE_WINDOW]
        _failures[key] = recent
        return len(recent) >= MAX_FAILURES


def _record_failure(key: str) -> None:
    with _failures_lock:
        _failures.setdefault(key, []).append(time.time())


# --- API --------------------------------------------------------------------------------------------

router = APIRouter()

Username = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z0-9_.@-]+$")
Password = Field(min_length=8, max_length=200)


class Credentials(BaseModel):
    username: str = Field(max_length=40)
    password: str = Field(max_length=200)


class SetupIn(BaseModel):
    username: str = Username
    password: str = Password


def _check_permissions(v: list[str]) -> list[str]:
    unknown = [p for p in v if p not in PERMISSIONS]
    if unknown:
        raise ValueError(f"Unknown permissions: {', '.join(unknown)}")
    return sorted(set(v))


class UserIn(BaseModel):
    username: str = Username
    password: str = Password
    role: str = Field("user", pattern="^(admin|user)$")
    permissions: list[str] = []

    _perms = field_validator("permissions")(_check_permissions)


class UserUpdate(BaseModel):
    username: str | None = Field(None, min_length=2, max_length=40, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str | None = Field(None, min_length=8, max_length=200)
    role: str | None = Field(None, pattern="^(admin|user)$")
    permissions: list[str] | None = None
    disabled: bool | None = None

    @field_validator("permissions")
    @classmethod
    def _perms(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _check_permissions(v)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Password


def _set_cookie(request: Request, response: Response, token: str) -> None:
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(COOKIE, token, max_age=30 * 24 * 3600, httponly=True, samesite="lax", secure=secure,
                        path="/")


def _status_for(request: Request) -> dict[str, Any]:
    token = request.cookies.get(COOKIE)
    user = session_user(token, active=request.headers.get(BACKGROUND_HEADER) != "1") if token else None
    return {"setup_needed": needs_setup(), "user": user, "permissions": PERMISSIONS,
            "idle_hours": SESSION_IDLE_HOURS}


@router.get("/api/auth/status")
def auth_status(request: Request) -> dict:
    return _status_for(request)


@router.post("/api/auth/setup")
def setup(body: SetupIn, request: Request, response: Response) -> dict:
    """Create the first admin. Only works while there are no users."""
    with _failures_lock:  # serialise so two first visitors cannot both become admin
        if not needs_setup():
            raise HTTPException(409, "Setup is already done; log in instead")
        user = create_user(body.username, body.password, "admin", [])
    _set_cookie(request, response, _start_session(user["id"]))
    return {**_status_for(request), "user": {**user, "session_expires_at": time.time() + IDLE_SECONDS}}


@router.post("/api/auth/login")
def login(body: Credentials, request: Request, response: Response) -> dict:
    key = f"{_client(request)}|{body.username.lower()}"
    if _throttled(key) or _throttled(_client(request)):
        raise HTTPException(429, "Too many failed attempts. Try again in 15 minutes.")
    user = _verify_login(body.username, body.password)
    if user is None:
        _record_failure(key)
        _record_failure(_client(request))
        raise HTTPException(401, "Wrong username or password")
    _set_cookie(request, response, _start_session(user["id"]))
    return {**_status_for(request), "user": {**user, "session_expires_at": time.time() + IDLE_SECONDS}}


@router.post("/api/auth/logout", status_code=204)
def logout(request: Request) -> Response:
    token = request.cookies.get(COOKIE)
    if token:
        _end_session(token)
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, path="/")
    return response


@router.post("/api/auth/password", status_code=204)
def change_password(body: PasswordChange, request: Request) -> Response:
    user = current_user(request)
    with db.connect() as conn:
        stored = conn.execute("SELECT password FROM users WHERE id = ?", (user["id"],)).fetchone()["password"]
    if not check_password(body.current_password, stored):
        raise HTTPException(400, "Current password is wrong")
    update_user(user["id"], password=body.new_password)  # ends every session, including this one
    token = _start_session(user["id"])
    response = Response(status_code=204)
    _set_cookie(request, response, token)
    return response


@router.get("/api/users")
def users() -> list[dict]:
    return list_users()


@router.get("/api/users/activity")
def user_activity() -> list[dict]:
    """Admins: what each user created, in which tool."""
    return db.activity()


@router.post("/api/users", status_code=201)
def add_user(body: UserIn) -> dict:
    return create_user(body.username, body.password, body.role, body.permissions)


@router.patch("/api/users/{uid}")
def edit_user(uid: str, body: UserUpdate) -> dict:
    return update_user(uid, **body.model_dump(exclude_none=True))


@router.delete("/api/users/{uid}", status_code=204)
def remove_user(uid: str, request: Request) -> Response:
    if uid == current_user(request)["id"]:
        raise HTTPException(400, "You cannot delete your own account")
    delete_user(uid)
    return Response(status_code=204)
