"""Live speech to text: a browser's microphone relayed to Deepgram's streaming API.

Ported from gecko-notes' `routers/stt_stream.py`, protocol unchanged, so the apps' clients
port as they are: the browser sends audio chunks as binary frames and `{"type": "stop"}` when
the person stops; the suite answers with `{"type": "interim"|"final", "text"}` as Deepgram
hears the words, or `{"type": "error", "message"}`. The key never leaves the suite.

The suite adds what Notes' relay did not need: the caller's app is recorded with the seconds
Deepgram reports (`Metadata.duration`), as a batch transcription is.

`open_upstream` is the seam the tests replace: it opens the websocket to Deepgram.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, Protocol

from app.config import settings
from app.speech import deepgram

logger = logging.getLogger(__name__)

# Close codes the clients already know (Notes' `deepgramStream.ts`), plus the suite's own.
CLOSE_NO_KEY = 4400
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_BUSY = 4409

# How long Deepgram gets to send its last finals after the person stops.
FLUSH_SECONDS = 3.0


class Upstream(Protocol):
    async def send(self, data: Any) -> None: ...
    def __aiter__(self) -> AsyncIterator[Any]: ...
    async def close(self) -> None: ...


def listen_url(model: str, language: Optional[str]) -> str:
    base = settings.deepgram_base_url.rstrip("/")
    base = "wss://" + base[len("https://"):] if base.startswith("https://") else (
        "ws://" + base[len("http://"):] if base.startswith("http://") else base)
    url = f"{base}/v1/listen?model={model}&interim_results=true&smart_format=true&punctuate=true"
    if language:
        url += f"&language={language}"
    return url


async def _connect(url: str, api_key: str) -> Upstream:
    from websockets.asyncio.client import connect

    return await connect(url, additional_headers={"Authorization": f"Token {api_key}"},
                         open_timeout=15)


open_upstream: Callable[[str, str], Awaitable[Upstream]] = _connect


async def relay(client, upstream: Upstream) -> float:
    """Pump audio one way and words the other until the person stops or either side ends.

    `client` is a Starlette WebSocket. Returns the seconds of audio Deepgram reported, for
    the usage ledger.
    """
    seconds = 0.0

    async def client_to_upstream() -> None:
        while True:
            message = await client.receive()
            if message["type"] == "websocket.disconnect":
                return
            data = message.get("bytes")
            if data is not None:
                await upstream.send(data)
                continue
            try:
                control = json.loads(message.get("text") or "")
            except (TypeError, ValueError):
                continue
            if isinstance(control, dict) and control.get("type") == "stop":
                # Deepgram flushes what it has heard, sends the last finals, then closes.
                await upstream.send(json.dumps({"type": "CloseStream"}))
                return

    async def upstream_to_client() -> None:
        nonlocal seconds
        async for raw in upstream:
            try:
                event = json.loads(raw)
            except (TypeError, ValueError):
                continue
            kind = event.get("type")
            if kind == "Results":
                alternatives = (event.get("channel") or {}).get("alternatives") or [{}]
                text = (alternatives[0].get("transcript") or "").strip()
                if text:
                    await client.send_json({"type": "final" if event.get("is_final") else "interim",
                                            "text": text})
            elif kind == "Metadata":
                duration = event.get("duration")
                if isinstance(duration, (int, float)):
                    seconds = float(duration)
            elif kind == "Error":
                await client.send_json({"type": "error", "message": event.get("message")
                                        or event.get("description") or "Deepgram reported an error"})

    up = asyncio.create_task(client_to_upstream())
    down = asyncio.create_task(upstream_to_client())
    try:
        done, _ = await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
        if up in done and not down.done():
            # The person stopped (or went away): give Deepgram a moment to send its last
            # finals, which is what makes the end of a sentence appear after the button.
            try:
                await asyncio.wait_for(down, timeout=FLUSH_SECONDS)
            except asyncio.TimeoutError:
                pass
        elif down in done and not up.done():
            # Deepgram ended on its own (an idle timeout, a network drop): say so, rather
            # than closing as if all were well.
            try:
                await client.send_json({"type": "error", "message": "The dictation connection was lost."})
            except Exception:
                pass
        for task in (up, down):
            if task.done() and not task.cancelled() and task.exception():
                logger.warning("Live dictation relay ended with %r", task.exception())
    finally:
        for task in (up, down):
            task.cancel()
        try:
            await upstream.close()
        except Exception:
            pass
    return seconds


def billable_seconds(seconds: float) -> int:
    return math.ceil(seconds)


DEFAULT_MODEL = deepgram.DEFAULT_MODEL
