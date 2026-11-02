"""Models reachable through a provider, and the catalogue they are usually added from."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Response, status
from sqlmodel import Session, select

from app.auth import CurrentUser
from app.clock import utcnow
from app.database import get_session
from app.models.ai import AIModel, AIProvider, CatalogEntry
from app.schemas import DataResponse, ListResponse
from app.schemas_ai import CatalogRead, ModelCreate, ModelRead, ModelUpdate
from app.services import access
from app.services.registry import (
    capabilities_of,
    catalog_entry_for,
    dump_json,
    effective_pricing,
    load_json,
    usable,
    visible_model,
    visible_provider,
    visible_providers_query,
)
from app.vocab import CAPABILITIES

router = APIRouter()


def serialise_model(
    session: Session, model: AIModel, provider: AIProvider, writable: bool
) -> ModelRead:
    return ModelRead(
        id=model.id,
        provider_id=provider.id,
        provider_name=provider.name,
        provider_scope=provider.scope,
        vendor=provider.vendor,
        model_id=model.model_id,
        label=model.label or model.model_id,
        capabilities=sorted(capabilities_of(model)),
        max_tokens=model.max_tokens,
        context_window=model.context_window,
        embed_dims=model.embed_dims,
        voices=load_json(model.voices, []) or None,
        pricing=load_json(model.pricing, {}) or None,
        effective_pricing=effective_pricing(session, model, provider),
        params=load_json(model.params, {}) or None,
        is_active=model.is_active,
        usable=usable(model, provider),
        writable=writable,
    )


def _check_capabilities(capabilities: Optional[list[str]]) -> None:
    unknown = sorted(set(capabilities or []) - set(CAPABILITIES))
    if unknown:
        raise access.bad_request("bad_capability", f"Unknown capability: {', '.join(unknown)}")


def _writable_provider(session: Session, provider_id: str, user) -> AIProvider:
    provider = visible_provider(session, provider_id, user.id)
    if provider is None:
        raise access.not_found("provider")
    if not access.can_write(
        provider.scope, provider.user_id, user, access.caller_is_admin(session, user)
    ):
        raise access.admin_required()
    return provider


@router.get("/models", response_model=ListResponse[ModelRead])
def list_models(
    user: CurrentUser,
    capability: Optional[str] = None,
    usable_only: bool = False,
    session: Session = Depends(get_session),
) -> ListResponse[ModelRead]:
    """Every model the caller can use, across their providers and the shared ones.

    What an app shows in a "which model?" picker: `capability=vision` for a describe
    job, `capability=embed` for search.
    """
    admin = access.caller_is_admin(session, user)
    providers = {p.id: p for p in session.exec(visible_providers_query(user.id)).all()}
    models = session.exec(
        select(AIModel).where(AIModel.provider_id.in_(list(providers))).order_by(AIModel.created_at)  # type: ignore[union-attr]
    ).all() if providers else []

    data = []
    for model in models:
        provider = providers[model.provider_id]
        if capability and capability not in capabilities_of(model):
            continue
        if usable_only and not usable(model, provider):
            continue
        writable = access.can_write(provider.scope, provider.user_id, user, admin)
        data.append(serialise_model(session, model, provider, writable))
    return ListResponse(data=data, total=len(data), limit=len(data), offset=0)


@router.get("/providers/{provider_id}/models", response_model=ListResponse[ModelRead])
def list_provider_models(
    provider_id: str, user: CurrentUser, session: Session = Depends(get_session)
) -> ListResponse[ModelRead]:
    provider = visible_provider(session, provider_id, user.id)
    if provider is None:
        raise access.not_found("provider")
    writable = access.can_write(
        provider.scope, provider.user_id, user, access.caller_is_admin(session, user)
    )
    models = session.exec(
        select(AIModel).where(AIModel.provider_id == provider.id).order_by(AIModel.created_at)
    ).all()
    data = [serialise_model(session, m, provider, writable) for m in models]
    return ListResponse(data=data, total=len(data), limit=len(data), offset=0)


@router.post(
    "/providers/{provider_id}/models", response_model=DataResponse[ModelRead], status_code=201
)
def create_model(
    provider_id: str,
    payload: ModelCreate,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[ModelRead]:
    provider = _writable_provider(session, provider_id, user)
    _check_capabilities(payload.capabilities)

    entry = catalog_entry_for(session, provider.vendor, payload.model_id) if payload.from_catalog else None
    # A family row ("claude-opus-*") is a price, not a description of this model, so
    # only an exact match fills in label, limits and capabilities. Its price still
    # applies at costing time through effective_pricing.
    exact = entry if entry is not None and not entry.model_id.endswith("*") else None

    model = AIModel(
        provider_id=provider.id,
        model_id=payload.model_id,
        label=payload.label if payload.label is not None else (exact.label if exact else ""),
        capabilities=(
            (dump_json(sorted(payload.capabilities)) or "[]")
            if payload.capabilities is not None
            else (exact.capabilities if exact else "[]")
        ),
        max_tokens=payload.max_tokens or (exact.max_tokens if exact else None),
        context_window=payload.context_window or (exact.context_window if exact else None),
        embed_dims=payload.embed_dims or (exact.embed_dims if exact else None),
        voices=dump_json(payload.voices),
        # Deliberately not copied from the catalogue: a copied price would go stale the
        # first time a migration updates the catalogue. Null means "use the catalogue's".
        pricing=dump_json(payload.pricing),
        params=dump_json(payload.params),
        is_active=payload.is_active,
    )
    session.add(model)
    session.commit()
    session.refresh(model)
    return DataResponse(data=serialise_model(session, model, provider, True))


def _writable_model(session: Session, model_id: str, user) -> tuple[AIModel, AIProvider]:
    model = visible_model(session, model_id, user.id)
    if model is None:
        raise access.not_found("model")
    provider = _writable_provider(session, model.provider_id, user)
    return model, provider


@router.patch("/models/{model_id}", response_model=DataResponse[ModelRead])
def update_model(
    model_id: str, payload: ModelUpdate, user: CurrentUser, session: Session = Depends(get_session)
) -> DataResponse[ModelRead]:
    model, provider = _writable_model(session, model_id, user)
    _check_capabilities(payload.capabilities)

    if payload.label is not None:
        model.label = payload.label
    if payload.capabilities is not None:
        model.capabilities = dump_json(sorted(payload.capabilities)) or "[]"
    for field in ("max_tokens", "context_window", "embed_dims", "is_active"):
        value = getattr(payload, field)
        if value is not None:
            setattr(model, field, value)
    for field in ("voices", "pricing", "params"):
        value = getattr(payload, field)
        if value is not None:
            # {} / [] clear it: for pricing that means "the catalogue's price again".
            setattr(model, field, dump_json(value))

    model.updated_at = utcnow()
    session.add(model)
    session.commit()
    session.refresh(model)
    return DataResponse(data=serialise_model(session, model, provider, True))


@router.delete(
    "/models/{model_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
)
def delete_model(model_id: str, user: CurrentUser, session: Session = Depends(get_session)) -> None:
    model, _ = _writable_model(session, model_id, user)
    session.delete(model)
    session.commit()


@router.get("/catalog", response_model=ListResponse[CatalogRead])
def list_catalog(
    user: CurrentUser, vendor: Optional[str] = None, session: Session = Depends(get_session)
) -> ListResponse[CatalogRead]:
    """The known models and list prices, for the "add a model" picker.

    Family rows ("claude-opus-*") are pricing fallbacks, not models anyone can add, so
    they are left out.
    """
    query = select(CatalogEntry).order_by(CatalogEntry.vendor, CatalogEntry.sort_order)
    if vendor:
        query = query.where(CatalogEntry.vendor == vendor)
    entries = [e for e in session.exec(query).all() if not e.model_id.endswith("*")]
    data = [
        CatalogRead(
            vendor=e.vendor,
            model_id=e.model_id,
            label=e.label,
            capabilities=sorted(capabilities_of(e)),
            max_tokens=e.max_tokens,
            context_window=e.context_window,
            embed_dims=e.embed_dims,
            pricing=load_json(e.pricing, {}) or None,
            note=e.note,
        )
        for e in entries
    ]
    return ListResponse(data=data, total=len(data), limit=len(data), offset=0)
