"""Providers: a credential and an endpoint, shared by an admin or owned by one user.

Carried over from GVC's router (from GAM's): the key goes out only as
`api_key_configured`, base URLs are SSRF-checked for the vendors that fetch them, and
`POST /test` checks a key before anything depends on it. New here: scope. Anyone sees
the instance providers and their own; only an admin writes the instance ones.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import Session, func, select

from app import crypto, outbound
from app.config import settings
from app.auth import CurrentUser
from app.clock import utcnow
from app.database import get_session
from app.models.ai import AIModel, AIProvider
from app.providers.endpoints import (
    OLLAMA_BASE,
    anthropic_base,
    anthropic_headers,
    openai_compat_base,
    speaks_anthropic,
)
from app.safe_url import require_safe_external_url
from app.schemas import DataResponse, ListResponse
from app.schemas_ai import (
    ProviderCreate,
    ProviderRead,
    ProviderTest,
    ProviderTestResult,
    ProviderUpdate,
)
from app.services import access
from app.services.registry import load_json, visible_provider, visible_providers_query
from app.vocab import (
    ANTHROPIC_CAPABLE_VENDORS,
    CHECKED_BASE_URL_VENDORS,
    KEYLESS_VENDORS,
    LLM_VENDORS,
    VENDOR_DEEPGRAM,
    VENDOR_FAL,
    VENDOR_OLLAMA,
    VENDORS,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Short on purpose: a reachability check a person is watching a spinner for. A provider
# that cannot answer a ten-token question in ten seconds has failed the question.
PROBE_TIMEOUT = httpx.Timeout(10.0)
PROBE_BODY = {"max_tokens": 10, "messages": [{"role": "user", "content": "Hi"}]}


def _serialise(session: Session, provider: AIProvider, writable: bool) -> ProviderRead:
    count = session.exec(
        select(func.count()).select_from(AIModel).where(AIModel.provider_id == provider.id)
    ).one()
    return ProviderRead(
        id=provider.id,
        scope=provider.scope,
        name=provider.name,
        vendor=provider.vendor,
        base_url=provider.base_url,
        use_anthropic_api=provider.use_anthropic_api,
        extra_params=load_json(provider.extra_params, {}) or None,
        max_tokens=provider.max_tokens,
        enabled=provider.enabled,
        api_key_configured=bool(provider.api_key),
        writable=writable,
        model_count=count,
    )


def _require_vendor(vendor: Optional[str]) -> str:
    if vendor not in VENDORS:
        raise access.bad_request("bad_vendor", f"Unknown vendor: {vendor}")
    return vendor


def _check_base_url(vendor: str, base_url: Optional[str]) -> None:
    """SSRF-check a base URL, for the vendors whose base URL is actually fetched."""
    if base_url and vendor in CHECKED_BASE_URL_VENDORS:
        require_safe_external_url(base_url)


def _check_anthropic_flag(vendor: str, use_anthropic_api: bool) -> None:
    # Refused rather than silently ignored: a setting that is stored and then has no
    # effect is a setting someone will spend an afternoon debugging.
    if use_anthropic_api and vendor not in ANTHROPIC_CAPABLE_VENDORS:
        raise access.bad_request(
            "bad_request",
            "Only DeepSeek and custom providers can be switched to the Anthropic protocol",
        )


def _writable(session: Session, provider_id: str, user) -> AIProvider:
    """The provider, if the caller may change it; 404 if they cannot see it, 403 if
    they can see it but it is shared and they are not an admin."""
    provider = visible_provider(session, provider_id, user.id)
    if provider is None:
        raise access.not_found("provider")
    admin = access.caller_is_admin(session, user)
    if not access.can_write(provider.scope, provider.user_id, user, admin):
        raise access.admin_required()
    return provider


@router.get("", response_model=ListResponse[ProviderRead])
def list_providers(
    user: CurrentUser, scope: Optional[str] = None, session: Session = Depends(get_session)
) -> ListResponse[ProviderRead]:
    query = visible_providers_query(user.id)
    if scope:
        query = query.where(AIProvider.scope == scope)
    providers = session.exec(query.order_by(AIProvider.scope, AIProvider.created_at)).all()
    admin = access.caller_is_admin(session, user)
    data = [
        _serialise(session, p, access.can_write(p.scope, p.user_id, user, admin))
        for p in providers
    ]
    return ListResponse(data=data, total=len(data), limit=len(data), offset=0)


@router.post("", response_model=DataResponse[ProviderRead], status_code=201)
def create_provider(
    payload: ProviderCreate, user: CurrentUser, session: Session = Depends(get_session)
) -> DataResponse[ProviderRead]:
    admin = access.caller_is_admin(session, user)
    access.require_scope(payload.scope, admin)
    vendor = _require_vendor(payload.vendor)
    _check_base_url(vendor, payload.base_url)
    _check_anthropic_flag(vendor, payload.use_anthropic_api)

    provider = AIProvider(
        scope=payload.scope,
        user_id=access.owner_for(payload.scope, user),
        name=payload.name,
        vendor=vendor,
        api_key=crypto.encrypt(payload.api_key),
        base_url=payload.base_url or None,
        use_anthropic_api=payload.use_anthropic_api,
        extra_params=json.dumps(payload.extra_params, sort_keys=True) if payload.extra_params else None,
        max_tokens=payload.max_tokens,
        enabled=payload.enabled,
    )
    session.add(provider)
    session.commit()
    session.refresh(provider)
    return DataResponse(data=_serialise(session, provider, True))


@router.patch("/{provider_id}", response_model=DataResponse[ProviderRead])
def update_provider(
    provider_id: str,
    payload: ProviderUpdate,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[ProviderRead]:
    provider = _writable(session, provider_id, user)

    if payload.base_url is not None:
        _check_base_url(provider.vendor, payload.base_url)
        provider.base_url = payload.base_url or None
    if payload.use_anthropic_api is not None:
        _check_anthropic_flag(provider.vendor, payload.use_anthropic_api)
        provider.use_anthropic_api = payload.use_anthropic_api
    if payload.name is not None:
        provider.name = payload.name
    if payload.enabled is not None:
        provider.enabled = payload.enabled
    if payload.extra_params is not None:
        provider.extra_params = (
            json.dumps(payload.extra_params, sort_keys=True) if payload.extra_params else None
        )
    if "max_tokens" in payload.model_fields_set:
        provider.max_tokens = payload.max_tokens
    if payload.api_key is not None:
        provider.api_key = crypto.encrypt(payload.api_key)

    provider.updated_at = utcnow()
    session.add(provider)
    session.commit()
    session.refresh(provider)
    return DataResponse(data=_serialise(session, provider, True))


# response_model=None alongside `-> None`: FastAPI would otherwise infer a response
# model from NoneType and refuse a 204 at import time.
@router.delete(
    "/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
)
def delete_provider(
    provider_id: str, user: CurrentUser, session: Session = Depends(get_session)
) -> None:
    """Delete a provider, its models, and any routing that pointed at them.

    The database cascades (app.models.ai). Tasks routed to one of its models fall back
    to the next rule (app.services.registry.resolve) rather than breaking.
    """
    provider = _writable(session, provider_id, user)
    session.delete(provider)
    session.commit()


# ─── connection test ─────────────────────────────────────────────────────────


def _probe_result(status_code: int) -> ProviderTestResult:
    """Read an HTTP status as an answer about reachability, not about the probe body.

    400 counts as success: the endpoint answered, so the URL and the key were accepted;
    it is the ten-token request it disliked, which is not the question. 401/403/404 are
    the ones that mean the configuration is wrong.
    """
    if status_code in (200, 400):
        return ProviderTestResult(success=True, message="Connected")
    if status_code in (401, 403):
        return ProviderTestResult(success=False, message="The API key was rejected")
    if status_code == 404:
        return ProviderTestResult(success=False, message="No such model, or the wrong address")
    if status_code == 429:
        return ProviderTestResult(success=False, message="Rate limited or out of quota")
    return ProviderTestResult(success=False, message=f"The provider returned HTTP {status_code}")


# The vendors that are not chat models have no ten-token question to ask. Each is asked
# something cheap and read-only that only a valid key may see, so a test spends nothing:
# Deepgram its projects, fal.ai one model's price (what the suite asks before a job anyway).
_KEY_PROBES = {
    VENDOR_DEEPGRAM: lambda key: ("GET", f"{settings.deepgram_base_url.rstrip('/')}/v1/projects",
                                  {"Authorization": f"Token {key}"}, None),
    VENDOR_FAL: lambda key: ("GET", f"{settings.fal_api_base_url.rstrip('/')}/v1/models/pricing",
                             {"Authorization": f"Key {key}"}, {"endpoint_id": "fal-ai/flux/dev"}),
}


def _probe_key_only(vendor: str, api_key: str) -> ProviderTestResult:
    probe = _KEY_PROBES.get(vendor)
    if probe is None:
        return ProviderTestResult(success=False, message="This vendor cannot be tested")
    method, url, headers, params = probe(api_key)
    try:
        with outbound.client(timeout=PROBE_TIMEOUT) as http:
            response = http.request(method, url, headers=headers, params=params)
    except httpx.TimeoutException:
        return ProviderTestResult(success=False, message="The provider did not respond in time")
    except httpx.HTTPError as exc:
        logger.warning("Provider probe failed: %s", type(exc).__name__)
        return ProviderTestResult(success=False, message="Could not reach that address")
    if response.status_code == 200:
        return ProviderTestResult(success=True, message="Connected")
    # 400 is not "connected" here as it is for a chat probe: these requests are fixed, so
    # a 400 means the vendor's API changed, which is worth saying rather than hiding.
    return _probe_result(response.status_code) if response.status_code != 400 else ProviderTestResult(
        success=False, message="The provider answered, but not as expected (HTTP 400)")


@router.post("/test", response_model=DataResponse[ProviderTestResult])
def test_provider(
    payload: ProviderTest, user: CurrentUser, session: Session = Depends(get_session)
) -> DataResponse[ProviderTestResult]:
    """Ask the provider a ten-token question and report whether it answered.

    Testing a saved provider needs only to be able to *see* it: the key is used, never
    returned, so any user can check that the shared Claude key still works.
    """
    probe = AIProvider(
        scope="user",
        name="probe",
        vendor=payload.vendor or "",
        base_url=payload.base_url,
        use_anthropic_api=payload.use_anthropic_api,
    )
    api_key = payload.api_key

    if payload.provider_id:
        saved = visible_provider(session, payload.provider_id, user.id)
        if saved is None:
            raise access.not_found("provider")
        probe.vendor = saved.vendor
        probe.base_url = payload.base_url or saved.base_url
        probe.use_anthropic_api = saved.use_anthropic_api
        api_key = api_key or crypto.decrypt(saved.api_key)

    vendor = _require_vendor(probe.vendor)
    if not api_key and vendor not in KEYLESS_VENDORS:
        return DataResponse(data=ProviderTestResult(success=False, message="No API key is set"))
    if vendor not in LLM_VENDORS:
        return DataResponse(data=_probe_key_only(vendor, api_key))

    model_id = payload.model_id
    if not model_id and vendor != VENDOR_OLLAMA:
        raise access.bad_request("bad_request", "Name a model to test the provider with")

    try:
        with outbound.client(timeout=PROBE_TIMEOUT) as http:
            if speaks_anthropic(probe):
                # Checked whatever the vendor: gecko-notes checked only the OpenAI path,
                # which left a custom provider's URL going straight into a POST.
                _check_base_url(vendor, probe.base_url)
                response = http.post(
                    f"{anthropic_base(probe)}/v1/messages",
                    headers=anthropic_headers(api_key, vendor),
                    json={"model": model_id, **PROBE_BODY},
                )
            elif vendor == VENDOR_OLLAMA:
                # The one keyless provider, so "is it running" is the whole question.
                target = (probe.base_url or OLLAMA_BASE).rstrip("/")
                response = http.get(f"{target}/api/tags")
                ok = response.status_code == 200
                return DataResponse(
                    data=ProviderTestResult(
                        success=ok,
                        message="Ollama is reachable"
                        if ok
                        else f"Ollama returned HTTP {response.status_code}",
                    )
                )
            else:
                _check_base_url(vendor, probe.base_url)
                response = http.post(
                    f"{openai_compat_base(vendor, probe.base_url)}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
                    json={"model": model_id, **PROBE_BODY},
                )
    except HTTPException:
        raise
    except httpx.TimeoutException:
        return DataResponse(
            data=ProviderTestResult(success=False, message="The provider did not respond in time")
        )
    except httpx.HTTPError as exc:
        # The URL and the exception text can both carry the key; log neither.
        logger.warning("Provider probe failed: %s", type(exc).__name__)
        return DataResponse(
            data=ProviderTestResult(success=False, message="Could not reach that address")
        )

    return DataResponse(data=_probe_result(response.status_code))
