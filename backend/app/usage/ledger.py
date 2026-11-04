"""Pricing a call and writing it to the ledger.

Every cost here is an estimate from list prices (`app.services.registry.effective_pricing`),
stored with `cost_estimated=True`. Providers discount and change prices; nothing may show
one of these figures without saying it is an estimate.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session

from app.gateway.types import Target, Usage
from app.models.ai import AIModel, AIProvider
from app.models.usage import UsageEvent
from app.services.registry import catalog_entry_for, effective_pricing, load_json

logger = logging.getLogger(__name__)


def token_cost(pricing: Optional[dict], usage: Usage) -> Optional[tuple[float, str]]:
    """(cost, currency) for a token-priced call, or None if the price is not known.

    Cache reads and writes are priced at their own rate where the price list has one,
    and at the input rate where it does not. That over-states a cached call rather than
    making it look free, which is the safer error for a number people budget with.
    """
    if not pricing or "input" not in pricing or "output" not in pricing:
        return None
    per = 1_000_000.0
    input_price = float(pricing["input"])
    cost = (
        usage.input_tokens * input_price
        + usage.output_tokens * float(pricing["output"])
        + usage.cache_read_tokens * float(pricing.get("cache_read", input_price))
        + usage.cache_write_tokens * float(pricing.get("cache_write", input_price))
    ) / per
    return round(cost, 6), str(pricing.get("currency", "USD"))


def pricing_for_served(session: Session, target: Target, served_model: str) -> Optional[dict]:
    """The price of the model that actually answered.

    Usually the routed model. When a vendor fell back to another model after a refusal,
    the rescue is billed at the fallback model's rates, so it is priced by its own
    catalogue entry; a model's negotiated price applies only to that model.
    """
    model = session.get(AIModel, target.model_ref)
    provider = session.get(AIProvider, target.provider_id)
    if model is None or provider is None:
        return None
    if not served_model or served_model == target.model_id:
        return effective_pricing(session, model, provider)
    entry = catalog_entry_for(session, target.vendor, served_model)
    return (load_json(entry.pricing, {}) or None) if entry else None


def record_chat(
    session: Session,
    *,
    user_id: str,
    app: Optional[str],
    task: Optional[str],
    target: Target,
    usage: Usage,
    served_model: str,
    subject_ref: Optional[str],
    status: str = "ok",
) -> UsageEvent:
    priced = token_cost(pricing_for_served(session, target, served_model), usage)
    event = UsageEvent(
        user_id=user_id,
        app=app,
        task=task,
        kind="ai",
        provider_id=target.provider_id,
        provider_name=target.provider_name,
        vendor=target.vendor,
        model=served_model or target.model_id,
        units=usage.input_tokens + usage.output_tokens + usage.cache_read_tokens + usage.cache_write_tokens,
        unit_type="tokens",
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        cost=priced[0] if priced else None,
        currency=priced[1] if priced else None,
        cost_estimated=True,
        status=status,
        subject_ref=subject_ref,
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def record_raw(
    session: Session,
    *,
    user_id: str,
    app: Optional[str],
    provider: AIProvider,
    model: Optional[AIModel],
    model_id: str,
    usage: Usage,
    subject_ref: Optional[str],
    status: str = "ok",
) -> UsageEvent:
    """The ledger row for a raw-passthrough call, where there may be no model row.

    The passthrough matches by vendor and model name, so the model may not be one this
    suite has been told about. Its price then comes from the catalogue by name, and when
    even that is unknown the cost is None, never 0.
    """
    if model is not None:
        pricing = effective_pricing(session, model, provider)
    else:
        entry = catalog_entry_for(session, provider.vendor, model_id)
        pricing = (load_json(entry.pricing, {}) or None) if entry else None
    priced = token_cost(pricing, usage)
    event = UsageEvent(
        user_id=user_id, app=app, task=None, kind="ai", provider_id=provider.id,
        provider_name=provider.name, vendor=provider.vendor, model=model_id,
        units=usage.input_tokens + usage.output_tokens + usage.cache_read_tokens + usage.cache_write_tokens,
        unit_type="tokens", input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens, cache_write_tokens=usage.cache_write_tokens,
        cost=priced[0] if priced else None, currency=priced[1] if priced else None,
        cost_estimated=True, status=status, subject_ref=subject_ref)
    session.add(event)
    session.commit()
    session.refresh(event)
    return event
