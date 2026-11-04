"""Speech: audio to text now; text to audio next. See `app/speech/`."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from fastapi.concurrency import run_in_threadpool
from sqlmodel import Session

from app.auth import CurrentUser
from app.database import get_session
from app.schemas import DataResponse
from app.speech import deepgram, stt, tts

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
