"""The automatic AI chain: Claude first, then DeepSeek, then Groq (SHORTCUT_AI_CHAIN).

Each request goes to the first service in the chain that is not known to be down. A service that is out
of hours, out of quota, rate-limited or unreachable is marked down and skipped, so no requests are wasted
on it, until its recheck time (Claude 10 min, DeepSeek 3 min, a rate limit's own reset time); the first
request after that rechecks it, so the chain returns to Claude as soon as Claude answers again.

Only when every service in the chain is down does the user see the usage-limit dialog (wait or switch).
"""

import logging
import threading
import time
from dataclasses import dataclass

from ..config import AI_CHAIN, GROQ_FAST_MODEL, GROQ_LLM_MODEL, RECHECK_MINUTES
from . import limits

log = logging.getLogger(__name__)

DEFAULT_RECHECK = 5 * 60.0
MIN_LIMIT_WAIT = 30.0
MAX_QUIET_ROUNDS = 3  # rounds of brief waits with the whole chain down before asking the user


@dataclass
class _Health:
    down_until: float = 0.0
    reason: str = ""
    last_ok: float = 0.0


_health: dict[str, _Health] = {}
_lock = threading.Lock()
_current: str | None = None  # the service that answered last, to log switches


def _h(service: str) -> _Health:
    return _health.setdefault(service, _Health())


def usable(service: str) -> bool:
    with _lock:
        return time.time() >= _h(service).down_until


def mark_ok(service: str) -> None:
    global _current
    with _lock:
        h = _h(service)
        h.down_until, h.reason, h.last_ok = 0.0, "", time.time()
        if _current != service:
            log.info("AI chain: now using %s", service)
            _current = service


def mark_down(service: str, seconds: float, reason: str) -> None:
    with _lock:
        h = _h(service)
        h.down_until, h.reason = time.time() + seconds, reason
    log.info("AI chain: %s unavailable for %.0f s (%s)", service, seconds, reason)


def recheck_seconds(service: str) -> float:
    return RECHECK_MINUTES.get(service, DEFAULT_RECHECK / 60) * 60


def choices(clip_model: str, available: dict[str, str]) -> list[str]:
    """The chain as "service:model" choices, leaving out services without a key."""
    out = []
    for service in AI_CHAIN:
        if service == "groq":
            model = GROQ_FAST_MODEL if clip_model == "fast" else GROQ_LLM_MODEL
        else:
            model = available.get(service, "")
        if service in available and model:
            out.append(f"{service}:{model}")
    return out


def status(labels: dict[str, str], available: dict[str, str]) -> list[dict]:
    now = time.time()
    with _lock:
        out = []
        for service in AI_CHAIN:
            h = _h(service)
            state = ("no key" if service not in available else
                     "down" if h.down_until > now else "in use" if service == _current else "ready")
            out.append({"service": service, "label": labels.get(service, service), "state": state,
                        "reason": h.reason if h.down_until > now else "",
                        "retry_in": max(0.0, h.down_until - now) if h.down_until > now else None})
        return out


def earliest_limit(chain: list[str], last: limits.LimitReached | None) -> limits.Limit:
    """What to show when the whole chain is down: the last usage limit, or the service back soonest."""
    if last is not None:
        return last.limit
    with _lock:
        service = min((c.partition(":")[0] for c in chain), key=lambda s: _h(s).down_until)
        h = _h(service)
        wait = max(1.0, h.down_until - time.time())
        reason = h.reason
    return limits.Limit(service, "AI chain", "availability", None, None, None, None, wait,
                        f"Every AI service in the chain is unavailable; {service} is rechecked first. {reason}")
