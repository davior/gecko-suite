"""The gateway endpoints: what the other apps call to use AI."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from sqlmodel import Session

from app.auth import CurrentUser
from app.database import get_session
from app.gateway import chat, embed, raw
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


# ─── the raw passthrough (M4): Notes' bodies in, Notes' shapes out. See gateway/raw.py ───

_STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _raw(protocol: str, request_model):
    def blocking(request, user: CurrentUser, session: Session = Depends(get_session)):
        # The vendor's own dict, not an envelope: Notes' frontend reads it as it always did.
        return JSONResponse(content=raw.complete(session, user, protocol, request))

    def streaming(request, user: CurrentUser, session: Session = Depends(get_session)):
        return StreamingResponse(raw.stream(session, user, protocol, request),
                                 media_type="text/event-stream", headers=_STREAM_HEADERS)

    # This module postpones annotations, so a closure's `request_model` is only a string
    # FastAPI cannot resolve (it would read `request` as a query parameter). Set the class.
    for fn, suffix in ((blocking, ""), (streaming, "/stream")):
        fn.__annotations__["request"] = request_model
        fn.__name__ = f"proxy_{protocol}{suffix.replace('/', '_')}"
        router.post(f"/proxy/{protocol}{suffix}")(fn)


_raw("anthropic", raw.AnthropicRequest)
_raw("openai", raw.OpenAIRequest)
_raw("ollama", raw.OllamaRequest)
