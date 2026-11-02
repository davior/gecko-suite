"""Text to vectors, through whichever embedding model the task routes to.

Ported from GAM's `embeddings/` (OpenAI-compatible `/v1/embeddings` and Ollama's
`/api/embed`). The vectors go back to the caller and are not kept: GAM owns its index,
and stores the model id beside every vector, because vectors from two models are not
comparable and a routing change must never silently mix them.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from app import crypto
from app.auth import UserCtx
from app.gateway import upstream
from app.gateway.chat import upstream_http_error
from app.gateway.types import UpstreamError
from app.models.ai import AIProvider
from app.models.usage import UsageEvent
from app.providers.endpoints import OLLAMA_BASE, openai_compat_base
from app.services.access import app_for, bad_request, not_found
from app.services.registry import capabilities_of, effective_pricing, resolve, usable, visible_model
from app.vocab import KEYLESS_VENDORS, VENDOR_ANTHROPIC, VENDOR_OLLAMA

# Each request carries a whole batch: well under the vendors' input caps, and small enough
# that one failure costs little work. A backfill of thousands is many calls to this.
MAX_BATCH = 128
MAX_TEXTS = 2048
REQUEST_TIMEOUT = 180.0


class EmbedRequest(BaseModel):
    task: str = "embed.text"
    model_ref: Optional[str] = None
    app: Optional[str] = None
    subject_ref: Optional[str] = Field(default=None, max_length=300)
    texts: list[str] = Field(min_length=1, max_length=MAX_TEXTS)
    # Fewer dimensions than the model's native size, for a model trained to be truncated
    # (OpenAI's text-embedding-3 family). GAM asks for 512: a third of the memory for
    # almost all of the retrieval quality.
    dimensions: Optional[int] = Field(default=None, ge=1, le=8192)


class EmbedResult(BaseModel):
    model_config = {"protected_namespaces": ()}

    vectors: list[list[float]]
    model: str
    model_ref: str
    dimensions: int
    input_tokens: int
    cost: Optional[float] = None
    currency: Optional[str] = None
    usage_event_id: str


def run(session: Session, user: UserCtx, request: EmbedRequest) -> EmbedResult:
    app = app_for(user, request.app)
    if request.model_ref:
        model = visible_model(session, request.model_ref, user.id)
        if model is None:
            raise not_found("model")
        provider = session.get(AIProvider, model.provider_id)
        if not usable(model, provider):
            raise bad_request("model_disabled", f"{model.label or model.model_id} is switched off")
    else:
        if request.task != "embed.text":
            raise bad_request("bad_task", f"{request.task} is not an embedding task")
        hit = resolve(session, user.id, request.task, app)
        if hit is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
                "code": "ai_not_configured",
                "message": "No embedding model is set up. Choose one in Gecko Suite's AI settings.",
            })
        model, provider = hit.model, hit.provider

    if "embed" not in capabilities_of(model) or provider.vendor == VENDOR_ANTHROPIC:
        raise bad_request("missing_capability", f"{model.label or model.model_id} is not an embedding model")
    api_key = crypto.decrypt(provider.api_key)
    if not api_key and provider.vendor not in KEYLESS_VENDORS:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "code": "provider_key_missing", "message": f"{provider.name} has no usable API key."})

    vectors: list[list[float]] = []
    tokens = 0
    try:
        for start in range(0, len(request.texts), MAX_BATCH):
            batch = request.texts[start:start + MAX_BATCH]
            got, used = _embed_batch(provider, api_key, model.model_id, batch, request.dimensions)
            vectors.extend(got)
            tokens += used
    except UpstreamError as exc:
        _record(session, user, app, request, provider, model, tokens, status_="error")
        raise upstream_http_error(exc) from exc

    event = _record(session, user, app, request, provider, model, tokens)
    return EmbedResult(vectors=vectors, model=model.model_id, model_ref=model.id,
                       dimensions=len(vectors[0]) if vectors else 0, input_tokens=tokens,
                       cost=event.cost, currency=event.currency, usage_event_id=event.id)


def _embed_batch(provider: AIProvider, api_key: str, model_id: str, batch: list[str],
                 dimensions: Optional[int]) -> tuple[list[list[float]], int]:
    if provider.vendor == VENDOR_OLLAMA:
        # /api/embed, not the older /api/embeddings: the newer one batches.
        url = f"{(provider.base_url or OLLAMA_BASE).rstrip('/')}/api/embed"
        body: dict = {"model": model_id, "input": batch}
        headers = {"content-type": "application/json"}
    else:
        url = f"{openai_compat_base(provider.vendor, provider.base_url)}/v1/embeddings"
        body = {"model": model_id, "input": batch}
        if dimensions:
            body["dimensions"] = dimensions
        headers = {"content-type": "application/json", "Authorization": f"Bearer {api_key}"}

    response = upstream.post_json(url, headers=headers, body=body, timeout=REQUEST_TIMEOUT, label=provider.name)
    if response.status_code >= 400:
        raise upstream.http_error(response, provider.name)
    try:
        payload = response.json()
    except ValueError as exc:
        raise UpstreamError(f"{provider.name} returned a response that was not JSON") from exc

    if provider.vendor == VENDOR_OLLAMA:
        vectors = payload.get("embeddings") if isinstance(payload, dict) else None
        used = upstream.as_int(payload, "prompt_eval_count")
    else:
        data = payload.get("data") if isinstance(payload, dict) else None
        # The API documents `index` and may return items out of order; sorting by it is
        # what makes the ordering guarantee true.
        vectors = [item.get("embedding") for item in sorted(data, key=lambda i: i.get("index", 0))] \
            if isinstance(data, list) else None
        used = upstream.as_int(payload.get("usage") if isinstance(payload, dict) else None, "prompt_tokens")
    if not isinstance(vectors, list) or len(vectors) != len(batch) or not all(isinstance(v, list) for v in vectors):
        raise UpstreamError(f"{provider.name} returned a different number of vectors than texts")
    return [[float(x) for x in v] for v in vectors], used


def _record(session, user, app, request, provider, model, tokens, status_="ok") -> UsageEvent:
    pricing = effective_pricing(session, model, provider)
    # Embeddings are priced on input alone. A catalogue row without an input price is an
    # unknown cost, recorded as None.
    cost = round(tokens * float(pricing["input"]) / 1_000_000, 6) if pricing and "input" in pricing else None
    event = UsageEvent(
        user_id=user.id, app=app, task="embed.text", kind="embed", provider_id=provider.id,
        provider_name=provider.name, vendor=provider.vendor, model=model.model_id, units=tokens,
        unit_type="tokens", input_tokens=tokens, cost=cost,
        currency=(pricing or {}).get("currency", "USD") if cost is not None else None,
        status=status_, subject_ref=request.subject_ref,
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event
