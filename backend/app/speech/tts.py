"""Text to speech, through whichever model `speech.tts` is routed to.

Ported from Notes' `routers/settings.py` (`synthesize_tts_bytes`, `_fal_tts`,
`_deepgram_tts`), which is what Gecko Video Creator's narration has been calling. Two
engines, as there: Deepgram's Flux voices and fal.ai's (ElevenLabs and others, each endpoint
with its own request fields).

**Routed, not parameterised.** Unlike fal's endpoints and Deepgram's STT models, which an
app names, the voice is a setting a person makes once: Video Creator has no voice setting of
its own to send. So `speech.tts` is routed to a model row that carries everything this needs:
its `voices`, and `params` for the engine's dialect (`text_field`, `voice_field`,
`extra_params`, `default_voice`; for Deepgram `expressivity` and `speed`).

**One video, one voice.** The first call of a narration answers with the model and voice it
resolved (`X-Speech-Model`, `X-Speech-Voice`); the caller sends them back for every later
chunk. A request that names a model uses it and nothing else: if that model fails the call
fails, where Notes' "auto" quietly fell back from Deepgram to fal. That fallback is
deliberately gone. It handed back a video whose narrator changes voice partway through,
which is worse than an error, and routing already says which engine is wanted.

**Billed once.** Audio is cached on disk by provider, model, voice, settings and text, so a
preview followed by a full render reads the narration once. A hit records nothing.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlmodel import Session

from app import crypto
from app.auth import UserCtx
from app.config import settings
from app.gateway import upstream
from app.gateway.chat import upstream_http_error
from app.gateway.types import UpstreamError
from app.media import fal, pricing
from app.models.ai import AIModel, AIProvider
from app.models.usage import UsageEvent
from app.safe_url import require_safe_external_url
from app.services.access import app_for, bad_request, not_found
from app.services.registry import capabilities_of, load_json, resolve, usable, visible_model
from app.vocab import VENDOR_DEEPGRAM, VENDOR_FAL

TASK = "speech.tts"
MIN_AUDIO_BYTES = 256
REQUEST_TIMEOUT = 120.0

# Deepgram rejects out-of-range or fractional values rather than clamping them.
EXPRESSIVITY = (-2, 2)
SPEED = (0.85, 1.15)


@dataclass
class Spoken:
    audio: bytes
    media_type: str
    vendor: str
    model_ref: str
    voice: str
    cached: bool


def speak(session: Session, user: UserCtx, *, text: str, model_ref: Optional[str],
          voice: Optional[str], app: Optional[str], subject_ref: Optional[str]) -> Spoken:
    text = (text or "").strip()
    if not text:
        raise bad_request("empty_text", "No text to synthesize")
    if len(text) > settings.tts_max_chars:
        raise bad_request("text_too_long", f"Text exceeds {settings.tts_max_chars} characters")

    model, provider = _target(session, user, model_ref, app_for(user, app))
    params = load_json(model.params, {}) or {}
    chosen = _voice(model, params, voice)

    key = crypto.decrypt(provider.api_key)
    if not key:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "code": "provider_key_missing", "message": f"{provider.name} has no usable API key."})

    cache_key = _cache_key(provider, model, params, chosen, text)
    cached = _cache_get(cache_key)
    if cached is not None:
        return Spoken(cached, "audio/mpeg", provider.vendor, model.id, chosen, True)

    try:
        if provider.vendor == VENDOR_DEEPGRAM:
            audio, media_type, billed, request_id = _deepgram(key, chosen, text, params)
        else:
            audio, media_type, billed, request_id = _fal(key, model, params, chosen, text)
    except UpstreamError as exc:
        raise upstream_http_error(exc) from exc

    if len(audio) < MIN_AUDIO_BYTES:
        raise HTTPException(status_code=502, detail={
            "code": "audio_empty",
            "message": f"{provider.name} returned {len(audio)} bytes, which is not audio, for: {text[:80]!r}"})
    if len(audio) > settings.tts_max_audio_mb * 1024 * 1024:
        raise HTTPException(status_code=502, detail={
            "code": "audio_too_large", "message": "Generated audio exceeds the size limit"})
    if "mpeg" in media_type or "mp3" in media_type:
        _cache_put(cache_key, audio)

    _record(session, user, app_for(user, app), provider, model, chosen, text, billed, request_id,
            key, subject_ref)
    return Spoken(audio, media_type, provider.vendor, model.id, chosen, False)


# ─── which model, which voice ────────────────────────────────────────────────


def _target(session: Session, user: UserCtx, model_ref: Optional[str],
            app: Optional[str]) -> tuple[AIModel, AIProvider]:
    if model_ref:
        model = visible_model(session, model_ref, user.id)
        if model is None:
            raise not_found("model")
        provider = session.get(AIProvider, model.provider_id)
    else:
        hit = resolve(session, user.id, TASK, app)
        if hit is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
                "code": "ai_not_configured",
                "message": "No text-to-speech model is set up. In Gecko Suite, add a model with "
                           "the tts capability to a fal.ai or Deepgram provider and route "
                           "speech.tts to it."})
        model, provider = hit.model, hit.provider
    if not usable(model, provider):
        raise bad_request("model_disabled", f"{model.label or model.model_id} is switched off")
    if "tts" not in capabilities_of(model) or provider.vendor not in (VENDOR_FAL, VENDOR_DEEPGRAM):
        raise bad_request("missing_capability",
                          f"{model.label or model.model_id} is not a text-to-speech model")
    return model, provider


def _voice_ids(model: AIModel) -> list[str]:
    voices = load_json(model.voices, []) or []
    return [str(v["id"] if isinstance(v, dict) else v) for v in voices
            if (isinstance(v, dict) and v.get("id")) or (isinstance(v, str) and v)]


def _voice(model: AIModel, params: dict, requested: Optional[str]) -> str:
    """The requested voice if the model offers it, else the model's default.

    An unknown voice is coerced rather than refused, as in Notes: a stale voice saved for a
    different model must read in a voice this one supports, not fail the whole render."""
    offered = _voice_ids(model)
    requested = (requested or "").strip()
    if requested and (not offered or requested in offered):
        return requested
    return str(params.get("default_voice") or (offered[0] if offered else ""))


# ─── the engines ─────────────────────────────────────────────────────────────


def _deepgram(key: str, voice: str, text: str, params: dict) -> tuple[bytes, str, Optional[str], Optional[str]]:
    """Flux through `/v2/speak`. The voice *is* Deepgram's `model` parameter."""
    if not voice or not voice.replace("-", "").replace("_", "").isalnum():
        # It goes into a query string the key is sent with.
        raise bad_request("bad_voice", f"{voice!r} is not a Deepgram voice")
    response = upstream.post_json(
        f"{settings.deepgram_base_url.rstrip('/')}/v2/speak",
        headers={"Authorization": f"Token {key}", "Content-Type": "application/json"},
        body={"text": text}, timeout=REQUEST_TIMEOUT, label="Deepgram",
        params={"model": voice, "encoding": "mp3",
                "expressivity": _int_in(params.get("expressivity"), EXPRESSIVITY, 0),
                "speed": f"{_float_in(params.get('speed'), SPEED, 1.0):.2f}"})
    if response.status_code in (401, 403):
        raise UpstreamError("Deepgram rejected its API key. Check it in Gecko Suite's providers.",
                            code="upstream_auth")
    if response.status_code >= 400:
        raise UpstreamError(f"Deepgram: {upstream.error_detail(response)}")
    # Deepgram sends no billed quantity; the characters are recorded, the cost is unknown.
    return response.content, "audio/mpeg", None, None


def _fal(key: str, model: AIModel, params: dict, voice: str, text: str) -> tuple[bytes, str, Optional[str], Optional[str]]:
    """fal's synchronous endpoint, then the audio from the URL it names."""
    endpoint = model.model_id
    if not fal.valid_endpoint_id(endpoint):
        raise bad_request("bad_endpoint", f"{endpoint!r} is not a fal.ai endpoint id")
    # Endpoints disagree on field names (`text` vs `prompt`, `voice` vs `voice_id`) and some
    # need extras, so the model row says which; anything it does not say is the common shape.
    body: dict[str, Any] = {str(params.get("text_field") or "text"): text}
    if voice:
        body[str(params.get("voice_field") or "voice")] = voice
    extra = params.get("extra_params")
    if isinstance(extra, dict):
        body.update(extra)

    response = upstream.post_json(
        f"{settings.fal_run_base_url.rstrip('/')}/{endpoint}", headers=fal.auth_headers(key),
        body=body, timeout=REQUEST_TIMEOUT, label=fal.LABEL)
    if response.status_code >= 400:
        raise fal.error_for(response)
    try:
        audio = response.json()["audio"]
        url = audio["url"]
    except (ValueError, KeyError, TypeError) as exc:
        raise UpstreamError("Unexpected fal.ai response: no audio in it", code="upstream_error") from exc

    # fal's URLs are ephemeral and, unlike its queue's, are fetched without the key.
    try:
        require_safe_external_url(url)
    except HTTPException as exc:
        raise UpstreamError("fal.ai named an audio address this service will not fetch",
                            code="upstream_error") from exc
    download = upstream.request("GET", url, headers={}, timeout=REQUEST_TIMEOUT, label="the audio download")
    if download.status_code >= 400:
        raise UpstreamError(f"Audio download returned HTTP {download.status_code}")
    media_type = (audio.get("content_type") if isinstance(audio, dict) else None) \
        or download.headers.get("content-type") or "audio/mpeg"
    return (download.content, media_type, response.headers.get("x-fal-billable-units"),
            response.headers.get("x-fal-request-id"))


def _int_in(value: Any, bounds: tuple[int, int], default: int) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) \
        and bounds[0] <= value <= bounds[1] else default


def _float_in(value: Any, bounds: tuple[float, float], default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) \
        and bounds[0] <= value <= bounds[1] else default


# ─── the bill ────────────────────────────────────────────────────────────────


def _record(session: Session, user: UserCtx, app: Optional[str], provider: AIProvider,
            model: AIModel, voice: str, text: str, billed: Optional[str],
            request_id: Optional[str], key: str, subject_ref: Optional[str]) -> None:
    cost: Optional[float] = None
    currency: Optional[str] = None
    estimated = True
    if provider.vendor == VENDOR_FAL:
        # fal's own count of what it billed (its unit, often thousands of characters) times
        # its own price per that unit: exact. Anything less is unknown, never 0.
        quantity = pricing._parse_quantity(billed)
        price = pricing.list_price(provider.id, model.model_id, api_key=key) if quantity is not None else None
        if quantity is not None and price is not None:
            cost, currency, estimated = round(quantity * price.unit_price, 6), price.currency, False
    session.add(UsageEvent(
        user_id=user.id, app=app, task=TASK, kind="tts", provider_id=provider.id,
        provider_name=provider.name, vendor=provider.vendor,
        model=model.model_id if provider.vendor == VENDOR_FAL else voice,
        units=len(text), unit_type="chars", cost=cost, currency=currency,
        cost_estimated=estimated, external_ref=request_id, subject_ref=subject_ref))
    session.commit()


# ─── the cache ───────────────────────────────────────────────────────────────


def _cache_dir() -> Path:
    return settings.resolved_data_dir / "tts_cache"


def _cache_key(provider: AIProvider, model: AIModel, params: dict, voice: str, text: str) -> str:
    """Everything that changes the audio: whose key, which model, the engine's settings
    (expressivity and speed alter the sound as much as the voice does), the voice, the words.
    The provider is in it so a personal key and the shared one never share audio: a hit
    records no usage, and one person's spend must not become another's free read."""
    shaping = repr(sorted((k, params[k]) for k in ("expressivity", "speed", "extra_params", "text_field",
                                                   "voice_field") if k in params))
    raw = f"{provider.id}\x00{model.model_id}\x00{shaping}\x00{voice}\x00{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> Optional[bytes]:
    try:
        data = (_cache_dir() / f"{key}.mp3").read_bytes()
    except OSError:
        return None
    # Too small to be audio: an empty or error body cached before the providers checked for
    # that. A miss, so it is fetched again and overwritten rather than served to every
    # later render of the same words.
    return data if len(data) >= MIN_AUDIO_BYTES else None


def _cache_put(key: str, data: bytes) -> None:
    """Best effort: any filesystem error just means the next read is live."""
    try:
        directory = _cache_dir()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{key}.mp3").write_bytes(data)
        files = sorted(directory.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
        for old in files[:-settings.tts_cache_max_files]:
            try:
                old.unlink()
            except OSError:
                pass
    except OSError:
        pass
