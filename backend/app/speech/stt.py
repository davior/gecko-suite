"""Speech to text through Deepgram, for an app's audio.

Thin like the fal gateway: the caller names the Deepgram model and sends the audio, and the
suite adds the key, records the seconds and (when it knows a price) the cost. The key is the
caller's visible Deepgram provider, not task routing: the model is a parameter of the call.
"""

from __future__ import annotations

import math
from typing import Optional

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session

from app import crypto
from app.auth import UserCtx
from app.config import settings
from app.gateway.chat import upstream_http_error
from app.gateway.types import UpstreamError
from app.models.usage import UsageEvent
from app.services.access import app_for, bad_request
from app.services.vendor_keys import find_provider
from app.speech import deepgram
from app.vocab import VENDOR_DEEPGRAM


class Word(BaseModel):
    text: str
    start: float
    end: float


class Segment(BaseModel):
    text: str
    start: float
    end: float
    speaker: Optional[int] = None
    words: list[Word] = []


class Transcript(BaseModel):
    segments: list[Segment]
    language: str
    model: str
    duration_seconds: float
    # The price of Deepgram's prerecorded minutes is not in the suite's catalogue, so a cost
    # is unknown, never 0: the seconds are recorded and the readout says they are not costed.
    cost: Optional[float] = None
    currency: Optional[str] = None
    usage_event_id: str


class Readiness(BaseModel):
    ready: bool
    provider: Optional[str] = None


def readiness(session: Session, user: UserCtx) -> Readiness:
    """Whether a transcription would find a Deepgram key. An app asks before queueing work
    that is certain to fail (GAM's upload chain, which would otherwise put a red row in the
    activity feed after every upload)."""
    provider = find_provider(session, user.id, VENDOR_DEEPGRAM)
    return Readiness(ready=provider is not None, provider=provider.name if provider else None)


def transcribe(session: Session, user: UserCtx, audio: bytes, *, content_type: str, model: str,
               language: Optional[str], app: Optional[str],
               subject_ref: Optional[str]) -> Transcript:
    if not audio:
        raise bad_request("empty_audio", "No audio was sent")
    limit = settings.stt_max_upload_mb * 1024 * 1024
    if len(audio) > limit:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail={
            "code": "audio_too_large",
            "message": f"That audio is over {settings.stt_max_upload_mb} MB. Send it in parts."})
    if not model.replace("-", "").replace(".", "").replace("_", "").isalnum():
        # The model goes into a query string the key is sent with.
        raise bad_request("bad_model", f"{model!r} is not a Deepgram model name")
    provider = find_provider(session, user.id, VENDOR_DEEPGRAM)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "code": "ai_not_configured",
            "message": "No Deepgram key is set up. Add a Deepgram provider in Gecko Suite."})
    try:
        result = deepgram.transcribe(audio, crypto.decrypt(provider.api_key),
                                     content_type=content_type, model=model, language=language)
    except UpstreamError as exc:
        raise upstream_http_error(exc) from exc

    event = UsageEvent(
        user_id=user.id, app=app_for(user, app), task="speech.stt", kind="stt",
        provider_id=provider.id, provider_name=provider.name, vendor=VENDOR_DEEPGRAM,
        model=result["model"], units=math.ceil(result["duration_seconds"]), unit_type="seconds",
        cost=None, subject_ref=subject_ref)
    session.add(event)
    session.commit()
    session.refresh(event)
    return Transcript(**result, usage_event_id=event.id)
