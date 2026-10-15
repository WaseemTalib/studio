"""ElevenLabs text to speech: models, voices, the shared voice library and synthesis.

API reference: https://elevenlabs.io/docs/api-reference
"""

from typing import Any

import requests

from ..config import ELEVENLABS_API, ELEVENLABS_API_KEY
from .common import TTSError

TIMEOUT = 120
PAGE_SIZE = 30


def available() -> bool:
    return bool(ELEVENLABS_API_KEY)


def _request(method: str, path: str, *, params: dict | None = None, json: dict | None = None,
             raw: bool = False) -> Any:
    if not available():
        raise TTSError("Set ELEVENLABS_API_KEY to use ElevenLabs voices")
    try:
        response = requests.request(method, ELEVENLABS_API + path, params=params, json=json, timeout=TIMEOUT,
                                    headers={"xi-api-key": ELEVENLABS_API_KEY})
    except requests.RequestException as e:
        raise TTSError("Could not reach ElevenLabs") from e
    if response.status_code >= 400:
        raise TTSError(_error_message(response))
    return response.content if raw else response.json()


def _error_message(response: requests.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, dict):  # {"status": "quota_exceeded", "message": "..."}
        status, message = detail.get("status"), detail.get("message")
        if status == "quota_exceeded":
            return f"ElevenLabs character quota used up: {message}"
        if status in ("invalid_api_key", "needs_authorization") or response.status_code == 401:
            return "ElevenLabs API key is missing or invalid (ELEVENLABS_API_KEY)"
        return f"ElevenLabs error {response.status_code}: {message or status}"
    if isinstance(detail, list) and detail:  # validation errors
        return "ElevenLabs rejected the request: " + "; ".join(str(d.get("msg", d)) for d in detail)
    if response.status_code == 401:
        return "ElevenLabs API key is missing or invalid (ELEVENLABS_API_KEY)"
    if response.status_code == 429:
        return "ElevenLabs rate limit reached; try again in a moment"
    return f"ElevenLabs error {response.status_code}: {response.text[:200]}"


def models() -> list[dict]:
    out = []
    for m in _request("GET", "/v1/models"):
        if not m.get("can_do_text_to_speech"):
            continue
        out.append({
            "id": m["model_id"], "name": m.get("name") or m["model_id"], "description": m.get("description") or "",
            "max_chars": m.get("maximum_text_length_per_request") or 5000,
            "languages": [l.get("name") for l in m.get("languages") or [] if l.get("name")],
            "controls": ["speed", "stability", "similarity"]
                        + (["style"] if m.get("can_use_style") else [])
                        + (["speaker_boost"] if m.get("can_use_speaker_boost") else []),
        })
    return out


def _voice(v: dict, *, shared: bool) -> dict:
    labels = v.get("labels") or {}
    pick = (lambda key: v.get(key)) if shared else (lambda key: labels.get(key))
    return {
        "provider": "elevenlabs",
        "id": v["voice_id"],
        "name": v.get("name") or v["voice_id"],
        "description": v.get("description") or labels.get("description") or pick("descriptive") or "",
        "gender": pick("gender"),
        "age": pick("age"),
        "accent": pick("accent"),
        "language": v.get("language") if shared else labels.get("language"),
        "use_case": pick("use_case") or labels.get("use case"),
        "category": v.get("category"),
        "preview_url": v.get("preview_url"),
        # A library voice is used through the user's account once added (see add_shared_voice).
        "public_owner_id": v.get("public_owner_id") if shared else None,
        "in_library": bool(v.get("is_added_by_user")) if shared else True,
        "popularity": v.get("cloned_by_count") if shared else None,
    }


def my_voices(kind: str, search: str | None, page_token: str | None) -> dict:
    """kind "default": ElevenLabs' own premade voices; "mine": voices in the user's account."""
    params = {"page_size": 100, "search": search or None, "next_page_token": page_token or None,
              "include_total_count": "false",
              "voice_type": "default" if kind == "default" else "non-default"}
    data = _request("GET", "/v2/voices", params={k: v for k, v in params.items() if v is not None})
    return {"voices": [_voice(v, shared=False) for v in data.get("voices", [])],
            "next": data.get("next_page_token") if data.get("has_more") else None}


def library_voices(filters: dict, page: int) -> dict:
    """The community voice library, most cloned first unless another sort is given."""
    params = {"page_size": PAGE_SIZE, "page": page, "sort": filters.get("sort") or "cloned_by_count"}
    for key in ("search", "gender", "age", "accent", "language", "category"):
        if filters.get(key):
            params[key] = filters[key]
    if filters.get("use_case"):
        params["use_cases"] = [filters["use_case"]]
    data = _request("GET", "/v1/shared-voices", params=params)
    return {"voices": [_voice(v, shared=True) for v in data.get("voices", [])],
            "next": str(page + 1) if data.get("has_more") else None}


def add_shared_voice(public_owner_id: str, voice_id: str, name: str) -> str:
    """Add a library voice to the user's account so it can be used; returns its id there."""
    data = _request("POST", f"/v1/voices/add/{public_owner_id}/{voice_id}", json={"new_name": name})
    return data["voice_id"]


def usage() -> dict:
    data = _request("GET", "/v1/user/subscription")
    return {"used": data.get("character_count"), "limit": data.get("character_limit"),
            "resets_at": data.get("next_character_count_reset_unix"), "tier": data.get("tier")}


def synthesize(text: str, voice_id: str, model_id: str, settings: dict) -> bytes:
    """MP3 audio (44.1 kHz, 128 kbps) of `text`."""
    voice_settings = {
        "speed": settings["speed"],
        "stability": settings["stability"],
        "similarity_boost": settings["similarity"],
        "style": settings["style"],
        "use_speaker_boost": settings["speaker_boost"],
    }
    return _request("POST", f"/v1/text-to-speech/{voice_id}", params={"output_format": "mp3_44100_128"},
                    json={"text": text, "model_id": model_id, "voice_settings": voice_settings}, raw=True)
