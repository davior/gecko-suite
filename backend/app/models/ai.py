"""The AI registry: providers, their models, the catalogue, and which model does which job.

The three sibling apps each have an `AIProvider` row that is one credential *and* one
model, owned by one user. That shape is why the same key gets pasted three times in one
app (once per model) and again in each app. Here the two are split:

- `AIProvider` is a credential and an endpoint, owned by one user or by the instance.
- `AIModel` is a model reachable through it, with what it can do and what it costs.
- `TaskAssignment` says which model a task uses, per user, per app, or for everyone.
- `CatalogEntry` is reference data, seeded by migration: the known models and their
  list prices, offered as templates and used to price a model that has none of its own.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, ForeignKey, String
from sqlmodel import Field, SQLModel

from app.clock import utcnow


def new_id() -> str:
    return str(uuid.uuid4())


class AIProvider(SQLModel, table=True):
    __tablename__ = "ai_provider"

    id: str = Field(default_factory=new_id, primary_key=True)
    # instance | user. An instance provider has no owner and only an admin writes it.
    scope: str = Field(index=True)
    # Null exactly when scope is "instance". Indexed: every list filters on it.
    user_id: Optional[str] = Field(default=None, index=True)

    name: str
    vendor: str  # one of app.vocab.VENDORS
    # Encrypted with app.crypto; "" for a keyless vendor (Ollama) or a key not yet set.
    api_key: str = Field(default="")
    # Only read for openai/custom (SSRF-checked) and ollama (may be private). The fixed
    # vendors ignore it; see app.providers.endpoints.
    base_url: Optional[str] = None
    # Speak the Anthropic Messages protocol to a deepseek or custom endpoint. DeepSeek's
    # Anthropic-compatible endpoint runs a server-side web_search tool its OpenAI one
    # lacks, which is why a vendor flag is not enough.
    use_anthropic_api: bool = Field(default=False)
    # JSON-as-TEXT: extra request parameters merged into every request through this
    # provider, after app.providers.params strips the structural keys.
    extra_params: Optional[str] = None
    enabled: bool = Field(default=True)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AIModel(SQLModel, table=True):
    __tablename__ = "ai_model"

    id: str = Field(default_factory=new_id, primary_key=True)
    # Cascades in the database (foreign_keys=ON, app.database): a model has no meaning
    # without the credential that reaches it. Spelled as a Column because this SQLModel
    # version has no `ondelete` on Field.
    provider_id: str = Field(
        sa_column=Column(
            String, ForeignKey("ai_provider.id", ondelete="CASCADE"), index=True, nullable=False
        )
    )

    # What the vendor calls it: "claude-opus-5-5", "gpt-4.1-mini", "nomic-embed-text".
    # Free text, so a model released this morning can be used this afternoon.
    model_id: str
    label: str = ""
    # JSON-as-TEXT list drawn from app.vocab.CAPABILITIES.
    capabilities: str = "[]"
    # Cap on the response length the suite asks for. Callers may ask for less.
    max_tokens: Optional[int] = None
    context_window: Optional[int] = None
    embed_dims: Optional[int] = None
    # JSON-as-TEXT. Voices a TTS model offers (M3).
    voices: Optional[str] = None
    # JSON-as-TEXT, the same shape as CatalogEntry.pricing. Null means "use the
    # catalogue's price for this model id", which is the usual case; set it only for a
    # negotiated price or a model the catalogue does not know.
    pricing: Optional[str] = None
    # JSON-as-TEXT. Vendor-specific defaults (fal's text_field / voice_field, default
    # image size), merged under whatever the request supplies.
    params: Optional[str] = None
    is_active: bool = Field(default=True)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class TaskAssignment(SQLModel, table=True):
    """Which model a task uses, for one user or for everyone, in one app or in all.

    Uniqueness of (scope, user_id, app, task) is enforced by the router, not by an
    index: in SQLite NULLs are distinct in a unique index, so the instance-wide,
    all-apps row (both NULL) could be inserted any number of times past one.
    """

    __tablename__ = "task_assignment"

    id: str = Field(default_factory=new_id, primary_key=True)
    scope: str = Field(index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    # Null means every app.
    app: Optional[str] = None
    task: str = Field(index=True)
    # Cascades: deleting a model drops the routing that pointed at it, and the task falls
    # back (app.services.registry.resolve) instead of pointing at nothing.
    model_ref: str = Field(
        sa_column=Column(
            String, ForeignKey("ai_model.id", ondelete="CASCADE"), index=True, nullable=False
        )
    )
    # JSON-as-TEXT. Per-task defaults merged into the request, e.g. effort or max_tokens
    # for a draft versus an edit.
    params: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class CatalogEntry(SQLModel, table=True):
    """A known model and its list price. Reference data, written by migrations.

    Not a foreign key target. An `AIModel` matches its entry by (vendor, model_id) at
    the moment it is priced, so updating a price in a later migration reprices every
    model that has no price of its own, and deleting an entry orphans nothing.
    """

    __tablename__ = "catalog_entry"

    id: str = Field(default_factory=new_id, primary_key=True)
    vendor: str = Field(index=True)
    # An exact id, or a family prefix ending in "*" ("claude-opus-*") used when no
    # exact entry matches. The longest matching prefix wins.
    model_id: str
    label: str = ""
    capabilities: str = "[]"
    max_tokens: Optional[int] = None
    context_window: Optional[int] = None
    embed_dims: Optional[int] = None
    # JSON-as-TEXT: {"currency", "input", "output", "cache_read", "cache_write"} per
    # million tokens, or {"per_image"} / {"per_megapixel"} / {"per_second"}. A key that
    # is absent is unknown, which is not the same as free.
    pricing: Optional[str] = None
    # Why this row says what it says: a source, a caveat ("first 100K tokens").
    note: str = ""
    sort_order: int = 0
