"""The gateway endpoints: what the other apps call to use AI."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from app.auth import CurrentUser
from app.database import get_session
from app.gateway import chat, embed
from app.gateway.types import ChatRequest, ChatResult
from app.schemas import DataResponse

router = APIRouter()


@router.post("/chat", response_model=DataResponse[ChatResult])
def chat_endpoint(request: ChatRequest, user: CurrentUser, session: Session = Depends(get_session)):
    """One model call. With `stream: true`, Server-Sent Events instead of JSON.

    Synchronous on purpose: FastAPI runs it in a threadpool, so a five-minute
    generation holds a worker thread and never the event loop.
    """
    if request.stream:
        frames = chat.run_stream(session, user, request)
        return StreamingResponse(
            frames,
            media_type="text/event-stream",
            # Nginx buffers proxied responses by default, which would hold every frame
            # until the end and turn a stream back into a long wait.
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return DataResponse(data=chat.run(session, user, request))


@router.post("/embed", response_model=DataResponse[embed.EmbedResult])
def embed_endpoint(request: embed.EmbedRequest, user: CurrentUser, session: Session = Depends(get_session)):
    """Vectors for a batch of texts, in order. Nothing is stored but the usage row."""
    return DataResponse(data=embed.run(session, user, request))
