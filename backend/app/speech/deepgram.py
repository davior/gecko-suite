"""Deepgram's prerecorded speech-to-text, and its response as segments.

Ported from GAM's `enrichment/deepgram.py`, which holds the reasoning for the parameters:
`utterances=true` is what produces sensible segments (without it Deepgram returns one wall
of text per channel, useless for jumping to a moment), and `diarize=true` labels speakers,
which matters for interviews.

Changed from GAM's: the key and the audio come from the caller of this service, failures are
`UpstreamError`s with a code, and parsing returns plain dicts for the API to send on.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from app.config import settings
from app.gateway import upstream
from app.gateway.types import UpstreamError

LABEL = "Deepgram"
DEFAULT_MODEL = "nova-3"

# A long recording is a long upload followed by a long transcription, and Deepgram holds
# the connection for both. Generous, because failing at minute nine of a ten-minute job
# wastes the whole call and its cost.
REQUEST_TIMEOUT = 1800.0


def transcribe(audio: bytes, api_key: str, *, content_type: str, model: str,
               language: Optional[str]) -> dict:
    params: dict[str, Any] = {"model": model, "smart_format": "true", "punctuate": "true",
                              "utterances": "true", "diarize": "true"}
    if language:
        params["language"] = language
    else:
        # Let Deepgram pick, rather than assuming English of a library that may not be.
        params["detect_language"] = "true"
    response = upstream.post_bytes(
        f"{settings.deepgram_base_url.rstrip('/')}/v1/listen", params=params, content=audio,
        headers={"Authorization": f"Token {api_key}", "Content-Type": content_type},
        timeout=REQUEST_TIMEOUT, label=LABEL)
    if response.status_code == 401:
        # The one failure a person can fix, so it is named rather than buried in a status.
        raise UpstreamError("Deepgram rejected its API key. Check it in Gecko Suite's providers.",
                            code="upstream_auth")
    if response.status_code == 402:
        raise UpstreamError("The Deepgram account has no credit remaining", status=402,
                            code="upstream_quota")
    if response.status_code >= 400:
        raise UpstreamError(f"Deepgram returned {response.status_code}: {_detail(response)}",
                            status=422 if response.status_code == 400 else 502,
                            code="upstream_invalid" if response.status_code == 400 else "upstream_error")
    try:
        payload = response.json()
    except ValueError as exc:
        raise UpstreamError("Deepgram returned a response that was not JSON") from exc
    return parse_response(payload, requested_model=model)


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return (response.text or "").strip()[:200] or "no detail"
    if isinstance(body, dict):
        return str(body.get("err_msg") or body.get("message") or body)[:200]
    return str(body)[:200]


def parse_response(payload: dict[str, Any], *, requested_model: str = DEFAULT_MODEL) -> dict:
    """{"segments", "language", "model", "duration_seconds"} from Deepgram's response."""
    results = payload.get("results") or {}
    metadata = payload.get("metadata") or {}

    model = requested_model
    # Deepgram reports what it actually ran under a uuid key; the name is what matters.
    for info in (metadata.get("model_info") or {}).values():
        if isinstance(info, dict) and info.get("name"):
            model = str(info["name"])
            break

    utterances = results.get("utterances")
    if utterances:
        segments = [_segment(u) for u in utterances if _has_text(u)]
    else:
        # utterances=true was requested, so this is a fallback for an unexpected response.
        segments = _segments_from_channels(results)

    duration = _float(metadata.get("duration")) or (segments[-1]["end"] if segments else 0.0)
    return {"segments": segments, "language": _language(results), "model": model,
            "duration_seconds": duration}


def _has_text(utterance: Any) -> bool:
    return isinstance(utterance, dict) and bool((utterance.get("transcript") or "").strip())


def _words(items: Any) -> list[dict]:
    return [{
        # punctuated_word carries the casing and punctuation smart_format added; `word` is
        # the bare token. The transcript should read as written.
        "text": str(w.get("punctuated_word") or w.get("word") or ""),
        "start": _float(w.get("start")), "end": _float(w.get("end")),
    } for w in items or [] if isinstance(w, dict)]


def _segment(utterance: dict) -> dict:
    speaker = utterance.get("speaker")
    return {"text": str(utterance.get("transcript") or "").strip(),
            "start": _float(utterance.get("start")), "end": _float(utterance.get("end")),
            "speaker": int(speaker) if isinstance(speaker, (int, float)) else None,
            "words": _words(utterance.get("words"))}


def _segments_from_channels(results: dict) -> list[dict]:
    segments: list[dict] = []
    for channel in results.get("channels") or []:
        for alternative in (channel or {}).get("alternatives") or []:
            text = str(alternative.get("transcript") or "").strip()
            if not text:
                continue
            words = _words(alternative.get("words"))
            segments.append({"text": text, "start": words[0]["start"] if words else 0.0,
                             "end": words[-1]["end"] if words else 0.0, "speaker": None,
                             "words": words})
            break  # the first alternative is the best one
    return segments


def _language(results: dict) -> str:
    for channel in results.get("channels") or []:
        language = (channel or {}).get("detected_language")
        if language:
            return str(language)
    return ""


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
