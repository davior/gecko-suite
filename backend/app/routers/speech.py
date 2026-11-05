"""Speech: audio to text now; text to audio next. See `app/speech/`."""

from __future__ import annotations

from typing import Optional

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, WebSocket
from pydantic import BaseModel, Field
from fastapi.concurrency import run_in_threadpool
from sqlmodel import Session

from app.auth import CurrentUser, origin_allowed, user_from_token
from app.database import get_session
from app.schemas import DataResponse
from app import crypto
from app.models.usage import UsageEvent
from app.services.access import app_for
from app.services.vendor_keys import find_provider
from app.speech import deepgram, live, stt, tts
from app.vocab import VENDOR_DEEPGRAM

logger = logging.getLogger(__name__)

# One live dictation per person, as in Notes: a second tab's mic would otherwise double the
# minutes, and nobody dictates into two places at once.
_live_sessions: set[str] = set()

router = APIRouter()


@router.get("/stt/ready", response_model=DataResponse[stt.Readiness])
def stt_ready(user: CurrentUser, session: Session = Depends(get_session)):
    return DataResponse(data=stt.readiness(session, user))


@router.post("/stt", response_model=DataResponse[stt.Transcript])
async def transcribe(
    request: Request,
    user: CurrentUser,
    session: Session = Depends(get_session),
    model: str = Query(default=deepgram.DEFAULT_MODEL, max_length=60),
    language: Optional[str] = Query(default=None, max_length=20),
    app: Optional[str] = Query(default=None),
    subject_ref: Optional[str] = Query(default=None, max_length=300),
):
    """Transcribe the audio in the request body (FLAC is what GAM sends; Deepgram sniffs the
    container). Raw bytes, not multipart: a long recording is tens of megabytes, and
    multipart would parse it twice.

    The body is read here, on the event loop, and the call to Deepgram, which holds the
    connection for minutes, runs in a worker thread so it never blocks other requests.
    """
    audio = await request.body()
    content_type = request.headers.get("content-type", "audio/flac").split(";")[0].strip()
    result = await run_in_threadpool(
        stt.transcribe, session, user, audio, content_type=content_type, model=model,
        language=language, app=app, subject_ref=subject_ref)
    return DataResponse(data=result)


class SpeakRequest(BaseModel):
    text: str
    # Pins the model a narration began on: the `X-Speech-Model` of its first answer.
    # Omitted, the routed `speech.tts` model is used.
    model_ref: Optional[str] = None
    # Omitted, the model's default voice. A voice the model does not offer is replaced by it.
    voice: Optional[str] = None
    app: Optional[str] = None
    subject_ref: Optional[str] = Field(default=None, max_length=300)


@router.post("/tts")
def speak(body: SpeakRequest, user: CurrentUser, session: Session = Depends(get_session)):
    """The text as audio. The body of the response *is* the audio, not an envelope, so a
    caller can hand it straight to ffmpeg; what resolved comes back as headers."""
    spoken = tts.speak(session, user, text=body.text, model_ref=body.model_ref, voice=body.voice,
                       app=body.app, subject_ref=body.subject_ref)
    return Response(content=spoken.audio, media_type=spoken.media_type, headers={
        "X-Speech-Provider": spoken.vendor, "X-Speech-Model": spoken.model_ref,
        "X-Speech-Voice": spoken.voice, "X-Speech-Cached": "true" if spoken.cached else "false"})


@router.websocket("/stt/stream")
async def live_transcription(
    websocket: WebSocket,
    session: Session = Depends(get_session),
    token: str = Query(default=""),
    app: Optional[str] = Query(default=None),
    model: str = Query(default=live.DEFAULT_MODEL, max_length=60),
    language: Optional[str] = Query(default=None, max_length=20),
):
    """Live dictation (see `app/speech/live.py`). The token travels in the query because a
    browser websocket cannot set headers; the page's origin is checked because CORS does not
    cover websockets. Refusals are close codes the apps' clients already turn into sentences."""
    await websocket.accept()
    if not origin_allowed(websocket.headers.get("origin"), websocket.headers.get("host")):
        await websocket.close(code=live.CLOSE_FORBIDDEN, reason="origin_not_allowed")
        return
    try:
        user = user_from_token(session, token)
        attributed = app_for(user, app)
    except HTTPException as exc:
        code = live.CLOSE_UNAUTHORIZED if exc.status_code == 401 else live.CLOSE_FORBIDDEN
        await websocket.close(code=code, reason=str((exc.detail or {}).get("code", "refused")))
        return
    if not model.replace("-", "").replace(".", "").replace("_", "").isalnum() or (
            language and not language.replace("-", "").isalnum()):
        await websocket.close(code=live.CLOSE_FORBIDDEN, reason="bad_parameters")
        return
    provider = find_provider(session, user.id, VENDOR_DEEPGRAM)
    if provider is None:
        await websocket.close(code=live.CLOSE_NO_KEY, reason="no_deepgram_key")
        return
    if user.id in _live_sessions:
        await websocket.close(code=live.CLOSE_BUSY, reason="stream_already_active")
        return

    _live_sessions.add(user.id)
    seconds = 0.0
    try:
        try:
            upstream = await live.open_upstream(live.listen_url(model, language),
                                                crypto.decrypt(provider.api_key))
        except Exception as exc:
            logger.warning("Could not reach Deepgram for live dictation: %r", exc)
            await websocket.send_json({"type": "error", "message": "Could not reach Deepgram. Try again."})
            await websocket.close()
            return
        seconds = await live.relay(websocket, upstream)
    finally:
        _live_sessions.discard(user.id)
        # Recorded before the close below, so whoever sees the stream end sees it counted.
        if seconds > 0:
            session.add(UsageEvent(
                user_id=user.id, app=attributed, task="speech.stt", kind="stt",
                provider_id=provider.id, provider_name=provider.name, vendor=VENDOR_DEEPGRAM,
                model=model, units=live.billable_seconds(seconds), unit_type="seconds", cost=None,
                subject_ref="live-dictation"))
            session.commit()
    try:
        await websocket.close()
    except Exception:
        pass
