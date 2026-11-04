"""One chat call through the gateway: route it, check it, send it, count it."""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator, Optional

from fastapi import HTTPException, status
from sqlmodel import Session

from app import crypto
from app.auth import UserCtx
from app.gateway import anthropic, openai
from app.gateway.types import ChatRequest, ChatResult, Reply, Target, UpstreamError
from app.models.ai import AIModel, AIProvider
from app.providers.endpoints import speaks_anthropic
from app.providers.params import parse_extra_params
from app.models.library import Persona
from app.routers.library import visible_row
from app.services.access import app_for, bad_request, not_found
from app.services.registry import capabilities_of, catalog_entry_for, load_json, resolve, usable, visible_model
from app.usage.ledger import record_chat
from app.vocab import KEYLESS_VENDORS, LLM_VENDORS, TASK_REQUIRES, TASKS

logger = logging.getLogger(__name__)

# What a request gets when neither it nor the model says. The Claude API reference's
# advice: generous, because hitting the cap truncates mid-thought and costs a retry, and
# higher for a stream, which has no HTTP timeout to stay under.
DEFAULT_MAX_TOKENS = 16_000
DEFAULT_STREAM_MAX_TOKENS = 64_000


def _not_configured(task: str) -> HTTPException:
    """Nothing is routed for this task. 409, with a sentence the calling app can show:
    its remedy is a setting in the suite, not a retry."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "ai_not_configured",
            "message": f"No AI model is set up for {task}. Choose one in Gecko Suite's AI settings.",
        },
    )


def prepare(session: Session, user: UserCtx, request: ChatRequest) -> tuple[Target, Optional[str], int, dict[str, Any]]:
    """Resolve and validate a request, before anything is sent.

    Returns the target, the task it ran as, the max_tokens to ask for, and the merged
    request parameters. Everything that can be refused is refused here, as an HTTP
    error, so a streamed response never has to report a configuration mistake mid-way.
    """
    # An app credential fixes the app; a request made with one cannot claim another.
    request.app = app_for(user, request.app)
    if request.task:
        if request.task not in TASKS:
            raise bad_request("bad_task", f"Unknown task: {request.task}")
        if "chat" not in TASK_REQUIRES[request.task]:
            raise bad_request("bad_task", f"{request.task} is not a chat task")

    persona = _persona(session, user, request)
    if persona is not None and persona.system_prompt:
        # First, as the most stable block, so it stays inside the cached prefix.
        request.system = [persona.system_prompt, *request.system]
    persona_model = _persona_model(session, user, persona, request.task)

    assignment_params: dict[str, Any] = {}
    if persona_model is not None:
        model, provider = persona_model
    elif request.task:
        hit = resolve(session, user.id, request.task, request.app)
        if hit is None:
            raise _not_configured(request.task)
        model, provider = hit.model, hit.provider
        assignment_params = load_json(hit.assignment.params, {})
    elif request.model_ref:
        model = visible_model(session, request.model_ref, user.id)
        if model is None:
            raise not_found("model")
        provider = session.get(AIProvider, model.provider_id)
        if not usable(model, provider):
            raise bad_request("model_disabled", f"{model.label or model.model_id} is switched off")
    else:
        model, provider = _by_name(session, user, request.vendor, request.model)

    _check_capabilities(model, provider, request)

    api_key = crypto.decrypt(provider.api_key)
    if not api_key and provider.vendor not in KEYLESS_VENDORS:
        # A key that no longer decrypts (an encryption key removed too early) reads as
        # unset, the same way the settings screen shows it.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "provider_key_missing",
                    "message": f"{provider.name} has no usable API key. Set it in Gecko Suite."},
        )

    # Merged lowest to highest: the provider's, the model's, the task's, the request's.
    # Structural keys (model, messages, tools, stream...) are stripped from each, so
    # parameters can tune a request but never replace one.
    params: dict[str, Any] = {}
    for layer in (
        parse_extra_params(provider.extra_params),
        parse_extra_params(model.params),
        parse_extra_params(json.dumps(assignment_params)),
        parse_extra_params(json.dumps(request.params)),
    ):
        params.update(layer)

    target = Target(
        provider_id=provider.id,
        provider_name=provider.name,
        vendor=provider.vendor,
        base_url=provider.base_url,
        use_anthropic_api=provider.use_anthropic_api,
        api_key=api_key,
        model_ref=model.id,
        model_id=model.model_id,
        capabilities=capabilities_of(model),
        max_tokens=model.max_tokens,
    )
    default = DEFAULT_STREAM_MAX_TOKENS if request.stream else DEFAULT_MAX_TOKENS
    asked = request.max_tokens or default
    max_tokens = min(asked, model.max_tokens) if model.max_tokens else asked
    return target, request.task, max_tokens, params


def _by_name(session: Session, user: UserCtx, vendor: str, model_id: str) -> tuple[AIModel, AIProvider]:
    """A model named by vendor and name, as Notes' own model picker names it.

    The provider is found as the raw passthrough finds it. A model with no row of its own
    (one added in Notes after the import) gets a stand-in row, never saved, whose
    capabilities are the catalogue's for that name, or only chat and tools when the
    catalogue does not know it: a model is never assumed to see images it was not
    declared to see, and the capability check below refuses that in words.
    """
    from app.gateway.raw import find_by_name

    provider, model, _key = find_by_name(session, user, vendor=vendor, model_id=model_id)
    if model is not None:
        return model, provider
    entry = catalog_entry_for(session, vendor, model_id)
    capabilities = load_json(entry.capabilities, None) if entry else None
    stand_in = AIModel(
        id="", provider_id=provider.id, model_id=model_id, label=model_id,
        capabilities=json.dumps(sorted(capabilities or ["chat", "tools"])),
        max_tokens=entry.max_tokens if entry else None,
    )
    return stand_in, provider


def _persona(session: Session, user: UserCtx, request: ChatRequest) -> Optional[Persona]:
    if not request.persona_id:
        return None
    persona = visible_row(session, Persona, request.persona_id, user.id)
    if persona is None:
        raise not_found("persona")
    return persona


def _persona_model(session: Session, user: UserCtx, persona: Optional[Persona],
                   task: Optional[str]) -> Optional[tuple[AIModel, AIProvider]]:
    """The persona's own model for this task, if it has one the caller can use.

    Checked against the caller, not whoever saved the persona, and skipped (falling back
    to routing) if switched off, the same way a switched-off assignment is.
    """
    if persona is None or not task:
        return None
    model_ref = load_json(persona.model_overrides, {}).get(task)
    model = visible_model(session, model_ref, user.id) if model_ref else None
    if model is None:
        return None
    provider = session.get(AIProvider, model.provider_id)
    return (model, provider) if usable(model, provider) else None


def _check_capabilities(model: AIModel, provider: AIProvider, request: ChatRequest) -> None:
    """Refuse what the model cannot do, here, in words, rather than as a vendor 400.

    Images to a text-only model are the case that matters most: DeepSeek answers them
    with a deserialisation error that says nothing about images.
    """
    caps = capabilities_of(model)
    name = model.label or model.model_id
    if provider.vendor not in LLM_VENDORS or "chat" not in caps:
        raise bad_request("missing_capability", f"{name} is not a chat model")
    if request.has_images() and "vision" not in caps:
        raise bad_request("missing_capability", f"{name} cannot see images")
    if request.tools and "tools" not in caps:
        raise bad_request("missing_capability", f"{name} cannot call tools")
    if request.builtin_tools:
        if "web_search" not in caps or not speaks_anthropic(provider):
            raise bad_request("missing_capability", f"{name} has no built-in web search")


def _adapter(target: Target):
    return anthropic if speaks_anthropic(_ProviderView(target)) else openai


class _ProviderView:
    def __init__(self, target: Target):
        self.vendor = target.vendor
        self.use_anthropic_api = target.use_anthropic_api
        self.base_url = target.base_url


def _result(reply: Reply, target: Target, task: Optional[str], event) -> ChatResult:
    return ChatResult(
        text=reply.text,
        tool_calls=reply.tool_calls,
        stop=reply.stop,
        refusal_category=reply.refusal_category,
        message={"role": "assistant", "text": reply.text, "tool_calls": reply.tool_calls, "raw": reply.raw},
        usage={
            "input_tokens": reply.usage.input_tokens,
            "output_tokens": reply.usage.output_tokens,
            "cache_read_tokens": reply.usage.cache_read_tokens,
            "cache_write_tokens": reply.usage.cache_write_tokens,
        },
        model=reply.served_model or target.model_id,
        model_ref=target.model_ref,
        provider=target.provider_name,
        vendor=target.vendor,
        task=task,
        cost=event.cost,
        currency=event.currency,
        cost_estimated=event.cost_estimated,
        usage_event_id=event.id,
    )


def _record_failure(session: Session, user: UserCtx, request: ChatRequest, task, target, exc: UpstreamError) -> None:
    """A reply that was billed and then could not be used is still spend."""
    if exc.usage is None:
        return
    record_chat(session, user_id=user.id, app=request.app, task=task, target=target, usage=exc.usage,
                served_model=exc.served_model, subject_ref=request.subject_ref, status="error")


def upstream_http_error(exc: UpstreamError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


def run(session: Session, user: UserCtx, request: ChatRequest) -> ChatResult:
    target, task, max_tokens, params = prepare(session, user, request)
    try:
        reply = _adapter(target).call(target, request, max_tokens=max_tokens, params=params)
    except UpstreamError as exc:
        _record_failure(session, user, request, task, target, exc)
        raise upstream_http_error(exc) from exc
    event = record_chat(session, user_id=user.id, app=request.app, task=task, target=target,
                        usage=reply.usage, served_model=reply.served_model, subject_ref=request.subject_ref)
    return _result(reply, target, task, event)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def run_stream(session: Session, user: UserCtx, request: ChatRequest) -> Iterator[str]:
    """Server-Sent Events: `delta` {text} as text arrives, a `tool_call` per call, then
    `final` with the same body the blocking call returns, or `error` {code, message}.

    `prepare` runs before the first frame (the router calls it), so configuration
    mistakes are HTTP errors. Only failures after the stream has started are frames.

    The session is the request's. FastAPI may close it before the stream ends, and a
    closed SQLAlchemy Session reopens a connection on its next use, which is what
    recording usage at the end relies on. Everything else it needs was copied into the
    Target before the first byte.
    """
    target, task, max_tokens, params = prepare(session, user, request)

    def frames() -> Iterator[str]:
        try:
            for kind, value in _adapter(target).stream(target, request, max_tokens=max_tokens, params=params):
                if kind == "delta":
                    yield _sse("delta", {"text": value})
                elif kind == "final":
                    event = record_chat(session, user_id=user.id, app=request.app, task=task, target=target,
                                        usage=value.usage, served_model=value.served_model,
                                        subject_ref=request.subject_ref)
                    result = _result(value, target, task, event)
                    for call in result.tool_calls:
                        yield _sse("tool_call", call.model_dump())
                    yield _sse("final", result.model_dump())
        except UpstreamError as exc:
            _record_failure(session, user, request, task, target, exc)
            yield _sse("error", {"code": exc.code, "message": exc.message})
        except Exception:
            # Headers are sent; an exception now would just cut the connection, which a
            # caller cannot tell apart from a network drop.
            logger.exception("Chat stream failed")
            yield _sse("error", {"code": "stream_failed", "message": "The stream failed"})

    return frames()
