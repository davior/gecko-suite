"""Wire shapes for the AI registry.

The rule this file exists to enforce: a stored key goes out as a boolean
(`api_key_configured`) and never as a string. Redacting it to "" instead, as gecko-notes
does, is indistinguishable from "no key set".
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

# `protected_namespaces=()` throughout: pydantic reserves `model_` for itself and warns
# about `model_id`, which is the vendor's own name for the field and the clearest one.
_CONFIG = ConfigDict(protected_namespaces=())


# ─── providers ───────────────────────────────────────────────────────────────


class ProviderRead(BaseModel):
    model_config = _CONFIG

    id: str
    scope: str
    name: str
    vendor: str
    base_url: Optional[str] = None
    use_anthropic_api: bool
    extra_params: Optional[dict[str, Any]] = None
    max_tokens: Optional[int] = None
    enabled: bool
    api_key_configured: bool
    # Whether the caller may change it: theirs, or an instance one and they are an admin.
    # Computed per request so the frontend never has to re-derive the rule.
    writable: bool
    model_count: int = 0


class ProviderCreate(BaseModel):
    model_config = _CONFIG

    scope: str = "user"
    name: str = Field(min_length=1, max_length=200)
    vendor: str
    api_key: str = ""
    base_url: Optional[str] = None
    use_anthropic_api: bool = False
    extra_params: Optional[dict[str, Any]] = None
    max_tokens: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    enabled: bool = True


class ProviderUpdate(BaseModel):
    """Every field optional. `scope` and `vendor` are absent on purpose: moving a key
    from one person to everybody, or re-pointing it at another vendor, is a new provider,
    not an edit."""

    model_config = _CONFIG

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    # Three-state: None leaves the stored key alone, "" removes it, anything else
    # replaces it. The browser never has the key to send back, so "unchanged" has to be
    # distinguishable from "remove".
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    use_anthropic_api: Optional[bool] = None
    extra_params: Optional[dict[str, Any]] = None
    # Sent as null to go back to the gateway's default; left out to keep it. Told apart
    # by `model_fields_set`, since both arrive as None.
    max_tokens: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    enabled: Optional[bool] = None


class ProviderTest(BaseModel):
    """A connection probe, for a saved provider or an unsaved form.

    `provider_id` names a saved provider whose stored key and URL are used, so the
    screen can re-test without the key it never received.
    """

    model_config = _CONFIG

    provider_id: Optional[str] = None
    vendor: Optional[str] = None
    api_key: str = ""
    base_url: Optional[str] = None
    use_anthropic_api: bool = False
    model_id: Optional[str] = None


class ProviderTestResult(BaseModel):
    success: bool
    message: str


# ─── models ──────────────────────────────────────────────────────────────────


class ModelRead(BaseModel):
    model_config = _CONFIG

    id: str
    provider_id: str
    provider_name: str
    provider_scope: str
    vendor: str
    model_id: str
    label: str
    capabilities: list[str]
    max_tokens: Optional[int] = None
    context_window: Optional[int] = None
    embed_dims: Optional[int] = None
    voices: Optional[list[Any]] = None
    # The model's own price, as set. `effective_pricing` is what a call is costed at.
    pricing: Optional[dict[str, Any]] = None
    effective_pricing: Optional[dict[str, Any]] = None
    params: Optional[dict[str, Any]] = None
    is_active: bool
    # Active and its provider enabled: what routing will consider.
    usable: bool
    writable: bool


class ModelCreate(BaseModel):
    """A model on a provider. With `from_catalog`, every field left unset is filled
    from the catalogue's row for this vendor and id, so adding a known model is one
    field."""

    model_config = _CONFIG

    model_id: str = Field(min_length=1, max_length=200)
    from_catalog: bool = True
    label: Optional[str] = Field(default=None, max_length=200)
    capabilities: Optional[list[str]] = None
    max_tokens: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    context_window: Optional[int] = Field(default=None, ge=1)
    embed_dims: Optional[int] = Field(default=None, ge=1)
    voices: Optional[list[Any]] = None
    pricing: Optional[dict[str, Any]] = None
    params: Optional[dict[str, Any]] = None
    is_active: bool = True


class ModelUpdate(BaseModel):
    model_config = _CONFIG

    label: Optional[str] = Field(default=None, max_length=200)
    capabilities: Optional[list[str]] = None
    max_tokens: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    context_window: Optional[int] = Field(default=None, ge=1)
    embed_dims: Optional[int] = Field(default=None, ge=1)
    voices: Optional[list[Any]] = None
    # {} clears it, so the catalogue's price applies again.
    pricing: Optional[dict[str, Any]] = None
    params: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None


class CatalogRead(BaseModel):
    model_config = _CONFIG

    vendor: str
    model_id: str
    label: str
    capabilities: list[str]
    max_tokens: Optional[int] = None
    context_window: Optional[int] = None
    embed_dims: Optional[int] = None
    pricing: Optional[dict[str, Any]] = None
    note: str = ""


# ─── routing ─────────────────────────────────────────────────────────────────


class AssignmentRead(BaseModel):
    model_config = _CONFIG

    id: str
    scope: str
    app: Optional[str] = None
    task: str
    model_ref: str
    model_id: str
    model_label: str
    provider_name: str
    params: Optional[dict[str, Any]] = None
    writable: bool


class AssignmentPut(BaseModel):
    """Set the model for (scope, app, task), replacing whatever was there."""

    model_config = _CONFIG

    scope: str = "user"
    app: Optional[str] = None
    task: str
    # The suite's id for the model (AIModel.id), not the vendor's model_id: the same
    # vendor model can be reachable through two providers.
    model_ref: str
    params: Optional[dict[str, Any]] = None


class ResolveRead(BaseModel):
    model_config = _CONFIG

    task: str
    app: Optional[str] = None
    # Null when nothing is routed for this task: the caller has no AI for it.
    model: Optional[ModelRead] = None
    matched_task: Optional[str] = None
    rule: Optional[str] = None
    params: Optional[dict[str, Any]] = None
