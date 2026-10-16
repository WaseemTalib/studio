"""Rate limits of hosted AI services, and asking the user what to do when one is reached.

A provider that hits a limit raises LimitReached with the details it got back (which limit,
how much is used, how long until it frees up). handle() then decides:

- A per-minute limit is waited out automatically: it clears within a minute and long
  transcripts hit it on almost every request. The wait is shown on the project.
- Anything longer is put to the user through the job's Decider (bound per worker thread by
  the runner): wait for the reset, switch to another option, or cancel the project.

Nothing switches services on its own.
"""

import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import cancel

# Limits that free up within a minute; everything else asks the user.
PER_MINUTE = {"RPM", "TPM", "ITPM", "OTPM"}
SHORT_WAIT_SECONDS = 90.0

SERVICE_LABELS = {
    "groq": "Groq", "gemini": "Google Gemini", "openrouter": "OpenRouter", "sambanova": "SambaNova",
    "local": "Whisper (this computer)",
}


@dataclass
class Limit:
    service: str
    model: str
    name: str                  # e.g. "tokens per day"
    code: str | None           # e.g. "TPD"
    limit: float | None
    used: float | None
    requested: float | None
    retry_after: float         # seconds until this request fits again
    message: str
    seen_at: float = field(default_factory=time.time)

    @property
    def remaining(self) -> float | None:
        return None if self.limit is None or self.used is None else max(0.0, self.limit - self.used)

    @property
    def short(self) -> bool:
        if self.code:
            return self.code in PER_MINUTE and self.retry_after <= SHORT_WAIT_SECONDS
        return self.retry_after <= SHORT_WAIT_SECONDS

    def public(self) -> dict:
        return {
            "service": self.service, "service_label": SERVICE_LABELS.get(self.service, self.service),
            "model": self.model, "name": self.name, "code": self.code, "limit": self.limit, "used": self.used,
            "requested": self.requested, "remaining": self.remaining,
            "resets_in": max(0.0, self.retry_after - (time.time() - self.seen_at)), "message": self.message,
            "recent": recent(self.service, self.model),
        }


class LimitReached(Exception):
    def __init__(self, limit: Limit):
        super().__init__(f"{SERVICE_LABELS.get(limit.service, limit.service)} {limit.name} limit reached "
                         f"for {limit.model}; it frees up in {format_wait(limit.retry_after)}")
        self.limit = limit


@dataclass
class Option:
    id: str                    # "service:model"
    label: str
    detail: str
    available: bool = True
    reason: str | None = None  # why it is not available, e.g. which key to add

    def public(self) -> dict:
        return {"id": self.id, "label": self.label, "detail": self.detail, "available": self.available,
                "reason": self.reason}


@dataclass
class Choice:
    action: str                # "wait" | "switch"
    option: str | None = None


# --- Reading limits -----------------------------------------------------------------


def parse_duration(text: str) -> float | None:
    """Groq's durations: "50.112s", "2m59.56s", "1h2m3s", "830ms"."""
    text = text.strip()
    if m := re.fullmatch(r"([\d.]+)ms", text):
        return float(m.group(1)) / 1000
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", text)
    if not m or not any(m.groups()):
        return None
    h, mi, s = m.groups()
    return int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)


def format_wait(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def _retry_after(headers: Any) -> float | None:
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return None


def from_groq(service: str, model: str, message: str, headers: Any) -> Limit:
    """Groq: "Rate limit reached for model `x` ... on tokens per day (TPD): Limit 200000, Used 197942,
    Requested 2174. Please try again in 50.112s." """
    if m := re.search(r"model `([^`]+)`", message):
        model = m.group(1)
    name, code, limit, used, requested = "rate", None, None, None, None
    if m := re.search(r"on ([a-z ]+?) \((\w+)\): Limit (\d+), Used (\d+), Requested (\d+)", message):
        name, code = m.group(1), m.group(2)
        limit, used, requested = float(m.group(3)), float(m.group(4)), float(m.group(5))
    wait = None
    if m := re.search(r"try again in ([\dhms.]+)", message):
        wait = parse_duration(m.group(1).rstrip("."))  # the sentence's full stop follows
    wait = wait if wait is not None else _retry_after(headers)
    return Limit(service, model, name, code, limit, used, requested, wait if wait is not None else 60.0, message)


def from_headers(service: str, model: str, message: str, headers: Any) -> Limit:
    """Services that only send retry-after."""
    wait = _retry_after(headers)
    return Limit(service, model, "rate", None, None, None, None, wait if wait is not None else 60.0, message)


# Latest rate-limit headers per service and model (Groq sends them with every answer).
_recent: dict[tuple[str, str], dict] = {}


def record(service: str, model: str, headers: Any) -> None:
    def num(name: str) -> float | None:
        try:
            return float(headers.get(name))
        except (TypeError, ValueError):
            return None

    def dur(name: str) -> float | None:
        value = headers.get(name)
        return parse_duration(value) if value else None

    info = {
        "requests_per_day": num("x-ratelimit-limit-requests"),
        "requests_left_today": num("x-ratelimit-remaining-requests"),
        "requests_reset_in": dur("x-ratelimit-reset-requests"),
        "tokens_per_minute": num("x-ratelimit-limit-tokens"),
        "tokens_left_this_minute": num("x-ratelimit-remaining-tokens"),
        "tokens_reset_in": dur("x-ratelimit-reset-tokens"),
        "at": time.time(),
    }
    if any(v is not None for k, v in info.items() if k != "at"):
        _recent[(service, model)] = info


def recent(service: str, model: str) -> dict | None:
    info = _recent.get((service, model))
    if not info:
        return None
    age = time.time() - info["at"]
    out = dict(info, age=age)
    for key in ("requests_reset_in", "tokens_reset_in"):
        if out[key] is not None:
            out[key] = max(0.0, out[key] - age)
    return out


# --- Asking the user ------------------------------------------------------------------


class Decider(Protocol):
    overrides: dict[str, str]          # stage -> option id chosen by the user
    patient: set[tuple]                # limits the user chose to wait out; not asked again

    def ask(self, stage: str, limit: Limit, options: list[Option]) -> Choice: ...
    def wait(self, stage: str, limit: Limit, options: list[Option]) -> Choice | None: ...
    def notice(self, limit: Limit) -> None: ...
    def clear(self) -> None: ...


_local = threading.local()


@contextmanager
def bound(decider: Decider) -> Iterator[None]:
    _local.decider = decider
    try:
        yield
    finally:
        _local.decider = None


def _current() -> Decider | None:
    return getattr(_local, "decider", None)


def override(stage: str) -> str | None:
    d = _current()
    return d.overrides.get(stage) if d else None


def handle(stage: str, limit: Limit, options: list[Option]) -> str | None:
    """Deal with a reached limit. Returns the option to switch to, or None to try the same
    service again (after waiting). Raises cancel.Cancelled if the user cancels, and
    LimitReached when there is nobody to ask (no project job)."""
    d = _current()
    key = (stage, limit.service, limit.model, limit.code)
    if limit.short or (d and key in d.patient):
        if d:
            d.notice(limit)
        try:
            cancel.sleep(limit.retry_after + 0.5)
        finally:
            if d:
                d.clear()
        return None
    if d is None:
        raise LimitReached(limit)
    choice = d.ask(stage, limit, options)
    while choice.action == "wait":
        d.patient.add(key)  # keep waiting on later hits of this limit too, without asking again
        switched = d.wait(stage, limit, options)
        if switched is None:
            return None
        choice = switched
    d.overrides[stage] = choice.option
    return choice.option
