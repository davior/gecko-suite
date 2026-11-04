"""The raw passthrough: Notes' browser-built chat bodies, answered in Notes' own shapes.

Notes' frontend builds a vendor request (a body, with its own system blocks, tools and
prefill), and its backend forwards it to the provider the user picked, reassembling the
stream into one message and recording the spend. This is that, moved here, so that Notes'
frontend does not change when its key and its ledger do. It is not the normalized chat
(`/ai/chat`): nothing here translates a request or a reply between protocols, which is
exactly why Notes' tools, server-side web search and thinking blocks keep working.

**How a provider is found.** Notes sends the vendor and model name from the provider row
the person picked; the importer merged Notes' rows by key, so those row ids are gone. The
caller's visible providers of that vendor are searched (their own first, then the shared
ones) for one with a model of that name, and that provider's key and the model's stored
settings are used. A provider of the vendor with no such model row is used if there is no
better one: the model is only a string to the vendor, and a model added in Notes after the
import should not need a second step in the suite. Its price then comes from the catalogue
by name, or is unknown.

**What the answers are.** The same dicts Notes returned, not an envelope:
- blocking Anthropic: the reassembled message (Notes streams it internally, so a long
  request does not time out waiting for the first byte);
- blocking OpenAI-compatible and Ollama: the vendor's own JSON;
- `/stream`: `event: delta {text}`, then `event: final <that same dict>`, or
  `event: error {code, message}`. Once the stream starts it is HTTP 200, so every failure
  after that is a frame.

**What differs from Notes.** Errors are the suite's `{"detail": {"code", "message"}}` (Notes
sent the vendor's raw text), cache reads and writes are priced at their own rates instead
of as input, and OpenAI's own models are sent `max_completion_tokens` (their reasoning
models reject `max_tokens`).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator, Optional, Union

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app import crypto
from app.auth import UserCtx
from app.gateway import anthropic, openai, upstream
from app.gateway.chat import upstream_http_error
from app.gateway.types import UpstreamError, Usage
from app.models.ai import AIModel, AIProvider
from app.providers.endpoints import (
    OLLAMA_BASE, anthropic_base, anthropic_headers, openai_compat_base, speaks_anthropic)
from app.providers.params import parse_extra_params
from app.services.access import app_for, bad_request
from app.services.registry import visible_providers_query
from app.usage.ledger import record_raw
from app.vocab import KEYLESS_VENDORS, SCOPE_USER, VENDOR_OLLAMA, VENDOR_OPENAI

logger = logging.getLogger(__name__)

# The gap between events, not the whole reply: vendors send pings, so this stays small even
# for a reply that takes minutes. Notes' own figure.
READ_TIMEOUT = 120.0
# Blocking OpenAI-compatible and Ollama calls return nothing until the reply exists.
BLOCKING_TIMEOUT = 600.0

OPENAI_COMPATIBLE = ("openai", "deepseek", "custom")


class _Common(BaseModel):
    # The vendor of the provider row the person picked in Notes ("anthropic", "deepseek"...).
    vendor: str
    model: str
    app: Optional[str] = None
    subject_ref: Optional[str] = None


class AnthropicRequest(_Common):
    max_tokens: int
    messages: list[dict[str, Any]]
    system: Optional[Union[str, list[dict[str, Any]]]] = None
    prefill: Optional[str] = None
    tools: Optional[list[dict[str, Any]]] = None


class OpenAIRequest(_Common):
    max_tokens: int
    messages: list[dict[str, Any]]


class OllamaRequest(_Common):
    vendor: str = VENDOR_OLLAMA
    messages: list[dict[str, Any]]
    max_tokens: Optional[int] = None


class Resolved(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    provider: AIProvider
    model: Optional[AIModel]
    api_key: str
    params: dict[str, Any]


# ─── which provider ──────────────────────────────────────────────────────────


def resolve(session: Session, user: UserCtx, *, protocol: str, vendor: str, model_id: str) -> Resolved:
    """The provider and, if there is one, the model row a request goes through."""
    def fits(provider: AIProvider) -> bool:
        if provider.vendor != vendor:
            return False
        if protocol == "anthropic":
            return speaks_anthropic(provider)
        if protocol == "ollama":
            return provider.vendor == VENDOR_OLLAMA
        return provider.vendor in OPENAI_COMPATIBLE

    provider, model, key = find_by_name(session, user, vendor=vendor, model_id=model_id, fits=fits)
    return _resolved(provider, model, key)


def find_by_name(session: Session, user: UserCtx, *, vendor: str, model_id: str,
                 fits=None) -> tuple[AIProvider, Optional[AIModel], str]:
    """A provider of `vendor` the caller can use, and its row for `model_id` if it has one.

    One rule for every caller that names a model as Notes does (the raw passthrough, and
    `/ai/chat` given a vendor and model): the caller's own providers first, then shared
    ones; among those, one with an active row for the model; failing that, the first with
    a usable key, because the model is only a string to the vendor. 409 when none exists.
    """
    fits = fits or (lambda provider: provider.vendor == vendor)
    rows = session.exec(visible_providers_query(user.id).where(col(AIProvider.enabled).is_(True))
                        .order_by(col(AIProvider.created_at))).all()
    # Their own first: it is what the person chose for themselves.
    rows = sorted((r for r in rows if fits(r)), key=lambda r: 0 if r.scope == SCOPE_USER else 1)
    usable = [(r, crypto.decrypt(r.api_key)) for r in rows]
    usable = [(r, key) for r, key in usable if key or r.vendor in KEYLESS_VENDORS]
    if not usable:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "code": "ai_not_configured",
            "message": f"No {vendor} provider with a key is set up in Gecko Suite. "
                       "Add one on its Providers screen."})

    for provider, key in usable:
        model = session.exec(select(AIModel).where(
            AIModel.provider_id == provider.id, AIModel.model_id == model_id,
            col(AIModel.is_active).is_(True))).first()
        if model is not None:
            return provider, model, key
    provider, key = usable[0]
    return provider, None, key


def _resolved(provider: AIProvider, model: Optional[AIModel], key: str) -> Resolved:
    # Lowest to highest, as the normalized chat layers them: the provider's, the model's.
    # Structural keys are stripped from each, so settings tune a request and never replace it.
    params = {**parse_extra_params(provider.extra_params), **(parse_extra_params(model.params) if model else {})}
    return Resolved(provider=provider, model=model, api_key=key, params=params)


# ─── what is sent ────────────────────────────────────────────────────────────


def _requests_web_search(tools: Optional[list[dict]]) -> bool:
    return bool(tools) and any(str(t.get("type", "")).startswith("web_search") for t in tools)


def anthropic_call(found: Resolved, request: AnthropicRequest) -> tuple[str, dict, dict]:
    messages = list(request.messages)
    if request.prefill:
        messages.append({"role": "assistant", "content": request.prefill})
    body: dict[str, Any] = {"model": request.model, "max_tokens": request.max_tokens, "messages": messages}
    if request.system:
        body["system"] = request.system
    if request.tools:
        body["tools"] = request.tools
    # After the caller's fields, byte for byte as Notes built them, to keep the prompt-cache
    # breakpoints in the system and message blocks where the caller put them.
    body.update(found.params)
    body["stream"] = True
    headers = anthropic_headers(found.api_key, found.provider.vendor,
                                web_search=_requests_web_search(request.tools))
    return f"{anthropic_base(found.provider)}/v1/messages", headers, body


def openai_call(found: Resolved, request: OpenAIRequest, *, stream: bool) -> tuple[str, dict, dict]:
    limit = "max_completion_tokens" if found.provider.vendor == VENDOR_OPENAI else "max_tokens"
    body: dict[str, Any] = {"model": request.model, limit: request.max_tokens, "messages": request.messages}
    body.update(found.params)
    if stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    headers = {"Authorization": f"Bearer {found.api_key}", "content-type": "application/json"}
    return f"{openai_compat_base(found.provider.vendor, found.provider.base_url)}/v1/chat/completions", headers, body


def ollama_call(found: Resolved, request: OllamaRequest, *, stream: bool) -> tuple[str, dict, dict]:
    body: dict[str, Any] = {"model": request.model, "messages": request.messages, "stream": stream}
    # Ollama nests its parameters under `options`, and num_predict is its output cap.
    options: dict[str, Any] = dict(found.params)
    if request.max_tokens is not None:
        options["num_predict"] = request.max_tokens
    if options:
        body["options"] = options
    base = (found.provider.base_url or OLLAMA_BASE).rstrip("/")
    return f"{base}/api/chat", {"content-type": "application/json"}, body


# ─── the events, in Notes' shapes ────────────────────────────────────────────


def anthropic_events(found: Resolved, request: AnthropicRequest) -> Iterator[tuple[str, Any]]:
    url, headers, body = anthropic_call(found, request)
    for kind, value in anthropic.message_events(url, headers, body, label=found.provider.name,
                                                read_timeout=READ_TIMEOUT):
        if kind == "message":
            for block in value.get("content", []):
                # The suite marks a tool input that did not parse; Notes never had that key.
                block.pop("_parse_error", None)
        yield kind, value


def openai_events(found: Resolved, request: OpenAIRequest) -> Iterator[tuple[str, Any]]:
    url, headers, body = openai_call(found, request, stream=True)
    for kind, value in openai.completion_events(url, headers, body, label=found.provider.name,
                                                read_timeout=READ_TIMEOUT):
        if kind == "payload":
            for choice in value.get("choices", []):
                # Notes' final always carried a string, empty when the model only called tools.
                choice["message"]["content"] = choice["message"].get("content") or ""
            value["usage"] = value.get("usage") or {}
            yield "final", value
        else:
            yield kind, value


def ollama_events(found: Resolved, request: OllamaRequest) -> Iterator[tuple[str, Any]]:
    """Ollama sends newline-delimited JSON, not SSE."""
    url, headers, body = ollama_call(found, request, stream=True)
    full, done_reason, prompt_eval, eval_count = "", None, 0, 0
    with upstream.stream_lines(url, headers=headers, body=body, read_timeout=READ_TIMEOUT,
                               label=found.provider.name) as lines:
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            piece = (event.get("message") or {}).get("content")
            if piece:
                full += piece
                yield "delta", piece
            if event.get("done"):
                done_reason = event.get("done_reason")
                prompt_eval = upstream.as_int(event, "prompt_eval_count")
                eval_count = upstream.as_int(event, "eval_count")
    yield "final", {"message": {"role": "assistant", "content": full}, "done_reason": done_reason,
                    "prompt_eval_count": prompt_eval, "eval_count": eval_count}


# ─── usage ───────────────────────────────────────────────────────────────────


def usage_of(protocol: str, data: dict) -> Usage:
    if protocol == "anthropic":
        return anthropic.parse_usage(data)
    if protocol == "ollama":
        return Usage(input_tokens=upstream.as_int(data, "prompt_eval_count"),
                     output_tokens=upstream.as_int(data, "eval_count"))
    usage = openai.parse_usage(data)
    if not (usage.input_tokens or usage.output_tokens):
        # Some compatible providers report only a total; it is costed as input.
        usage = Usage(input_tokens=upstream.as_int(data.get("usage"), "total_tokens"))
    return usage


def _record(session: Session, user: UserCtx, request: _Common, found: Resolved, protocol: str,
            data: dict, *, status_: str = "ok") -> None:
    usage = usage_of(protocol, data)
    if not (usage.input_tokens or usage.output_tokens or usage.cache_read_tokens or usage.cache_write_tokens):
        return
    try:
        record_raw(session, user_id=user.id, app=app_for(user, request.app), provider=found.provider,
                   model=found.model, model_id=request.model, usage=usage,
                   subject_ref=request.subject_ref, status=status_)
    except Exception:  # noqa: BLE001 - accounting must not undo the reply it measures
        session.rollback()
        logger.warning("Could not record the usage of a raw %s call", protocol, exc_info=True)


def _record_failure(session: Session, user: UserCtx, request: _Common, found: Resolved,
                    exc: UpstreamError) -> None:
    """A reply that was billed and then could not be used is still spend."""
    if exc.usage is None:
        return
    try:
        record_raw(session, user_id=user.id, app=app_for(user, request.app), provider=found.provider,
                   model=found.model, model_id=request.model, usage=exc.usage,
                   subject_ref=request.subject_ref, status="error")
    except Exception:  # noqa: BLE001
        session.rollback()


# ─── blocking ────────────────────────────────────────────────────────────────


def _check_app(user: UserCtx, request: _Common) -> None:
    app_for(user, request.app)  # an app credential fixes the app; it cannot claim another


def complete(session: Session, user: UserCtx, protocol: str, request: Any) -> dict:
    """One call, answered with the same dict Notes' blocking proxy returned."""
    _check_app(user, request)
    if not request.model.strip():
        raise bad_request("bad_model", "Say which model")
    found = resolve(session, user, protocol=protocol, vendor=request.vendor, model_id=request.model)
    try:
        if protocol == "anthropic":
            # Streamed internally, so the read timeout bounds the gap between events and a
            # long request with web search does not die waiting for its first byte.
            data = {}
            for kind, value in anthropic_events(found, request):
                if kind == "message":
                    data = value
        else:
            url, headers, body = (openai_call(found, request, stream=False) if protocol == "openai"
                                  else ollama_call(found, request, stream=False))
            response = upstream.post_json(url, headers=headers, body=body, timeout=BLOCKING_TIMEOUT,
                                          label=found.provider.name)
            if response.status_code >= 400:
                raise upstream.http_error(response, found.provider.name)
            try:
                data = response.json()
            except ValueError as exc:
                raise UpstreamError(f"{found.provider.name} returned a response that was not JSON") from exc
    except UpstreamError as exc:
        _record_failure(session, user, request, found, exc)
        raise upstream_http_error(exc) from exc
    _record(session, user, request, found, protocol, data)
    return data


# ─── streaming ───────────────────────────────────────────────────────────────


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def stream(session: Session, user: UserCtx, protocol: str, request: Any) -> Iterator[str]:
    """Notes' SSE: `delta` {text}, then `final` (the blocking dict), or `error` {code, message}.

    The provider is resolved before the first frame, so a missing one is an HTTP error. After
    that every failure is a frame. As in `chat.run_stream`, the request's session is used at
    the end to record usage: everything else was copied out before the first byte.
    """
    _check_app(user, request)
    if not request.model.strip():
        raise bad_request("bad_model", "Say which model")
    found = resolve(session, user, protocol=protocol, vendor=request.vendor, model_id=request.model)
    events = {"anthropic": anthropic_events, "openai": openai_events, "ollama": ollama_events}[protocol]

    def frames() -> Iterator[str]:
        try:
            for kind, value in events(found, request):
                if kind == "delta":
                    yield _sse("delta", {"text": value})
                else:  # "message" (Anthropic) or "final"
                    _record(session, user, request, found, protocol, value)
                    yield _sse("final", value)
        except UpstreamError as exc:
            _record_failure(session, user, request, found, exc)
            yield _sse("error", {"code": exc.code, "message": exc.message})
        except Exception:  # noqa: BLE001 - headers are sent; raising now only cuts the connection
            logger.exception("Raw %s stream failed", protocol)
            yield _sse("error", {"code": "stream_failed", "message": "The stream failed"})

    return frames()
